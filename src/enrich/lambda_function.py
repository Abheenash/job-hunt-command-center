"""Enrich task — second state of the 'process-email' Step Functions workflow.

Takes the classified email, matches it to an existing application (by recruiter
contact-email, then by the AI-extracted company name), auto-advances the status
(forward-only; rejection/offer override) and enriches fields (recruiter, pay,
location, interview date), then writes the deduped email-event either way.

It NEVER creates a new application — a human still owns every "apply". Matching is
AI-extraction-first (company/recruiter come from Classify), so no keyword matcher
is needed here.

Input  (state input): {"msg": <email>, "classification": <triage JSON>}
Output (state result): {"eventId", "linked", "appId", "action", "category"}
"""
import hashlib
import json
import os
import re
import time

import boto3

ddb = boto3.client("dynamodb")

APPS = os.environ["APPS_TABLE"]
EVENTS = os.environ["EVENTS_TABLE"]

# Status progression rank — auto-updates only advance forward (rejection/offer override).
STATUS_RANK = {"applied": 0, "screen": 1, "interview": 2, "offer": 3}

# Shared / generic ATS senders. EVERY application on the same ATS shares one of these
# (all Greenhouse confirmations come from no-reply@us.greenhouse.io, all Ashby ones from
# no-reply@ashbyhq.com, etc.). They must NEVER be stored as an app's contactEmail or used
# to match a later email — otherwise the first app to capture the address hijacks every
# subsequent email from that ATS (the company-mislabel bug: Reddit's confirmation linking
# to a Pinterest app, Baseten's to Snowflake). For these, match by the AI-extracted company
# from the (clear) subject line instead.
GENERIC_LOCALPART_RE = re.compile(
    r"^(no-?reply|do-?not-?reply|donotreply|noreply|notification|notifications|"
    r"jobs|careers|career|talent|recruiting|recruitment|mailer|mail|hello|info|"
    r"support|help|team|hr|people)([.\-_+]|$)", re.I)
ATS_DOMAIN_RE = re.compile(
    r"(greenhouse(-mail)?\.io|ashbyhq\.com|lever\.co|myworkday(jobs)?\.com|workday\.com|"
    r"smartrecruiters\.com|icims\.com|pageuppeople\.com|oraclecloud\.com|workflow\.mail|"
    r"jobvite\.com|breezy\.hr|bamboohr\.com|successfactors\.com|taleo\.net|eightfold\.ai|"
    r"paylocity\.com|dayforcehcm\.com|workablemail\.com|hire\.lever\.co|rippling\.com)$", re.I)


def _is_generic_sender(addr):
    """True if this is a shared/no-reply/ATS address that must not be stored or matched on."""
    a = (addr or "").strip().lower()
    if "@" not in a:
        return True
    local, _, domain = a.partition("@")
    return bool(GENERIC_LOCALPART_RE.match(local) or ATS_DOMAIN_RE.search(domain))


def handler(event, _ctx):
    msg = event["msg"]
    r = event["classification"]
    eid = msg.get("eventId") or _eid(msg)
    category = r.get("category", "other")

    if not r.get("jobRelated") or category == "other":
        # record (deduped) so this email isn't re-read on the next scan
        _write_event(eid, "unmatched", msg, "other", r.get("confidence", 0), "", r.get("summary", ""))
        return {"eventId": eid, "linked": False, "appId": "unmatched", "action": "", "category": "other"}

    apps = _load_applications()
    sender = _addr(msg.get("from", "")).lower()
    # Match by recruiter email ONLY when it's a real person address — a shared ATS no-reply
    # collides across every app on that ATS. For generic senders, go straight to matching by
    # the AI-extracted company (the subject names the company clearly on confirmations).
    app_id = None
    if not _is_generic_sender(sender):
        by_email = {(a.get("contactEmail") or "").lower(): a["appId"]
                    for a in apps if a.get("contactEmail") and not _is_generic_sender(a.get("contactEmail"))}
        app_id = by_email.get(sender)
    if not app_id:
        app_id = _match_by_name(r.get("company"), apps)

    action = _apply_to_app(app_id, category, msg, r) if app_id else ""
    _write_event(eid, app_id, msg, category, r.get("confidence", 0.7), action, r.get("summary", ""))
    return {"eventId": eid, "linked": bool(app_id), "appId": app_id or "unmatched",
            "action": action, "category": category}


def _match_by_name(company, apps):
    c = (company or "").strip().lower()
    if len(c) < 2:
        return None
    # Exact match wins first — so "Reddit" binds to the Reddit app and is never grabbed by a
    # loose substring of some other application seen earlier in the scan.
    for a in apps:
        if (a.get("company") or "").strip().lower() == c:
            return a["appId"]
    if len(c) < 4:                       # too short for safe substring matching
        return None
    for a in apps:                       # then containment either direction ("Amazon" vs "Amazon Web Services")
        ac = (a.get("company") or "").strip().lower()
        if ac and (c in ac or ac in c):
            return a["appId"]
    return None


def _apply_to_app(app_id, category, msg, r):
    """Cross-check the matched application: auto-advance status AND enrich fields
    from the email (recruiter, pay, location, interview date). Never creates a new
    application. Fills missing fields; updates pay if the email states a different
    one. Returns a combined description of changes, or '' if none."""
    item = ddb.get_item(TableName=APPS, Key={"appId": {"S": app_id}}).get("Item")
    if not item:
        return ""
    rec = json.loads(item["body"]["S"])
    cur = rec.get("status", "applied")
    changes = []

    # 1) status progression (forward-only; rejection/offer override)
    if category == "confirmation":
        if not rec.get("confirmed"):
            rec["confirmed"] = True
            changes.append("verified submitted")
    elif category == "rejection":
        if cur != "rejected":
            rec["status"] = "rejected"
            changes.append("moved to rejected")
    elif category == "offer":
        if cur not in ("offer", "rejected"):
            rec["status"] = "offer"
            changes.append("moved to offer")
    elif category == "interview":
        if STATUS_RANK.get(cur, 0) < STATUS_RANK["interview"]:
            rec["status"] = "interview"
            changes.append("moved to interview")
    elif category == "recruiter_reply":
        if STATUS_RANK.get(cur, 0) < STATUS_RANK["screen"]:
            rec["status"] = "screen"
            changes.append("moved to screen")

    # 2) enrich — fill missing fields; update pay if the email differs
    sender = _addr(msg.get("from", ""))
    # Only store a REAL recruiter address — never a shared ATS no-reply (storing it would
    # make this app hijack every future email from that ATS).
    if sender and "@" in sender and not _is_generic_sender(sender) and not rec.get("contactEmail"):
        rec["contactEmail"] = sender
        changes.append("added recruiter email")
    rn = (r.get("recruiterName") or "").strip()
    if rn and not rec.get("contactName"):
        rec["contactName"] = rn[:80]
        changes.append(f"recruiter {rn[:40]}")
    sal = (r.get("salary") or "").strip()
    if sal and sal.lower() not in ("none", "n/a", "not specified") and sal != rec.get("salary"):
        changes.append(f"pay {'→ ' + sal if rec.get('salary') else 'set ' + sal}")
        rec["salary"] = sal[:60]
    loc = (r.get("location") or "").strip()
    if loc and not rec.get("location"):
        rec["location"] = loc[:80]
        changes.append("added location")
    wm = (r.get("workMode") or "").strip()
    if wm in ("Remote", "Hybrid", "On-site") and not rec.get("workMode"):
        rec["workMode"] = wm
    idate = (r.get("interviewDate") or "").strip()
    if category == "interview" and _is_date(idate) and idate != rec.get("nextDue"):
        rec["nextDue"] = idate
        rec.setdefault("nextAction", "Prepare for interview")
        changes.append(f"interview {idate}")

    if not changes:
        return ""
    now = int(time.time())
    events = rec.get("timeline") or []
    subj = (msg.get("subject") or "")[:70]
    for ch in changes:
        events.append({"at": now, "event": f"Auto: {ch} — email “{subj}”"})
    rec["timeline"] = events[-60:]
    rec["updatedAt"] = now
    ddb.put_item(TableName=APPS, Item={
        "appId": {"S": app_id}, "userId": {"S": rec.get("userId", "")},
        "updatedAt": {"N": str(now)}, "body": {"S": json.dumps(rec)},
    })
    return "; ".join(changes)


def _write_event(eid, app_id, msg, category, confidence, action="", summary=""):
    ddb.put_item(TableName=EVENTS, Item={
        "eventId": {"S": eid},
        "appId": {"S": app_id or "unmatched"},
        "body": {"S": json.dumps({
            "from": msg["from"], "subject": msg["subject"],
            "snippet": msg["snippet"][:200], "category": category,
            "confidence": confidence, "receivedAt": msg["receivedAt"],
            "appId": app_id or "unmatched", "action": action, "summary": summary,
        })},
    })


def _load_applications():
    apps, kwargs = [], {"TableName": APPS}
    while True:
        r = ddb.scan(**kwargs)
        apps += [json.loads(i["body"]["S"]) for i in r.get("Items", [])]
        if "LastEvaluatedKey" not in r:
            break
        kwargs["ExclusiveStartKey"] = r["LastEvaluatedKey"]
    return apps


def _is_date(s):
    return bool(re.match(r"^\d{4}-\d{2}-\d{2}$", s or ""))


def _eid(msg):
    key = msg.get("messageId") or f"{msg.get('from', '')}|{msg.get('subject', '')}"
    return hashlib.sha1(key.encode("utf-8", "ignore")).hexdigest()


def _addr(from_header):
    if "<" in from_header and ">" in from_header:
        return from_header.split("<", 1)[1].split(">", 1)[0]
    return from_header.strip()
