"""resume-gen Lambda — POST /generate-resume.

Given a pasted JD, Claude Opus rewrites the candidate's résumé to fit it: it picks
the 4 best projects, rewrites the summary / skills / experience / project bullets to
emphasize JD-relevant angles, reorders skills, scores the fit, and suggests custom
fields to tag the application with. The Lambda renders the rewritten plain text into
compilable LaTeX (résumé + optional cover letter), snapshots it to versioned S3, and
returns everything for the portal.

Honesty guardrail (enforced in the prompt): the model may rewrite and reorder freely
but ONLY using facts, skills, tools, and metrics already in the corpus (profile.py).
It never invents. Anything the JD wants that the candidate lacks goes to 'gaps'.
"""
import json
import os
import re
import uuid

import boto3

import profile as P
import templates as T

bedrock = boto3.client("bedrock-runtime")
s3 = boto3.client("s3")
lam = boto3.client("lambda")

DOCS_BUCKET = os.environ["DOCS_BUCKET"]
SELF = os.environ["SELF_FUNCTION_NAME"]  # for async self-invoke (Opus > API GW 30s cap)

# Opus only (latest the account can invoke). No other models by request.
MODEL = "us.anthropic.claude-opus-4-5-20251101-v1:0"

SYSTEM = (
    "You are an expert technical résumé writer tailoring ONE candidate's résumé to ONE "
    "job description. You are given the candidate's complete, real corpus. Rewrite and "
    "reorder the content so it maximally fits the JD, then score the fit and suggest tags.\n\n"
    "WHAT YOU MAY DO:\n"
    "- Rewrite the summary, the experience bullets, each project's bullets, and the skills "
    "lines to emphasize what THIS JD cares about. Reorder skills categories and the items "
    "within them most-relevant-first. Pick and order the 4 best projects.\n"
    "- Surface relevant skills/tools the candidate genuinely has (present anywhere in the "
    "corpus) even if they were buried; lead bullets with JD-relevant results.\n"
    "- You may bold up to a few key phrases per bullet with **double asterisks**.\n\n"
    "HARD RULES (honesty):\n"
    "- Use ONLY facts, skills, tools, employers, dates, and metrics that appear in the corpus. "
    "NEVER invent, inflate, or add a skill/tool/number that isn't there. If the JD wants "
    "something the candidate lacks, put it in 'gaps' — do not write it into the résumé.\n"
    "- Keep every metric truthful to the corpus (e.g. '5.2x', '~7 seconds', '6m36s RTO').\n"
    "- Select EXACTLY 4 projects by their corpus id. Keep bullets concise (résumé length).\n"
    "- Output PLAIN TEXT only (no LaTeX, no markdown except **bold**).\n"
    "- 'customFields' = 2-6 useful application tags parsed from the JD (e.g. "
    '{"key":"Clearance","value":"TS/SCI"}, {"key":"Visa","value":"Sponsors H-1B"}, '
    '{"key":"Team","value":"Platform"}, {"key":"Comp","value":"$150k-$180k"}). Only what the '
    "JD actually states; [] if none.\n"
    "- Reply with ONLY one compact JSON object, no prose, matching this schema:\n"
    '{"summary":str,"skills":[{"category":str,"items":str}],"experienceBullets":[str],'
    '"projects":[{"id":str,"name":str,"tech":str,"bullets":[str]}],"rationale":str,'
    '"matchPercent":int,"matched":[str],"gaps":[str],"atsCovered":[str],"atsMissing":[str],'
    '"customFields":[{"key":str,"value":str}],"coverLetter":[str]}\n'
    "'coverLetter' = 3-4 plain-text paragraphs ONLY if asked; otherwise []."
)

_LATEX = [(r"\\textbf\{", ""), (r"\}", ""), (r"\$\\rightarrow\$", "->"), (r"\$\\leftrightarrow\$", "<->"),
          (r"\$\\sim\$", "~"), (r"\$\\times\$", "x"), (r"\\&", "&"), (r"\\%", "%"), (r"\\\$", "$")]


def _plain(s):
    for pat, rep in _LATEX:
        s = re.sub(pat, rep, s)
    return s


def _corpus_text():
    lines = ["=== SKILLS (categories the candidate has — reorder/rewrite items, never add new ones) ==="]
    for k, v in P.SKILLS.items():
        lines.append(f"* {_plain(k)}: {_plain(v)}")
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


def handler(event, ctx):
    # Async worker path: self-invoked with a raw job payload (Opus can exceed the
    # API Gateway 30s cap, so the request returns a jobId and the browser polls).
    if isinstance(event, dict) and event.get("_job"):
        return _run_job(event["_job"], event.get("params") or {}, ctx)

    method = (event.get("requestContext", {}).get("http", {}) or {}).get("method", "POST")
    if method == "GET":
        return _get_status(event)
    return _start_job(event)


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
              "company": (body.get("company") or "").strip(), "role": (body.get("role") or "").strip()}
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


def _run_job(job, params, ctx):
    """Async worker: run Opus, render, and write result.json for the poller."""
    jd = params.get("jd", "")
    want_cover = bool(params.get("coverLetter"))
    company = (params.get("company") or "").strip()
    role = (params.get("role") or "").strip()

    user = (
        f"{_corpus_text()}\n\n=== JOB DESCRIPTION ===\n{jd[:6000]}\n\n"
        f"Cover letter requested: {'YES — 3-4 paragraphs' if want_cover else 'NO — return []'}."
        + (f"\nTarget company: {company}" if company else "")
        + (f"\nTarget role: {role}" if role else "")
    )
    payload = {
        "anthropic_version": "bedrock-2023-05-31", "max_tokens": 4096, "temperature": 0.3,
        "system": SYSTEM,
        "messages": [{"role": "user", "content": [{"type": "text", "text": user}]}],
    }
    try:
        resp = bedrock.invoke_model(modelId=MODEL, body=json.dumps(payload))
        text = json.loads(resp["body"].read())["content"][0]["text"]
        sel = json.loads(_first_json(text))
    except Exception as e:  # noqa: BLE001
        print(f"generate failed: {type(e).__name__}: {e}")
        _write_result(job, {"status": "error", "error": "The model could not generate a résumé. Try again."})
        return {"ok": False}

    resume_tex = T.render_resume(sel)
    cover_tex = T.render_cover_letter(company, role, sel["coverLetter"]) if (want_cover and sel.get("coverLetter")) else None

    key = f"generated/{job}/resume.tex"
    try:
        s3.put_object(Bucket=DOCS_BUCKET, Key=key, Body=resume_tex.encode("utf-8"), ContentType="application/x-tex")
        if cover_tex:
            s3.put_object(Bucket=DOCS_BUCKET, Key=f"generated/{job}/cover-letter.tex",
                          Body=cover_tex.encode("utf-8"), ContentType="application/x-tex")
    except Exception as e:  # noqa: BLE001 — snapshot is best-effort
        print(f"snapshot failed (non-fatal): {type(e).__name__}: {e}")

    selected = [{"id": s.get("id"), "name": _plain((P.project_by_id(s.get("id")) or {}).get("name", s.get("id", "")))}
                for s in (sel.get("projects") or [])]
    custom = [{"key": (c.get("key") or "").strip(), "value": (c.get("value") or "").strip()}
              for c in (sel.get("customFields") or []) if (c.get("key") or "").strip()]
    _write_result(job, {
        "status": "ready", "model": "opus",
        "resumeLatex": resume_tex, "coverLetterLatex": cover_tex,
        "matchPercent": sel.get("matchPercent"), "matched": sel.get("matched", []),
        "gaps": sel.get("gaps", []), "atsCovered": sel.get("atsCovered", []),
        "atsMissing": sel.get("atsMissing", []), "rationale": sel.get("rationale", ""),
        "selectedProjects": selected, "customFields": custom, "snapshotKey": key,
    })
    return {"ok": True}


def _write_result(job, obj):
    s3.put_object(Bucket=DOCS_BUCKET, Key=f"generated/{job}/result.json",
                  Body=json.dumps(obj).encode("utf-8"), ContentType="application/json")


def _first_json(text):
    s, e = text.find("{"), text.rfind("}")
    return text[s:e + 1] if s != -1 and e != -1 else "{}"


def _resp(code, obj):
    return {"statusCode": code, "headers": {"content-type": "application/json"}, "body": json.dumps(obj)}
