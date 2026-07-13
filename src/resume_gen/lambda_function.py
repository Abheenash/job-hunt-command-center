"""resume-gen Lambda — POST /generate-resume.

Given a pasted job description, a strong Claude model (Sonnet by default, Opus on a
per-run toggle) selects the 4 most relevant projects, tailors the summary/skills
ordering to the JD, and scores the fit — returning a strict JSON *selection*. The
Lambda renders that into compilable LaTeX (résumé + optional cover letter), snapshots
it to the versioned S3 docs bucket, and returns everything for the portal to show.

Honesty guardrail: the model may only select/reorder/rephrase facts already in the
corpus (profile.py). It never writes raw LaTeX and never invents experience.
"""
import json
import os

import boto3

import profile as P
import templates as T

bedrock = boto3.client("bedrock-runtime")
s3 = boto3.client("s3")

DOCS_BUCKET = os.environ["DOCS_BUCKET"]

MODELS = {
    "sonnet": "us.anthropic.claude-sonnet-4-6",
    "opus": "us.anthropic.claude-opus-4-5-20251101-v1:0",
}
DEFAULT_MODEL = "sonnet"

SYSTEM = (
    "You are an expert technical résumé strategist tailoring ONE candidate's résumé to ONE "
    "job description. You are given the candidate's complete, real corpus (projects with "
    "indexed bullets, skill categories, a base summary). Your job: pick and order the content "
    "that best fits the JD, and score the fit.\n\n"
    "HARD RULES:\n"
    "- Use ONLY facts present in the corpus. Never invent skills, tools, employers, dates, "
    "metrics, or projects. If the JD wants something the candidate lacks, put it in 'gaps' — "
    "do not fabricate it into the résumé.\n"
    "- Select EXACTLY 4 projects (by id), ordered most-relevant-first for this JD.\n"
    "- For each chosen project, pick which bullet indices to include (usually all), ordered to "
    "lead with the most JD-relevant point.\n"
    "- 'summary' is 3-4 sentences of PLAIN TEXT (no LaTeX, no markdown, no special symbols) that "
    "reframes the candidate for THIS JD using only corpus facts.\n"
    "- 'skillsOrder' lists the skill-category names most-relevant-first (you may omit clearly "
    "irrelevant categories, but keep at least 5).\n"
    "- Reply with ONLY one compact JSON object, no prose, matching this schema:\n"
    '{"summary":str,"skillsOrder":[str],"projects":[{"id":str,"bulletIdx":[int]}],'
    '"rationale":str,"matchPercent":int,"matched":[str],"gaps":[str],'
    '"atsCovered":[str],"atsMissing":[str],"coverLetter":[str]}\n'
    "'coverLetter' is an array of 3-4 plain-text paragraphs ONLY if asked; otherwise []."
)


def _corpus_text():
    lines = ["SKILL CATEGORIES (use these exact names in skillsOrder):"]
    lines += [f"  - {k}" for k in P.SKILLS]
    lines.append("\nBASE SUMMARY (reframe, don't copy verbatim):")
    lines.append("  " + P.SUMMARY_BASE.replace("\\&", "&"))
    lines.append("\nPROJECTS (id | domains | tech | bullets with indices):")
    for p in P.PROJECTS:
        lines.append(f"* id={p['id']} | rank={p['rank']} | domains={','.join(p['domains'])}")
        lines.append(f"    name: {p['name'].replace(chr(92)+'&','&')}")
        lines.append(f"    tech: {p['tech'].replace(chr(92)+'&','&')}")
        for i, b in enumerate(p["bullets"]):
            clean = b.replace("\\textbf{", "").replace("}", "").replace("$\\rightarrow$", "->").replace("$\\sim$", "~").replace("$\\times$", "x").replace("\\&", "&").replace("\\%", "%")
            lines.append(f"    [{i}] {clean[:240]}")
    return "\n".join(lines)


def handler(event, _ctx):
    try:
        body = json.loads(event.get("body") or "{}")
    except Exception:  # noqa: BLE001
        return _resp(400, {"error": "invalid JSON body"})

    jd = (body.get("jd") or "").strip()
    if len(jd) < 40:
        return _resp(400, {"error": "Paste a fuller job description (at least a few lines)."})
    want_cover = bool(body.get("coverLetter"))
    model_key = body.get("model") if body.get("model") in MODELS else DEFAULT_MODEL
    company = (body.get("company") or "").strip()
    role = (body.get("role") or "").strip()

    user = (
        f"{_corpus_text()}\n\n=== JOB DESCRIPTION ===\n{jd[:6000]}\n\n"
        f"Cover letter requested: {'YES — produce 3-4 paragraphs' if want_cover else 'NO — return []'}."
        + (f"\nTarget company: {company}" if company else "")
        + (f"\nTarget role: {role}" if role else "")
    )
    payload = {
        "anthropic_version": "bedrock-2023-05-31", "max_tokens": 3500, "temperature": 0.3,
        "system": SYSTEM,
        "messages": [{"role": "user", "content": [{"type": "text", "text": user}]}],
    }
    try:
        resp = bedrock.invoke_model(modelId=MODELS[model_key], body=json.dumps(payload))
        text = json.loads(resp["body"].read())["content"][0]["text"]
        sel = json.loads(_first_json(text))
    except Exception as e:  # noqa: BLE001
        print(f"generate failed: {type(e).__name__}: {e}")
        return _resp(502, {"error": "The model could not generate a résumé. Try again."})

    # Render deterministically from the vetted corpus + the AI selection.
    resume_tex = T.render_resume(sel)
    cover_tex = None
    if want_cover and sel.get("coverLetter"):
        cover_tex = T.render_cover_letter(company, role, sel["coverLetter"])

    # Versioned snapshot of every generation (extra: never lose what you sent).
    rid = getattr(_ctx, "aws_request_id", "manual")
    key = f"generated/{rid}/resume.tex"
    try:
        s3.put_object(Bucket=DOCS_BUCKET, Key=key, Body=resume_tex.encode("utf-8"),
                      ContentType="application/x-tex")
        if cover_tex:
            s3.put_object(Bucket=DOCS_BUCKET, Key=f"generated/{rid}/cover-letter.tex",
                          Body=cover_tex.encode("utf-8"), ContentType="application/x-tex")
    except Exception as e:  # noqa: BLE001 — snapshot is best-effort, don't fail the response
        print(f"snapshot failed (non-fatal): {type(e).__name__}: {e}")

    selected = [{"id": s.get("id"), "name": (P.project_by_id(s.get("id")) or {}).get("name", s.get("id"))}
                for s in (sel.get("projects") or [])]
    return _resp(200, {
        "model": model_key,
        "resumeLatex": resume_tex,
        "coverLetterLatex": cover_tex,
        "matchPercent": sel.get("matchPercent"),
        "matched": sel.get("matched", []),
        "gaps": sel.get("gaps", []),
        "atsCovered": sel.get("atsCovered", []),
        "atsMissing": sel.get("atsMissing", []),
        "rationale": sel.get("rationale", ""),
        "selectedProjects": selected,
        "snapshotKey": key,
    })


def _first_json(text):
    s, e = text.find("{"), text.rfind("}")
    return text[s:e + 1] if s != -1 and e != -1 else "{}"


def _resp(code, obj):
    return {
        "statusCode": code,
        "headers": {"content-type": "application/json"},
        "body": json.dumps(obj),
    }
