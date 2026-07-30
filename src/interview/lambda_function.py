"""interview — the interview-prep companion: a STAR+Reflection story bank grounded in
the candidate's REAL corpus (never invents), a competency matcher that picks the right
story for a behavioral question, a deterministic company/JD red-flag detector, a
time-blocked prep plan, and a post-interview debrief template. All $0 / deterministic;
an AI layer can draft new stories from a project later, but every seed story here is a
true fact from the résumé/portfolio.

Routes (behind the Cognito JWT via the api Lambda):
  POST /interview/stories   {question}         -> best-matching STAR stories
  POST /interview/redflags  {text}             -> company/JD red flags
  POST /interview/plan      {role, days}        -> time-blocked prep plan
  POST /interview/debrief   {role}              -> debrief template
"""
import json
import re

# --- STAR+Reflection story bank — every story is a TRUE fact from the corpus ----
SEED_STORIES = [
    {
        "id": "oncall-incident", "title": "Weekly on-call: triage under pressure",
        "situation": "At HCLTech I held a weekly on-call rotation for a client's containerized B2B platform on AWS (ECS Fargate behind an ALB, RDS, Route 53).",
        "task": "When alerts fired I had to assess customer impact fast and restore service.",
        "action": "I triaged CloudWatch and PagerDuty alerts, separated signal from noise, executed the documented rollback/recovery, and authored a root-cause analysis.",
        "result": "Service was restored on the runbook path and the RCA fed a fix that stopped the recurrence.",
        "reflection": "Under pressure the discipline is separating the noisy symptom from the real cause before you act.",
        "tags": ["reliability", "incident", "pressure", "ownership", "communication"],
    },
    {
        "id": "eks-drill", "title": "Proving the platform recovers (EKS)",
        "situation": "I built a production-shaped Kubernetes platform on Amazon EKS with no prior EKS experience.",
        "task": "I wanted to prove it recovered and scaled, not assume it.",
        "action": "I deleted a pod under load and drove the CPU HPA, measuring everything, and documented the honest ALB-deregistration behavior I found.",
        "result": "A deleted pod self-healed in ~7 seconds and the HPA scaled 2 to 6 pods in ~60; I shipped it with honest findings, not just green checks.",
        "reflection": "Measure it, don't estimate it — and write down the ugly finding, because that's the credible part.",
        "tags": ["initiative", "ambiguity", "learning", "technical-depth", "honesty"],
    },
    {
        "id": "secpipe-secret", "title": "The pipeline that blocked a planted secret",
        "situation": "I wanted to guarantee no secret or misconfiguration could merge into a container project.",
        "task": "Build enforcement, not a guideline.",
        "action": "I built a GitHub Actions pipeline with fail-the-build gates (gitleaks, Checkov/tfsec, Trivy) enforced by branch protection, then tested it by planting a hardcoded AWS credential in a PR.",
        "result": "The pipeline automatically blocked the PR — prevention proven, not promised.",
        "reflection": "Prevention beats detection; a control you haven't tried to break isn't a control yet.",
        "tags": ["security", "quality", "ownership", "initiative", "rigor"],
    },
    {
        "id": "cicd-rebuild", "title": "Turning a manual release into a pipeline",
        "situation": "A client's release process was half-manual with console changes going straight to production.",
        "task": "Make deploys safe and repeatable.",
        "action": "I rebuilt it into a GitHub Actions pipeline — tests, security scanning, image versioning, staged deploys, and a gated production rollout with automatic rollback.",
        "result": "Deployments got shorter and console-based production changes were removed.",
        "reflection": "Automate the riskiest path first; that's where the manual error lives.",
        "tags": ["automation", "improvement", "impact", "ownership"],
    },
    {
        "id": "restore-test", "title": "The backup nobody had tested",
        "situation": "In my CloudOps lab I had backups configured but had never proven a restore.",
        "task": "Verify recovery against a target, not on faith.",
        "action": "I ran a timed RDS restore test end-to-end and measured the actual recovery time.",
        "result": "Measured a 6m36s RTO against a 60-minute target — a real, documented number.",
        "reflection": "An untested backup is a hope, not a recovery plan.",
        "tags": ["reliability", "rigor", "ownership", "resilience"],
    },
]

# Behavioral-question phrasing -> competency tags to match against story tags.
QUESTION_TAGS = [
    (re.compile(r"\b(fail|failure|mistake|went wrong|didn'?t go|setback)\b", re.I), ["honesty", "learning", "rigor"]),
    (re.compile(r"\b(pressure|deadline|stress|urgent|firefight|crisis|incident|outage)\b", re.I), ["pressure", "incident", "reliability"]),
    (re.compile(r"\b(ownership|initiative|above and beyond|took (?:the )?lead|proactive|without being asked)\b", re.I), ["ownership", "initiative"]),
    (re.compile(r"\b(ambigu|unclear|no (?:instruction|direction)|figure (?:it )?out|new (?:tech|tool|domain)|learn)\b", re.I), ["ambiguity", "learning", "initiative"]),
    (re.compile(r"\b(improve|process|efficien|automat|streamlin|optimi[sz]e)\b", re.I), ["improvement", "automation", "impact"]),
    (re.compile(r"\b(quality|test|bug|security|reliab|robust)\b", re.I), ["quality", "security", "rigor", "reliability"]),
    (re.compile(r"\b(technical|hardest|complex|deep|architecture|design)\b", re.I), ["technical-depth", "initiative"]),
    (re.compile(r"\b(impact|result|proud|accomplish|achieve)\b", re.I), ["impact", "ownership"]),
]
# Competencies the corpus doesn't cover well — flag so he PREPARES a story instead of faking one.
GAP_COMPETENCIES = {
    "conflict": re.compile(r"\b(conflict|disagree|difficult (?:person|coworker|teammate)|pushback|convince)\b", re.I),
    "leadership": re.compile(r"\b(lead(?:ership)? a team|managed people|mentor|delegate)\b", re.I),
    "customer": re.compile(r"\b(customer|stakeholder|client-facing|non-technical audience)\b", re.I),
}


def match_stories(question, stories=None, top=3):
    """Pick the best STAR stories for a behavioral question by tag overlap. Also flags
    competencies the corpus doesn't cover, so he prepares rather than fabricates."""
    stories = stories or SEED_STORIES
    wanted = []
    for rx, tags in QUESTION_TAGS:
        if rx.search(question or ""):
            wanted += tags
    wanted = set(wanted)
    scored = []
    for s in stories:
        overlap = len(wanted & set(s["tags"]))
        if overlap:
            scored.append((overlap, s))
    scored.sort(key=lambda x: -x[0])
    picks = [s for _, s in scored[:top]] or stories[:1]  # never return nothing
    gaps = [name for name, rx in GAP_COMPETENCIES.items() if rx.search(question or "")]
    return {
        "matched": [{"id": s["id"], "title": s["title"], "star": {k: s[k] for k in ("situation", "task", "action", "result", "reflection")}} for s in picks],
        "competencies": sorted(wanted),
        "prepGap": ([f"Your corpus is thin on '{g}' — prepare a specific story before the interview; don't improvise or invent one." for g in gaps]),
    }


# --- company / JD red-flag detector (deterministic) --------------------------
RED_FLAGS = [
    (re.compile(r"\b(fast[-\s]?paced)\b", re.I), "'fast-paced' — often code for understaffed / high churn. Ask about on-call load and headcount."),
    (re.compile(r"\b(wear(?:s|ing)? many hats|jack of all trades)\b", re.I), "'wear many hats' — role may be undefined / you'll absorb others' work. Ask what the first 90 days actually cover."),
    (re.compile(r"\b(rock ?star|ninja|guru|10x)\b", re.I), "'rockstar/ninja' language — immature job design. Ask how success is measured concretely."),
    (re.compile(r"\bwe'?re (?:like )?a family\b", re.I), "'we're a family' — can blur boundaries / discourage saying no. Ask about work-life norms and PTO usage."),
    (re.compile(r"\b(unlimited (?:pto|vacation))\b", re.I), "'unlimited PTO' — sometimes means people take LESS. Ask the average days actually taken."),
    (re.compile(r"\b(whatever it takes|no ego|hustle|grind|work hard play hard)\b", re.I), "hustle/grind language — possible crunch culture. Ask about typical hours and crunch frequency."),
    (re.compile(r"\b(thrive (?:in|under) (?:chaos|ambiguity|pressure)|comfortable with chaos)\b", re.I), "'thrive in chaos' — process may be immature. Ask what's documented vs tribal knowledge."),
    (re.compile(r"\b(competitive salary)\b", re.I), "'competitive salary' with no band — get a number early so you don't waste rounds."),
    (re.compile(r"\b(must be able to (?:relocate|work on-?site) .{0,20}(?:immediately|no exceptions))\b", re.I), "hard relocation/on-site demand — confirm it fits your OPT/work-auth timeline."),
    (re.compile(r"\b(entry[-\s]?level).{0,40}(\d)\s*\+?\s*years", re.I), "'entry-level' but asks for years of experience — contradictory bar; clarify the real requirement."),
]


def red_flags(text):
    t = text or ""
    hits = [{"phrase": rx.pattern, "flag": note} for rx, note in RED_FLAGS if rx.search(t)]
    return {
        "flags": [h["flag"] for h in hits],
        "count": len(hits),
        "note": ("No obvious language red flags — still check Glassdoor/Blind and ask your interviewers about on-call, hours, and turnover."
                 if not hits else "Language red flags found — turn each into a question for your interviewer."),
    }


def prep_plan(role, days=3):
    """A time-blocked prep plan sized to the days you have before the interview."""
    role = role or "the role"
    days = max(1, min(int(days or 3), 10))
    blocks = [
        ("Company + role", f"Read the JD line by line; map each requirement to one of your projects. Skim recent company news and the team's tech blog. Prepare 3 questions about {role}."),
        ("Behavioral (STAR)", "Rehearse 5 master STAR+Reflection stories out loud (on-call, EKS drill, secure pipeline, CI/CD rebuild, restore test). Prepare a 'failure' and a 'conflict' story specifically."),
        ("Technical depth", "Re-run/re-read your most relevant project so you can whiteboard it. Be ready to defend a trade-off you made and one thing you'd do differently."),
        ("Fundamentals", "Refresh the JD's core stack (VPC/IAM/Terraform/CI-CD or K8s as relevant). Do 1-2 scenario questions ('a service is 5xx-ing, walk me through it')."),
        ("Logistics + questions", "Test your A/V, confirm the schedule, prep your questions, and get the salary conversation ready. Sleep."),
    ]
    plan = []
    for i in range(days):
        b = blocks[i] if i < len(blocks) else blocks[-1]
        plan.append({"day": f"T-minus {days - i}", "focus": b[0], "task": b[1]})
    return {"role": role, "days": days, "plan": plan}


def debrief_template(role):
    return {
        "role": role or "the role",
        "capture": [
            "Which questions did I answer well? Which did I fumble — and what was the better answer?",
            "What did I learn about the team, the on-call load, and the actual day-to-day?",
            "Any red flags surfaced live (hours, turnover, unclear scope)?",
            "What are my open questions for the next round?",
            "Interviewers' names/roles (for thank-you notes and future connection).",
            "My read: do I still want this? Rate fit 1-5 and why.",
        ],
        "followUp": "Send a concise thank-you within 24h referencing one specific thing from the conversation.",
    }


def handler(event, _ctx):
    path = event.get("rawPath", "")
    try:
        body = json.loads(event.get("body") or "{}")
    except (ValueError, TypeError):
        body = {}
    if path.endswith("/stories"):
        res = match_stories(body.get("question", ""))
    elif path.endswith("/redflags"):
        res = red_flags(body.get("text", ""))
    elif path.endswith("/plan"):
        res = prep_plan(body.get("role", ""), body.get("days", 3))
    elif path.endswith("/debrief"):
        res = debrief_template(body.get("role", ""))
    else:
        return {"statusCode": 404, "body": json.dumps({"error": "not found"})}
    return {"statusCode": 200, "headers": {"Content-Type": "application/json"},
            "body": json.dumps(res)}
