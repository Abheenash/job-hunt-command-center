"""outreach — draft the contact-and-reach-out layer for an application: a <=300-char
LinkedIn connection note (tuned per contact type) and a formal application email
(recruiter / referral / cold). DRAFT-ONLY: this never sends, submits, or clicks
anything — it hands you copy to paste. Deterministic templates ($0); an optional AI
polish can layer on top later.

Standing rules baked in (see the owner's outreach guidance):
  - NEVER mention HCL / HCLTech in a LinkedIn note.
  - LinkedIn notes are hard-capped at 300 characters.
  - Lead with the Houston / UH-alum warmth when the contact shares it.
"""
import json
import re

# Minimal self-contained candidate context (kept local so this Lambda packages alone,
# like the others). Facts only — mirrors profile.py; never invents.
CANDIDATE = {
    "first": "Abheenash",
    "stack": "AWS, Terraform, CI/CD, Kubernetes",
    "creds": "AWS certified, M.S.",
    "site": "abheenash.com",
    "base": "Houston",
    "school": "University of Houston",
}
LINKEDIN_LIMIT = 300
HCL_RE = re.compile(r"\bHCL(?:Tech)?\b", re.I)


def _fit(s):
    """Never let an HCL mention slip into outreach; trim a note to the LinkedIn limit on a
    word boundary (keeping the sign-off) rather than a hard cut mid-word."""
    s = HCL_RE.sub("my current role", s).strip()
    if len(s) <= LINKEDIN_LIMIT:
        return s
    tail = " - " + CANDIDATE["first"]
    budget = LINKEDIN_LIMIT - len(tail)
    body = s.rsplit(" - " + CANDIDATE["first"], 1)[0][:budget].rsplit(" ", 1)[0].rstrip(" ,.-")
    return body + tail


def linkedin_note(app, contact_type="recruiter", contact_first="", shared_uh=False):
    """A <=300-char connection note tuned to who you're reaching. contact_type in
    {recruiter, hiring_manager, peer}. shared_uh=True adds the UH-alum warmth."""
    c = CANDIDATE
    role = (app or {}).get("title", "the role")
    company = (app or {}).get("company", "your team")
    who = contact_first or "there"
    uh = f" and a fellow {c['school']} alum" if shared_uh else ""
    lead = f"Hi {who} - I just applied for the {role} role at {company} and would love to connect."
    ident = f"I'm a {c['base']}-based cloud/DevOps engineer ({c['creds']}, {c['stack']}){uh}"
    tail_by_type = {
        "recruiter": "would really value being on your radar. Thanks!",
        "hiring_manager": "would value connecting with someone leading the team. Thanks!",
        "peer": "would value connecting with someone on the team. Thanks!",
    }
    tail = tail_by_type.get(contact_type, tail_by_type["recruiter"])
    note = f"{lead} {ident} - {tail} - {c['first']}"
    return _fit(note)


EMAIL_TEMPLATES = {
    "recruiter": {
        "subject": "Application — {role} ({company})",
        "opening": "Hi {who},\n\nI just applied for the {role} role at {company} and wanted to introduce myself directly.",
    },
    "referral": {
        "subject": "Quick ask — referral for {role} at {company}?",
        "opening": "Hi {who},\n\nI'm applying for the {role} role at {company} and noticed you're on the team. If it looks like a fit, I'd be grateful for a referral.",
    },
    "cold": {
        "subject": "{role} at {company} — Houston-based cloud/DevOps engineer",
        "opening": "Hi {who},\n\nI'm reaching out about the {role} role at {company}. I know a cold note is a long shot, so I'll be brief.",
    },
}


def application_email(app, kind="recruiter", contact_first="", fit_points=None):
    """A formal, paste-ready application email. Draft-only. fit_points is an optional list
    of 2-3 role-specific bullets (else a sensible default from the candidate's stack)."""
    c = CANDIDATE
    tmpl = EMAIL_TEMPLATES.get(kind, EMAIL_TEMPLATES["recruiter"])
    role = (app or {}).get("title", "the role")
    company = (app or {}).get("company", "your team")
    who = contact_first or "there"
    fields = {"role": role, "company": company, "who": who}
    points = fit_points or [
        f"Hands-on {c['stack']} — production cloud operations plus AWS projects built end-to-end in Terraform with security-gated CI/CD.",
        f"{c['creds']} in Computer & Systems Engineering; comfortable owning infrastructure end-to-end.",
    ]
    body = (
        tmpl["opening"].format(**fields)
        + "\n\nWhy I think I'm a fit:\n"
        + "\n".join(f"- {p}" for p in points[:3])
        + f"\n\nMy résumé is attached, and more of my work is at {c['site']}. "
        "I'd welcome a few minutes to talk. Thank you for your time.\n\n"
        f"Best,\n{c['first']} Rajolu"
    )
    return {
        "subject": tmpl["subject"].format(**fields),
        "body": body,
        "attachmentChecklist": ["Tailored résumé PDF (this role)",
                                "Cover letter (optional)",
                                "Link: " + c["site"]],
        "note": "Draft only — review, attach your tailored résumé, and send it yourself.",
    }


def drafts_for(app, contact_first="", contact_type="recruiter", shared_uh=False):
    """Everything for one contact: the LinkedIn note + the matching email, in one call."""
    email_kind = {"recruiter": "recruiter", "hiring_manager": "recruiter", "peer": "referral"}.get(contact_type, "cold")
    return {
        "linkedinNote": linkedin_note(app, contact_type, contact_first, shared_uh),
        "email": application_email(app, email_kind, contact_first),
        "contactType": contact_type,
    }


def handler(event, _ctx):
    try:
        body = json.loads(event.get("body") or "{}")
    except (ValueError, TypeError):
        body = {}
    app = body.get("app") or {}
    res = drafts_for(app, body.get("contactFirst", ""),
                     body.get("contactType", "recruiter"), bool(body.get("sharedUH")))
    return {"statusCode": 200, "headers": {"Content-Type": "application/json"},
            "body": json.dumps(res)}
