"""Rule-based classification of job-search emails.

Pure functions, no I/O — fast to unit-test (see test_classifier.py). Tuned for
PRECISION over recall: a job-tracker notification feed is useless if it's full of
Wingstop receipts and GitHub CI emails, so we only classify when a real job
signal is present. Categories, most-specific first: interview > offer > rejection
> confirmation > recruiter_reply. Returns (category, confidence, matched_terms).
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
        # deliberately NOT a bare "offer" (matches "special offer", "Marketplace offer")
        r"\bpleased to offer\b", r"\bextend(?:ing)? (?:you )?an offer\b",
        r"\bjob offer\b", r"\boffer letter\b", r"\bwelcome (?:aboard|to the team)\b",
        r"\bwe(?:'| a)re excited to offer\b",
    ]),
    ("rejection", [
        r"\bunfortunately\b", r"\bwe(?:'| a)re not (?:moving|proceeding)\b",
        r"\bnot (?:be )?moving forward\b", r"\bdecided (?:to|not to) (?:move|proceed)\b",
        r"\bother candidates?\b", r"\bwill not be (?:proceeding|progressing)\b",
        r"\bnot (?:a|the right) (?:fit|match)\b", r"\bposition has been filled\b",
        r"\bpursue other (?:candidates|applicants)\b",
    ]),
    ("confirmation", [
        r"\bthank you for (?:applying|your application)\b", r"\bapplication (?:received|submitted)\b",
        r"\bwe(?:'| ha)ve received your application\b", r"\bsuccessfully applied\b",
        r"\bapplication (?:confirmation|has been received)\b", r"\byour application (?:for|to) .* (?:has been|was) received\b",
    ]),
    ("recruiter_reply", [
        r"\brecruiter\b", r"\btalent (?:acquisition|team)\b", r"\bsourc(?:er|ing) (?:for|a)\b",
        r"\bcame across your (?:profile|resume|résumé|linkedin)\b",
        r"\bregarding your application\b", r"\bfor (?:the|a|this) (?:role|position|opening) (?:at|with)\b",
        r"\bhiring (?:manager|team) (?:at|for)\b", r"\bjob opportunity\b",
    ]),
]

# Applicant-tracking-system / recruiting sender domains — a strong signal.
ATS_DOMAINS = ("greenhouse", "lever.co", "hire.lever", "ashbyhq", "workday", "myworkday",
               "icims", "smartrecruiters", "jobvite", "taleo", "bamboohr", "workable",
               "gem.com", "eightfold")


def classify(subject: str, body: str, sender: str = ""):
    """Return (category, confidence 0-1, matched_terms). 'other' if not job-related."""
    low = f"{subject or ''}\n{body or ''}".lower()
    for category, patterns in RULES:
        hits = [p for p in patterns if re.search(p, low)]
        if hits:
            return category, round(min(1.0, 0.6 + 0.13 * len(hits)), 2), hits
    # No keyword rule matched — only trust a recruiting/ATS sender domain.
    if _from_ats(sender):
        return "recruiter_reply", 0.5, []
    return "other", 0.0, []


def is_job_related(subject: str, body: str, sender: str = "") -> bool:
    return classify(subject, body, sender)[0] != "other"


def _from_ats(sender: str) -> bool:
    s = (sender or "").lower()
    local = s.split("@")[0]
    domain = s.split("@")[-1] if "@" in s else ""
    if any(a in domain for a in ATS_DOMAINS):
        return True
    # careers@/jobs@/recruiting@/talent@ style mailboxes (not generic noreply@)
    return any(local.startswith(k) or ("." + k) in local for k in ("recruit", "talent", "careers", "jobs", "hiring"))


def match_company(sender: str, subject: str, body: str, companies) -> str | None:
    """Best-effort link to a tracked application by company name or sender domain."""
    text = f"{subject or ''} {body or ''} {sender or ''}".lower()
    domain = sender.split("@")[-1].lower() if "@" in (sender or "") else ""
    for company in companies:
        c = (company or "").strip().lower()
        if not c:
            continue
        token = re.sub(r"[^a-z0-9]", "", c.split()[0]) if c.split() else ""
        if (token and len(token) >= 3 and token in domain) or c in text:
            return company
    return None
