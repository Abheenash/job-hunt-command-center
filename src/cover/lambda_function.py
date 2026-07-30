"""cover — research-backed cover-letter generator. Bedrock (Claude) drafts the letter,
grounded ONLY in the candidate corpus + the JD (same anti-fabrication guardrail as the
résumé generator): it mirrors the JD's language and picks a real project as the anchor,
but may never invent experience, skills, or metrics. Falls back to a deterministic
letter (from real facts) if Bedrock is unavailable — so it always returns something true.

Route (behind the Cognito JWT via the api Lambda):
  POST /cover  {jd, company, role, angle?}  -> {letter, source, keywordsMirrored}
"""
import json
import os
import re

import boto3

bedrock = boto3.client("bedrock-runtime")
# Same model chain convention as resume_gen (sonnet default, fallbacks in order).
MODELS = [("us.anthropic.claude-sonnet-4-6", "sonnet-4.6"),
          ("us.anthropic.claude-sonnet-4-5-20250929-v1:0", "sonnet-4.5"),
          ("us.anthropic.claude-haiku-4-5-20251001-v1:0", "haiku-4.5")]
MAX_LETTER_CHARS = int(os.environ.get("MAX_LETTER_CHARS", "2600"))

# The corpus the model may draw from — facts only (mirrors profile.py, kept local so this
# Lambda packages standalone). The model selects/rephrases; it never adds a fact.
CORPUS = {
    "name": "Abheenash Rajolu", "first": "Abheenash",
    "creds": "AWS Certified Solutions Architect - Associate and Cloud Practitioner, M.S. in Computer & Systems Engineering (University of Houston)",
    "experience": "professional DevOps / AWS cloud-operations experience (weekly on-call incident response, Terraform migrations with drift detection, security-gated CI/CD)",
    "projects": {
        "aws-eks-platform": "a production-shaped Amazon EKS platform in Terraform (IRSA, ALB Ingress, HPA) proven with live self-heal (~7s) and autoscale drills",
        "secure-container-pipeline": "a DevSecOps pipeline (gitleaks/Checkov/tfsec/Trivy fail-the-build gates) that automatically blocked a PR carrying a planted AWS credential",
        "cloud-observability-sre": "an observability/SRE build with golden-signals dashboards, SLOs, and a real failure-injection drill",
        "aws-cloudops-lab": "a day-2 operations lab with incident drills, RCAs, and a measured 6m36s RDS restore",
        "job-hunt-command-center": "an event-driven Amazon Bedrock pipeline on Cognito/API Gateway/Lambda/Step Functions",
    },
    "site": "abheenash.com",
}

# JD-keyword -> which real project to anchor the letter on (deterministic angle pick).
ANGLE_MAP = [
    (re.compile(r"\b(kubernetes|k8s|eks|helm|ingress|container orchestrat)\b", re.I), "aws-eks-platform"),
    (re.compile(r"\b(security|devsecops|compliance|vulnerab|hardening|secrets)\b", re.I), "secure-container-pipeline"),
    (re.compile(r"\b(observability|sre|slo|monitoring|incident|reliability|on-call)\b", re.I), "cloud-observability-sre"),
    (re.compile(r"\b(support|troubleshoot|operations|day.?2|restore|backup|patch)\b", re.I), "aws-cloudops-lab"),
    (re.compile(r"\b(serverless|lambda|event.?driven|bedrock|genai|step functions)\b", re.I), "job-hunt-command-center"),
]

# A small stack vocabulary to report which JD keywords the letter should mirror.
MIRROR_KW = ["aws", "terraform", "kubernetes", "eks", "ci/cd", "cicd", "docker", "python",
             "linux", "iam", "vpc", "observability", "sre", "on-call", "incident",
             "security", "devsecops", "serverless", "automation", "reliability"]


def pick_angle(jd):
    for rx, pid in ANGLE_MAP:
        if rx.search(jd or ""):
            return pid
    return "aws-eks-platform"


def keywords_to_mirror(jd):
    t = (jd or "").lower()
    return [k for k in MIRROR_KW if k in t][:10]


def build_prompt(jd, company, role, angle_pid=None):
    """Construct the grounded Bedrock prompt. The system prompt forbids fabrication and
    caps length; the user message carries the JD + the ONLY facts the model may use."""
    angle_pid = angle_pid or pick_angle(jd)
    anchor = CORPUS["projects"].get(angle_pid, next(iter(CORPUS["projects"].values())))
    mirror = keywords_to_mirror(jd)
    system = (
        "You write a concise, specific cover letter for a job candidate. HARD RULES: "
        "use ONLY the facts provided about the candidate; never invent experience, skills, "
        "employers, metrics, or dates. Mirror the job description's own language where it "
        "honestly applies. 3 short paragraphs, under "
        f"{MAX_LETTER_CHARS} characters, no clichés, no em-dash walls, plain text (no markdown). "
        "Be honest about being early-career; lead with proof, not adjectives."
    )
    user = (
        f"Company: {company}\nRole: {role}\n\n"
        f"CANDIDATE FACTS (the only facts you may use):\n"
        f"- {CORPUS['name']}, {CORPUS['creds']}.\n"
        f"- {CORPUS['experience']}.\n"
        f"- Anchor project to feature: {anchor}.\n"
        f"- Portfolio: {CORPUS['site']}.\n\n"
        f"JOB DESCRIPTION:\n{(jd or '')[:4000]}\n\n"
        f"Mirror these JD terms where truthful: {', '.join(mirror) or '(none found)'}.\n"
        f"Write the letter body only (no address block), signed '{CORPUS['first']} Rajolu'."
    )
    return {"system": system, "user": user, "anglePid": angle_pid, "keywordsMirrored": mirror}


def fallback_letter(jd, company, role, angle_pid=None):
    """A real, deterministic cover letter from the corpus — used when Bedrock is
    unavailable. True facts only; no fabrication."""
    angle_pid = angle_pid or pick_angle(jd)
    anchor = CORPUS["projects"].get(angle_pid, next(iter(CORPUS["projects"].values())))
    return (
        f"Dear {company} Team,\n\n"
        f"I'm applying for the {role} role. I'm {CORPUS['creds']}, with {CORPUS['experience']} "
        f"— hands-on cloud and DevOps work rather than adjectives.\n\n"
        f"The piece most relevant to your team is {anchor}. I build end-to-end, in Terraform, "
        f"and prove my work with real drills and honest write-ups rather than assuming it works. "
        f"I'm early-career and I lead with that proof.\n\n"
        f"I'd welcome the chance to talk about how I can contribute. More of my work is at "
        f"{CORPUS['site']}. Thank you for your time.\n\n"
        f"Best,\n{CORPUS['first']} Rajolu"
    )


def _invoke(system, user):
    payload = {"anthropic_version": "bedrock-2023-05-31", "max_tokens": 900, "temperature": 0.4,
               "system": system, "messages": [{"role": "user", "content": user}]}
    for model_id, _name in MODELS:
        try:
            resp = bedrock.invoke_model(modelId=model_id, body=json.dumps(payload))
            data = json.loads(resp["body"].read())
            return "".join(b.get("text", "") for b in data.get("content", [])).strip()
        except Exception as e:  # noqa: BLE001 — fall through the model chain, then to the deterministic letter
            print(f"cover: model {model_id} failed: {type(e).__name__}: {e}")
    return None


def generate(jd, company, role, angle_pid=None):
    p = build_prompt(jd, company, role, angle_pid)
    letter = _invoke(p["system"], p["user"])
    source = "bedrock"
    if not letter:
        letter, source = fallback_letter(jd, company, role, p["anglePid"]), "deterministic-fallback"
    return {"letter": letter[:MAX_LETTER_CHARS], "source": source,
            "anchorProject": p["anglePid"], "keywordsMirrored": p["keywordsMirrored"]}


def handler(event, _ctx):
    try:
        body = json.loads(event.get("body") or "{}")
    except (ValueError, TypeError):
        body = {}
    res = generate(body.get("jd", ""), body.get("company", "the company"),
                   body.get("role", "the role"), body.get("angle"))
    return {"statusCode": 200, "headers": {"Content-Type": "application/json"},
            "body": json.dumps(res)}
