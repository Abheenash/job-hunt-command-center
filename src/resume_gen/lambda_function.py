"""resume-gen Lambda — JD -> tailored résumé (LaTeX + best-effort PDF), Opus-only.

Routes (all behind the Cognito JWT authorizer):
  POST /generate-resume        -> start an async job, returns {jobId}
  GET  /generate-resume?job=.. -> poll job status / result
  GET  /profile-skills         -> list candidate-confirmed extra skills
  POST /profile-skills {skill} -> add one (from an ATS "I have this" click)

Async because Opus full-rewrite + a server-side LaTeX compile exceed API Gateway's
30s cap. The worker runs two phases so the browser sees results fast:
  1. Opus rewrites -> render LaTeX -> write result.json (status=ready, pdfStatus=compiling)
  2. tectonic compiles the PDF with length auto-fit (trim to <=2 pages) -> update result.json

Honesty guardrail: the model rewrites/reorders but only from the corpus (profile.py)
plus skills the user explicitly confirmed they have. It never fabricates.
"""
import json
import os
import re
import shutil
import subprocess
import uuid

import boto3
from botocore.exceptions import ClientError

import profile as P
import templates as T

bedrock = boto3.client("bedrock-runtime")
s3 = boto3.client("s3")
lam = boto3.client("lambda")

DOCS_BUCKET = os.environ["DOCS_BUCKET"]
SELF = os.environ["SELF_FUNCTION_NAME"]
EXTRA_SKILLS_KEY = "profile/extra-skills.json"
TECTONIC = "/opt/bin/tectonic"
BADGE_PNG = "aws-certified-solutions-architect-associate.png"

# Primary = latest Opus; auto-fall-back to 4.5 if 4.8 access hasn't propagated yet,
# so generation never breaks and upgrades itself the moment 4.8 is granted.
MODEL = "us.anthropic.claude-opus-4-8"
MODEL_FALLBACK = "us.anthropic.claude-opus-4-5-20251101-v1:0"


def _invoke_opus(payload):
    """Try Opus 4.8, fall back to 4.5 only on an access-not-granted error."""
    body = json.dumps(payload)
    for model_id, tag in ((MODEL, "opus-4.8"), (MODEL_FALLBACK, "opus-4.5")):
        try:
            resp = bedrock.invoke_model(modelId=model_id, body=body)
            return json.loads(resp["body"].read())["content"][0]["text"], tag
        except ClientError as e:
            if e.response.get("Error", {}).get("Code") == "AccessDeniedException" and model_id != MODEL_FALLBACK:
                print(f"{model_id} not accessible yet — falling back to {MODEL_FALLBACK}")
                continue
            raise
    raise RuntimeError("no Opus model available")

SYSTEM = (
    "You are an expert technical résumé writer tailoring ONE candidate's résumé to ONE "
    "job description. You are given the candidate's complete, real corpus. Rewrite and "
    "reorder the content so it maximally fits the JD, then score the fit and suggest tags.\n\n"
    "WHAT YOU MAY DO:\n"
    "- Rewrite the summary, the experience bullets, each project's bullets, and the skills "
    "lines to emphasize what THIS JD cares about. Reorder skills categories and the items "
    "within them most-relevant-first. Pick and order the 4 best projects.\n"
    "- Surface relevant skills/tools the candidate genuinely has (present anywhere in the "
    "corpus) even if buried; lead bullets with JD-relevant results.\n"
    "- Bold up to a few key phrases per bullet with **double asterisks**.\n\n"
    "LENGTH: this must fit TWO pages. Keep ~3 bullets on the top 2 projects and 2 on the "
    "others; keep experience to 4-5 tight bullets; no bullet over ~2 lines.\n\n"
    "HARD RULES (honesty):\n"
    "- Use ONLY facts, skills, tools, employers, dates, and metrics in the corpus (including "
    "the candidate-confirmed extra skills, if any). NEVER invent or inflate. If the JD wants "
    "something the candidate lacks, put it in 'gaps' — do not write it into the résumé.\n"
    "- Keep every metric truthful (e.g. '5.2x', '~7 seconds', '6m36s RTO').\n"
    "- Select EXACTLY 4 projects by their corpus id.\n"
    "- Output PLAIN TEXT only (no LaTeX, no markdown except **bold**).\n"
    "- 'customFields' = 2-6 useful application tags parsed from the JD (e.g. "
    '{"key":"Clearance","value":"TS/SCI"}). Only what the JD states; [] if none.\n'
    "SCORING (be strict and evidence-based, not generous):\n"
    "- 'scoreBreakdown': score each of these EXACT dimensions 0-100 vs the JD using only "
    "corpus evidence — 'Required skills' (JD must-haves the résumé clearly evidences), "
    "'Preferred skills' (nice-to-haves), 'Experience & seniority' (years/level/scope fit), "
    "'Domain relevance' (role/industry focus fit), 'ATS keywords' (share of the JD's key hard "
    "terms present). One short 'note' each. The overall match % is computed from these by "
    "fixed weights server-side — you only supply the sub-scores, so be honest.\n"
    "- 'atsCovered'/'atsMissing': the JD's important hard keywords/skills that ARE / are NOT "
    "present in the résumé (this drives the ATS keyword-match rate).\n"
    "- Reply with ONLY one compact JSON object, no prose, matching this schema:\n"
    '{"summary":str,"skills":[{"category":str,"items":str}],"experienceBullets":[str],'
    '"projects":[{"id":str,"name":str,"tech":str,"bullets":[str]}],"rationale":str,'
    '"scoreBreakdown":[{"dimension":str,"score":int,"note":str}],'
    '"matched":[str],"gaps":[str],"atsCovered":[str],"atsMissing":[str],'
    '"customFields":[{"key":str,"value":str}],"coverLetter":[str]}\n'
    "'coverLetter' = 3-4 plain-text paragraphs ONLY if asked; otherwise []."
)

# Fixed weights for the match rubric — the model scores each dimension, we compute
# the weighted total here so the number is defined and reproducible, not a vibe.
MATCH_WEIGHTS = [
    ("Required skills", 40), ("Preferred skills", 15), ("Experience & seniority", 20),
    ("Domain relevance", 15), ("ATS keywords", 10),
]

_LATEX = [(r"\\textbf\{", ""), (r"\}", ""), (r"\$\\rightarrow\$", "->"), (r"\$\\leftrightarrow\$", "<->"),
          (r"\$\\sim\$", "~"), (r"\$\\times\$", "x"), (r"\\&", "&"), (r"\\%", "%"), (r"\\\$", "$")]


def _plain(s):
    for pat, rep in _LATEX:
        s = re.sub(pat, rep, s)
    return s


# ---------- routing -----------------------------------------------------------
def handler(event, ctx):
    if isinstance(event, dict) and event.get("_job"):
        return _run_job(event["_job"], event.get("params") or {}, ctx)
    http = (event.get("requestContext", {}).get("http", {}) or {})
    method, path = http.get("method", "POST"), http.get("path", "")
    if path.endswith("/profile-skills"):
        return _list_skills() if method == "GET" else _add_skill(event)
    if method == "GET":
        return _get_status(event)
    return _start_job(event)


# ---------- candidate-confirmed extra skills (ATS "I have this") --------------
def _load_extras():
    try:
        obj = s3.get_object(Bucket=DOCS_BUCKET, Key=EXTRA_SKILLS_KEY)
        return [s for s in json.loads(obj["Body"].read()).get("skills", []) if s]
    except Exception:  # noqa: BLE001 — none yet
        return []


def _list_skills():
    return _resp(200, {"skills": _load_extras()})


def _add_skill(event):
    try:
        skill = str(json.loads(event.get("body") or "{}").get("skill", "")).strip()
    except Exception:  # noqa: BLE001
        return _resp(400, {"error": "bad body"})
    if not (2 <= len(skill) <= 60):
        return _resp(400, {"error": "skill must be 2-60 chars"})
    skills = _load_extras()
    if skill.lower() not in [x.lower() for x in skills]:
        skills.append(skill)
        s3.put_object(Bucket=DOCS_BUCKET, Key=EXTRA_SKILLS_KEY,
                      Body=json.dumps({"skills": skills}).encode("utf-8"), ContentType="application/json")
    return _resp(200, {"skills": skills})


# ---------- job lifecycle -----------------------------------------------------
def _start_job(event):
    try:
        body = json.loads(event.get("body") or "{}")
    except Exception:  # noqa: BLE001
        return _resp(400, {"error": "invalid JSON body"})
    jd = (body.get("jd") or "").strip()
    if len(jd) < 40:
        return _resp(400, {"error": "Paste a fuller job description (at least a few lines)."})
    job = uuid.uuid4().hex
    params = {"jd": jd, "coverLetter": bool(body.get("coverLetter")),
              "company": (body.get("company") or "").strip(), "role": (body.get("role") or "").strip(),
              "template": body.get("template") if body.get("template") in T.TEMPLATES else "standard"}
    lam.invoke(FunctionName=SELF, InvocationType="Event",
               Payload=json.dumps({"_job": job, "params": params}).encode("utf-8"))
    return _resp(202, {"jobId": job, "status": "pending"})


def _get_status(event):
    job = (event.get("queryStringParameters") or {}).get("job", "")
    if not re.fullmatch(r"[0-9a-f]{32}", job or ""):
        return _resp(400, {"error": "bad job id"})
    try:
        obj = s3.get_object(Bucket=DOCS_BUCKET, Key=f"generated/{job}/result.json")
        return _resp(200, json.loads(obj["Body"].read()))
    except s3.exceptions.NoSuchKey:
        return _resp(200, {"status": "pending"})
    except Exception as e:  # noqa: BLE001
        print(f"status read failed: {type(e).__name__}: {e}")
        return _resp(200, {"status": "pending"})


def _corpus_text(extras):
    lines = ["=== SKILLS (categories the candidate has — reorder/rewrite items, never add new ones) ==="]
    for k, v in P.SKILLS.items():
        lines.append(f"* {_plain(k)}: {_plain(v)}")
    if extras:
        lines.append("* Candidate-confirmed additional skills (use if JD-relevant): " + ", ".join(extras))
    lines.append("\n=== EXPERIENCE — HCLTech, DevOps Engineer (rewrite these bullets) ===")
    for b in P.EXPERIENCE[0]["bullets"]:
        lines.append(f"  - {_plain(b)}")
    lines.append("\n=== BASE SUMMARY (rewrite for the JD) ===")
    lines.append("  " + _plain(P.SUMMARY_BASE))
    lines.append("\n=== PROJECTS (pick 4 by id; rewrite their bullets; keep name/tech accurate) ===")
    for p in P.PROJECTS:
        lines.append(f"* id={p['id']} | rank={p['rank']} | domains={','.join(p['domains'])}")
        lines.append(f"    name: {_plain(p['name'])}")
        lines.append(f"    tech: {_plain(p['tech'])}")
        for b in p["bullets"]:
            lines.append(f"    - {_plain(b)}")
    return "\n".join(lines)


def _run_job(job, params, ctx):
    jd = params.get("jd", "")
    want_cover = bool(params.get("coverLetter"))
    company = (params.get("company") or "").strip()
    role = (params.get("role") or "").strip()
    template = params.get("template") if params.get("template") in T.TEMPLATES else "standard"
    extras = _load_extras()

    user = (
        f"{_corpus_text(extras)}\n\n=== JOB DESCRIPTION ===\n{jd[:6000]}\n\n"
        f"Cover letter requested: {'YES — 3-4 paragraphs' if want_cover else 'NO — return []'}."
        + (f"\nTarget company: {company}" if company else "")
        + (f"\nTarget role: {role}" if role else "")
    )
    payload = {"anthropic_version": "bedrock-2023-05-31", "max_tokens": 4096, "temperature": 0.3,
               "system": SYSTEM, "messages": [{"role": "user", "content": [{"type": "text", "text": user}]}]}
    try:
        text, model_tag = _invoke_opus(payload)
        sel = json.loads(_first_json(text))
    except Exception as e:  # noqa: BLE001
        print(f"generate failed: {type(e).__name__}: {e}")
        _write_result(job, {"status": "error", "error": "The model could not generate a résumé. Try again."})
        return {"ok": False}

    resume_tex = T.render_resume(sel, template)
    cover_tex = T.render_cover_letter(company, role, sel["coverLetter"]) if (want_cover and sel.get("coverLetter")) else None
    selected = [{"id": s.get("id"), "name": _plain((P.project_by_id(s.get("id")) or {}).get("name", s.get("id", "")))}
                for s in (sel.get("projects") or [])]
    custom = [{"key": (c.get("key") or "").strip(), "value": (c.get("value") or "").strip()}
              for c in (sel.get("customFields") or []) if (c.get("key") or "").strip()]

    # Weighted match rubric (computed here, not taken from the model) + ATS keyword rate.
    by_dim = {str(d.get("dimension", "")).strip().lower(): d for d in (sel.get("scoreBreakdown") or [])}
    breakdown, wsum, tot = [], 0, 0
    for dim, w in MATCH_WEIGHTS:
        sc = max(0, min(100, int((by_dim.get(dim.lower()) or {}).get("score", 0) or 0)))
        breakdown.append({"dimension": dim, "weight": w, "score": sc,
                          "note": str((by_dim.get(dim.lower()) or {}).get("note", ""))[:120]})
        wsum += sc * w
        tot += w
    match_percent = round(wsum / tot) if tot else 0
    cov, miss = sel.get("atsCovered", []), sel.get("atsMissing", [])
    ats_score = round(100 * len(cov) / max(1, len(cov) + len(miss)))

    result = {
        "status": "ready", "model": model_tag, "pdfStatus": "compiling", "jobId": job,
        "resumeLatex": resume_tex, "coverLetterLatex": cover_tex,
        "matchPercent": match_percent, "scoreBreakdown": breakdown,
        "atsScore": ats_score, "matched": sel.get("matched", []),
        "gaps": sel.get("gaps", []), "atsCovered": cov, "atsMissing": miss,
        "rationale": sel.get("rationale", ""), "selectedProjects": selected,
        "customFields": custom, "snapshotKey": f"generated/{job}/resume.tex",
    }
    _snapshot(job, "resume.tex", resume_tex)
    if cover_tex:
        _snapshot(job, "cover-letter.tex", cover_tex)
    _write_result(job, result)   # phase 1: LaTeX ready fast

    # phase 2: best-effort server-side PDF with length auto-fit (never fatal)
    try:
        pdf, pages, final_tex = _compile_autofit(sel, template)
        if pdf:
            s3.put_object(Bucket=DOCS_BUCKET, Key=f"generated/{job}/resume.pdf", Body=pdf, ContentType="application/pdf")
            result["pdfUrl"] = _presign(f"generated/{job}/resume.pdf")
            result["pdfKey"] = f"generated/{job}/resume.pdf"
            result["pages"] = pages
            if final_tex != resume_tex:               # auto-fit trimmed it
                result["resumeLatex"] = final_tex
                _snapshot(job, "resume.tex", final_tex)
            if cover_tex:
                cpdf, _cp, _ct = _compile(cover_tex, "cover")
                if cpdf:
                    s3.put_object(Bucket=DOCS_BUCKET, Key=f"generated/{job}/cover-letter.pdf", Body=cpdf, ContentType="application/pdf")
                    result["coverPdfUrl"] = _presign(f"generated/{job}/cover-letter.pdf")
            result["pdfStatus"] = "ready"
        else:
            result["pdfStatus"] = "error"
    except Exception as e:  # noqa: BLE001
        print(f"pdf compile failed (non-fatal): {type(e).__name__}: {e}")
        result["pdfStatus"] = "error"
    _write_result(job, result)   # phase 2: PDF url (or error)
    return {"ok": True}


# ---------- LaTeX compile + length auto-fit -----------------------------------
def _seed_cache():
    """Copy the bundled tectonic package cache (in the layer, read-only /opt) into
    writable /tmp on cold start, so compiles skip the slow network bundle fetch.
    Best-effort: if it's missing, tectonic just fetches from the network as before."""
    dst = "/tmp/tct-cache"
    src = "/opt/cache"
    if not os.path.isdir(dst) and os.path.isdir(src):
        try:
            shutil.copytree(src, dst)
            print("seeded tectonic cache from layer")
        except Exception as e:  # noqa: BLE001
            print(f"cache seed skipped: {type(e).__name__}: {e}")


def _compile(tex, name):
    """Compile one .tex with tectonic. Returns (pdf_bytes|None, pages|None, tex)."""
    _seed_cache()
    work = "/tmp/tex"
    os.makedirs(work, exist_ok=True)
    src = os.path.join(os.path.dirname(__file__), BADGE_PNG)
    if os.path.exists(src):
        shutil.copy(src, os.path.join(work, BADGE_PNG))
    texpath = os.path.join(work, f"{name}.tex")
    with open(texpath, "w") as fh:
        fh.write(tex)
    env = dict(os.environ, HOME="/tmp", TECTONIC_CACHE_DIR="/tmp/tct-cache")
    r = subprocess.run([TECTONIC, texpath, "--outdir", work, "--keep-logs", "--chatter", "minimal"],
                       cwd=work, env=env, capture_output=True, text=True, timeout=180)
    pdfpath = os.path.join(work, f"{name}.pdf")
    if r.returncode != 0 or not os.path.exists(pdfpath):
        print(f"tectonic rc={r.returncode}: {r.stderr[-400:]}")
        return None, None, tex
    with open(pdfpath, "rb") as fh:
        pdf = fh.read()
    return pdf, _pages(os.path.join(work, f"{name}.log"), pdf), tex


def _pages(logpath, pdf):
    try:
        with open(logpath, encoding="utf-8", errors="ignore") as fh:
            m = re.search(r"\((\d+)\s+pages?", fh.read())
            if m:
                return int(m.group(1))
    except Exception:  # noqa: BLE001
        pass
    return len(re.findall(rb"/Type\s*/Page[^s]", pdf)) or None  # fallback


def _compile_autofit(sel, template="standard"):
    """Compile; if >2 pages, trim the least-important bullet and recompile (<=5x)."""
    tex = T.render_resume(sel, template)
    pdf, pages, _ = _compile(tex, "resume")
    tries = 0
    while pdf and pages and pages > 2 and tries < 5 and _trim(sel):
        tex = T.render_resume(sel, template)
        pdf, pages, _ = _compile(tex, "resume")
        tries += 1
    return pdf, pages, tex


def _trim(sel):
    """Drop one lowest-impact bullet (last project first, then experience). True if trimmed."""
    for pr in reversed(sel.get("projects") or []):
        if len(pr.get("bullets") or []) > 1:
            pr["bullets"].pop()
            return True
    eb = sel.get("experienceBullets") or []
    if len(eb) > 3:
        eb.pop()
        return True
    return False


# ---------- helpers -----------------------------------------------------------
def _snapshot(job, name, tex):
    s3.put_object(Bucket=DOCS_BUCKET, Key=f"generated/{job}/{name}", Body=tex.encode("utf-8"),
                  ContentType="application/x-tex")


def _presign(key):
    return s3.generate_presigned_url("get_object", Params={"Bucket": DOCS_BUCKET, "Key": key}, ExpiresIn=3600)


def _write_result(job, obj):
    s3.put_object(Bucket=DOCS_BUCKET, Key=f"generated/{job}/result.json",
                  Body=json.dumps(obj).encode("utf-8"), ContentType="application/json")


def _first_json(text):
    s, e = text.find("{"), text.rfind("}")
    return text[s:e + 1] if s != -1 and e != -1 else "{}"


def _resp(code, obj):
    return {"statusCode": code, "headers": {"content-type": "application/json"}, "body": json.dumps(obj)}
