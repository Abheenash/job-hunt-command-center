"""evaluate — the on-demand AI layer over the openings radar: a structured deep-eval
report for one role, and a 6-axis company-research brief. Bedrock does the reasoning,
grounded in the candidate corpus + the JD (anti-fabrication: it reasons about fit and
gaps, it never invents the candidate's experience). Both fall back to a deterministic
report/checklist when Bedrock is unavailable, so the feature always returns something
useful. Kept OFF the daily scan so the scan stays $0 — this only runs when the user
clicks 'deep eval' / 'research' on a role.

Routes (behind the Cognito JWT via the api Lambda):
  POST /evaluate  {jd, company, role}  -> 5-dimension deep-eval report
  POST /research  {company, role}      -> 6-axis research brief
"""
import json
import re

import boto3

bedrock = boto3.client("bedrock-runtime")
MODELS = [("us.anthropic.claude-sonnet-4-6", "sonnet-4.6"),
          ("us.anthropic.claude-sonnet-4-5-20250929-v1:0", "sonnet-4.5"),
          ("us.anthropic.claude-haiku-4-5-20251001-v1:0", "haiku-4.5")]

CANDIDATE_SUMMARY = (
    "Early-career (about 2 years + an M.S.). AWS SAA + CCP certified. DevOps/AWS cloud-ops "
    "experience at HCLTech (on-call, Terraform, CI/CD). Strong self-built AWS projects: EKS "
    "platform, DevSecOps container pipeline, observability/SRE, day-2 CloudOps lab, an "
    "event-driven Bedrock app. Stack: AWS, Terraform, CI/CD, Kubernetes/EKS, Python, Linux, "
    "C++ concurrency. Needs H-1B visa sponsorship (F-1 STEM OPT). Houston-based, relocates in US."
)
# Notable techs outside the corpus — used for the deterministic gaps read (mirrors openings).
GAP_TERMS = {
    "gcp": "GCP", "google cloud": "GCP", "azure": "Azure", "spark": "Spark", "kafka": "Kafka",
    "flink": "Flink", "airflow": "Airflow", "scala": "Scala", "java ": "Java", "go ": "Go",
    "golang": "Go", "ruby": "Ruby", "rust": "Rust", "vmware": "VMware", "openshift": "OpenShift",
    "istio": "Istio", "envoy": "Envoy", "snowflake": "Snowflake", "databricks": "Databricks",
    ".net": ".NET", "c#": "C#", "powershell": "PowerShell", "windows server": "Windows Server",
}
REQ_YEARS_RE = re.compile(r"(\d{1,2})\s*\+?\s*(?:to|-|–|—)?\s*\d{0,2}\s*years?\b", re.I)
SENIOR_RE = re.compile(r"\b(senior|staff|principal|lead|sr\.?|manager|director|iii|iv)\b", re.I)
SALARY_RE = re.compile(r"\$\s?(\d{2,3})(?:,?\d{3})?\s?[kK]?\s?(?:-|–|—|to)\s?\$?\s?(\d{2,3})(?:,?\d{3})?\s?[kK]?")


def _req_years(jd):
    yrs = [int(m) for m in REQ_YEARS_RE.findall(jd or "") if 0 < int(m) <= 20]
    return max(yrs) if yrs else 0


def deterministic_eval(jd, company, role):
    """A structured, deterministic deep-eval report — the always-available baseline the AI
    layers on top of. No fabrication: only observable JD facts + the candidate summary."""
    t = (jd or "").lower()
    title = (role or "").lower()
    gaps = sorted({label for kw, label in GAP_TERMS.items() if kw in t})
    yrs = _req_years(jd)
    if SENIOR_RE.search(title) or yrs >= 5:
        level, level_note = "reach", "Senior/5y+ signalled — above your range; only apply if the JD body is genuinely mid-level."
    elif yrs in (0, 1, 2):
        level, level_note = "on-target", "Entry/associate range — a real fit; apply with confidence."
    else:
        level, level_note = "stretch", f"{yrs}y asked — applyable; frame your project depth to close the gap."
    sm = SALARY_RE.search(jd or "")
    comp = f"Advertised band found in the JD: {sm.group(0)}" if sm else "No band in the JD — research comps (levels.fyi/Glassdoor) before any call."
    return {
        "company": company, "role": role,
        "dimensions": {
            "fit": "Cloud/DevOps/SRE/systems roles are your lane; score the JD against your AWS+Terraform+CI/CD+EKS core.",
            "gaps": gaps or ["None obvious from the JD vocabulary."],
            "levelStrategy": {"level": level, "note": level_note, "reqYears": yrs},
            "comp": comp,
            "personalization": "Lead with the single most JD-relevant real project and one measured result from it.",
        },
        "sponsorshipReminder": "Confirm visa sponsorship before investing rounds — you need H-1B (F-1 OPT).",
        "source": "deterministic",
    }


def build_eval_prompt(jd, company, role):
    system = (
        "You are a candid job-fit analyst for ONE candidate. Reason ONLY about fit using the "
        "candidate summary and the JD; never invent the candidate's experience, skills, or "
        "metrics. Return STRICT JSON with keys: fitSummary (string), matchedStrengths (array), "
        "gaps (array), levelStrategy (string), compNote (string), personalizationAngle (string), "
        "starAngle (string), sponsorshipRisk (string), score (number 1-5). Be honest, concrete, "
        "and brief. No markdown, JSON only."
    )
    user = (f"CANDIDATE:\n{CANDIDATE_SUMMARY}\n\nCOMPANY: {company}\nROLE: {role}\n\n"
            f"JOB DESCRIPTION:\n{(jd or '')[:5000]}")
    return {"system": system, "user": user}


def build_research_prompt(company, role):
    system = (
        "You produce a 6-axis research brief for a job candidate about a company. Cover: "
        "1) what the company does + AI/tech strategy, 2) recent moves (funding, launches, layoffs) "
        "3) engineering culture, 4) likely current challenges, 5) main competitors, 6) the angle "
        "THIS candidate should take. Where you are unsure or the info may be stale, say so and give "
        "the exact thing to verify. Return STRICT JSON: {axes:[{title,summary,verify}], candidateAngle}. "
        "JSON only, no markdown."
    )
    user = f"CANDIDATE:\n{CANDIDATE_SUMMARY}\n\nCOMPANY: {company}\nROLE: {role}"
    return {"system": system, "user": user}


def research_checklist(company, role):
    """Deterministic 6-axis research CHECKLIST — always useful, even with no AI: the exact
    questions to answer before applying/interviewing."""
    axes = [
        ("What they do + AI/tech strategy", f"What is {company}'s core product and where is AI/cloud in it? Read the homepage + eng blog."),
        ("Recent moves", "Funding round, layoffs, launches, or leadership changes in the last 12 months? (Crunchbase / news / Blind.)"),
        ("Engineering culture", "On-call load, remote/hybrid norms, deploy frequency, tenure? (Glassdoor / Blind / the team's talks.)"),
        ("Likely challenges", "Scale, reliability, cost, or migration pain the role would touch? Infer from the JD's pain points."),
        ("Competitors", f"Who competes with {company}, and how would you frame {company} as your choice?"),
        ("Your angle", "Which of your real projects maps to their #1 need, and what's your honest gap + ramp story?"),
    ]
    return {"company": company, "role": role,
            "axes": [{"title": t, "verify": q} for t, q in axes],
            "candidateAngle": "Anchor on your closest real project; be honest about being early-career and needing sponsorship.",
            "source": "deterministic-checklist"}


def _first_json(text):
    if not text:
        return None
    m = re.search(r"\{.*\}", text, re.S)
    try:
        return json.loads(m.group(0)) if m else None
    except (ValueError, TypeError):
        return None


def _invoke_json(system, user, max_tokens=1200):
    payload = {"anthropic_version": "bedrock-2023-05-31", "max_tokens": max_tokens,
               "temperature": 0.2, "system": system, "messages": [{"role": "user", "content": user}]}
    for model_id, _n in MODELS:
        try:
            resp = bedrock.invoke_model(modelId=model_id, body=json.dumps(payload))
            data = json.loads(resp["body"].read())
            text = "".join(b.get("text", "") for b in data.get("content", []))
            parsed = _first_json(text)
            if parsed:
                return parsed
        except Exception as e:  # noqa: BLE001
            print(f"evaluate: model {model_id} failed: {type(e).__name__}: {e}")
    return None


def evaluate(jd, company, role):
    base = deterministic_eval(jd, company, role)
    p = build_eval_prompt(jd, company, role)
    ai = _invoke_json(p["system"], p["user"])
    if ai:
        base["ai"] = ai
        base["source"] = "bedrock+deterministic"
    return base


def research(company, role):
    p = build_research_prompt(company, role)
    ai = _invoke_json(p["system"], p["user"])
    if ai:
        ai["source"] = "bedrock"
        ai["company"] = company
        ai["role"] = role
        ai["verifyNote"] = "AI knowledge may be stale — verify each axis before you rely on it."
        return ai
    return research_checklist(company, role)


def handler(event, _ctx):
    path = event.get("rawPath", "")
    try:
        body = json.loads(event.get("body") or "{}")
    except (ValueError, TypeError):
        body = {}
    if path.endswith("/evaluate"):
        res = evaluate(body.get("jd", ""), body.get("company", "the company"), body.get("role", "the role"))
    elif path.endswith("/research"):
        res = research(body.get("company", "the company"), body.get("role", "the role"))
    else:
        return {"statusCode": 404, "body": json.dumps({"error": "not found"})}
    return {"statusCode": 200, "headers": {"Content-Type": "application/json"},
            "body": json.dumps(res)}
