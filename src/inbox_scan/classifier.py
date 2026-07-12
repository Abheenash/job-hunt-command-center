"""Rule-based classification of job-search emails.

Pure functions, no I/O — so they're fast to unit-test (see test_classifier.py).
Categories, most-specific first: interview > offer > rejection > recruiter_reply
> confirmation > other. Returns (category, confidence, matched_terms).

This is intentionally simple and auditable; Bedrock-based extraction is future
scope. The point of keeping it a pure function with tests is that a bad change
can't silently start mislabeling mail.
"""
import re

# Ordered: the first category whose patterns hit wins.
RULES = [
    ("interview", [
        r"\binterview\b", r"\bschedule (?:a )?(?:call|meeting|chat)\b",
        r"\bavailab(?:le|ility)\b.*\b(?:call|chat|meet)", r"\bnext (?:round|step)s?\b",
        r"\bphone screen\b", r"\btechnical (?:screen|round|assessment)\b",
        r"\bcalendly\b", r"\bset up a time\b", r"\bmeet with the team\b",
    ]),
    ("offer", [
        r"\boffer\b", r"\bpleased to offer\b", r"\bextend an offer\b",
        r"\boffer letter\b", r"\bwelcome (?:aboard|to the team)\b",
    ]),
    ("rejection", [
        r"\bunfortunately\b", r"\bwe(?:'| a)re not (?:moving|proceeding)\b",
        r"\bnot (?:be )?moving forward\b", r"\bdecided (?:to|not to) (?:move|proceed)\b",
        r"\bother candidates?\b", r"\bwill not be (?:proceeding|progressing)\b",
        r"\bnot (?:a|the right) (?:fit|match)\b", r"\bwish you (?:the best|luck)\b",
        r"\bposition has been filled\b", r"\bpursue other\b",
    ]),
    # Confirmations are specific, so they're checked before the broader
    # recruiter_reply rules (which must NOT include a bare "your application").
    ("confirmation", [
        r"\bthank you for (?:applying|your application)\b", r"\bapplication (?:received|submitted)\b",
        r"\bwe(?:'| ha)ve received your application\b", r"\bsuccessfully applied\b",
        r"\bapplication (?:confirmation|has been received)\b",
    ]),
    ("recruiter_reply", [
        r"\brecruiter\b", r"\btalent (?:acquisition|team)\b", r"\bsourc(?:er|ing)\b",
        r"\breach(?:ing)? out\b", r"\bcame across your (?:profile|resume|résumé)\b",
        r"\bopportunity\b", r"\bhiring (?:manager|team)\b",
    ]),
]

# A weak signal that an email is job-related at all (used to skip noise).
JOB_HINT = re.compile(
    r"\b(?:appl(?:y|ied|ication)|interview|recruit|role|position|candidate|hiring|"
    r"opportunity|resume|résumé|offer|job)\b", re.I)


def classify(subject: str, body: str, sender: str = ""):
    """Return (category, confidence 0-1, matched_terms)."""
    text = f"{subject or ''}\n{body or ''}"
    low = text.lower()
    for category, patterns in RULES:
        hits = [p for p in patterns if re.search(p, low)]
        if hits:
            # confidence scales with the number of independent signals.
            conf = min(1.0, 0.55 + 0.15 * len(hits))
            return category, round(conf, 2), hits
    if JOB_HINT.search(low) or _looks_like_recruiter(sender):
        return "recruiter_reply", 0.4, []
    return "other", 0.0, []


def is_job_related(subject: str, body: str, sender: str = "") -> bool:
    cat, _conf, _ = classify(subject, body, sender)
    return cat != "other"


def _looks_like_recruiter(sender: str) -> bool:
    s = (sender or "").lower()
    return any(k in s for k in ("recruit", "talent", "careers", "jobs", "noreply@", "no-reply@", "hr@"))


def match_company(sender: str, subject: str, body: str, companies) -> str | None:
    """Best-effort link to a tracked application by company name or sender domain."""
    text = f"{subject or ''} {body or ''} {sender or ''}".lower()
    domain = sender.split("@")[-1].lower() if "@" in (sender or "") else ""
    best = None
    for company in companies:
        c = (company or "").strip().lower()
        if not c:
            continue
        # domain match (acme.com <- Acme) or name appears in the text
        token = re.sub(r"[^a-z0-9]", "", c.split()[0]) if c.split() else ""
        if (token and token in domain) or c in text:
            best = company
            break
    return best
