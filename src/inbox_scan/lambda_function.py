"""inbox-scan Lambda — reads recent mail, classifies it, links it to applications.

Runs on an EventBridge schedule. Reads via IMAP using a Google App Password kept
in Secrets Manager (chosen over Gmail-OAuth-in-Testing because App Passwords
don't expire every 7 days — see docs). Read-only: it never sends, moves, or
deletes mail. Data minimization: it stores the classification + subject + sender
+ a short snippet, never the full body.

If the secret is still a placeholder (no real credential yet), it logs and no-ops
so the schedule is harmless until the one-time credential is added.
"""
import email
import hashlib
import imaplib
import json
import os
import time
from datetime import datetime, timedelta, timezone
from email.header import decode_header

import re

import boto3

from classifier import classify  # keyword fallback if Bedrock is unavailable

ddb = boto3.client("dynamodb")
secrets = boto3.client("secretsmanager")
bedrock = boto3.client("bedrock-runtime")

APPS = os.environ["APPS_TABLE"]
EVENTS = os.environ["EVENTS_TABLE"]
SECRET_ID = os.environ["SECRET_ID"]
LOOKBACK_DAYS = int(os.environ.get("LOOKBACK_DAYS", "3"))
BEDROCK_MODEL = "us.anthropic.claude-haiku-4-5-20251001-v1:0"

# Bedrock reads the whole email and decides what it's actually about.
AI_CLASSIFY_SYSTEM = (
    "You triage one email for a job-application tracker. Read it and judge whether "
    "it is genuinely about THIS person's job search — i.e. an application confirmation, "
    "a recruiter reaching out, an interview invite/scheduling, a job offer, or a "
    "rejection. Newsletters, order receipts, bills, promotions, security alerts, "
    "CI/CD or app notifications, and personal mail are NOT job-related, even if they "
    "contain words like 'offer', 'application', or 'opportunity'. Reply with ONLY a "
    "compact JSON object, no prose: "
    '{"jobRelated":bool,"category":"interview"|"offer"|"rejection"|"recruiter_reply"'
    '|"confirmation"|"other","company":str (the hiring company, or ""),"role":str,'
    '"summary":str (one short sentence on what it is),"confidence":number 0-1}'
)

# Cheap pre-filter: only spend Bedrock on emails that plausibly touch a job search.
_JOB_HINT = re.compile(
    r"\b(?:appl(?:y|ied|ication|ying)|interview|recruit|recruiter|role|position|"
    r"candidate|hiring|opportunity|résumé|resume|offer|job|career|screening|"
    r"talent|onsite|assessment|coding|phone screen|hr team|next steps?)\b", re.I)
_ATS = ("greenhouse", "lever.co", "ashbyhq", "workday", "myworkday", "icims",
        "smartrecruiters", "jobvite", "taleo", "bamboohr", "workable")


def handler(event, _ctx):
    cred = _load_credential()
    if not cred:
        print("no email credential configured yet — skipping scan (harmless no-op).")
        return {"scanned": 0, "configured": False}

    apps = _load_applications()
    companies = [a.get("company") for a in apps]
    by_email = {(a.get("contactEmail") or "").lower(): a["appId"] for a in apps if a.get("contactEmail")}

    messages = _fetch_recent(cred)
    seen = _existing_event_ids()   # idempotency: don't re-process the same email
    linked, updated, ai_calls = 0, 0, 0
    for msg in messages:
        eid = _eid(msg)
        if eid in seen:
            continue
        # cheap pre-filter: skip obvious non-job mail without spending Bedrock
        if not _maybe_job(msg):
            continue
        seen.add(eid)
        ai_calls += 1
        r = _ai_classify(msg)  # Bedrock reads the email; keyword fallback on error
        category = r.get("category", "other")
        if not r.get("jobRelated") or category == "other":
            # record (deduped) so this email isn't re-read on the next scan
            _write_event(eid, "unmatched", msg, "other", r.get("confidence", 0), "", r.get("summary", ""))
            continue
        app_id = (by_email.get(_addr(msg["from"]).lower())
                  or _match_by_name(r.get("company"), apps)
                  or _match_company(msg, companies, apps))
        action = _apply_to_app(app_id, category, msg) if app_id else ""
        if action:
            updated += 1
        _write_event(eid, app_id, msg, category, r.get("confidence", 0.7), action, r.get("summary", ""))
        linked += 1
    print(f"scanned {len(messages)} msgs, {ai_calls} AI reads, {linked} findings, {updated} auto-updates")
    return {"scanned": len(messages), "recorded": linked, "updated": updated, "configured": True}


def _maybe_job(msg):
    if _JOB_HINT.search(f"{msg.get('subject', '')} {msg.get('snippet', '')}"):
        return True
    dom = (msg.get("from", "").split("@")[-1] or "").lower()
    return any(a in dom for a in _ATS)


def _ai_classify(msg):
    payload = {
        "anthropic_version": "bedrock-2023-05-31", "max_tokens": 300,
        "system": AI_CLASSIFY_SYSTEM,
        "messages": [{"role": "user", "content": [{"type": "text",
            "text": f"FROM: {msg.get('from', '')}\nSUBJECT: {msg.get('subject', '')}\n\nBODY:\n{(msg.get('snippet') or '')[:1500]}"}]}],
    }
    try:
        resp = bedrock.invoke_model(modelId=BEDROCK_MODEL, body=json.dumps(payload))
        text = json.loads(resp["body"].read())["content"][0]["text"]
        return json.loads(_first_json(text))
    except Exception as e:  # noqa: BLE001 — degrade to keyword classifier
        print(f"ai_classify fell back to keywords ({type(e).__name__}: {e})")
        cat, conf, _ = classify(msg.get("subject", ""), msg.get("snippet", ""), msg.get("from", ""))
        return {"jobRelated": cat != "other", "category": cat, "company": "", "summary": msg.get("subject", ""), "confidence": conf}


def _first_json(text):
    s, e = text.find("{"), text.rfind("}")
    return text[s:e + 1] if s != -1 and e != -1 else "{}"


def _match_by_name(company, apps):
    c = (company or "").strip().lower()
    if len(c) < 2:
        return None
    for a in apps:
        ac = (a.get("company") or "").strip().lower()
        if ac and (c == ac or c in ac or ac in c):
            return a["appId"]
    return None


# Status progression rank — auto-updates only advance forward (rejection/offer override).
STATUS_RANK = {"applied": 0, "screen": 1, "interview": 2, "offer": 3}


def _load_credential():
    try:
        raw = secrets.get_secret_value(SecretId=SECRET_ID)["SecretString"]
        c = json.loads(raw)
    except Exception as e:  # noqa: BLE001
        print(f"secret unreadable ({type(e).__name__}) — treating as unconfigured")
        return None
    if not c.get("email") or not c.get("app_password") or "REPLACE" in c.get("app_password", ""):
        return None
    c.setdefault("imap_host", "imap.gmail.com")
    return c


def _fetch_recent(cred):
    out = []
    since = (datetime.now(timezone.utc) - timedelta(days=LOOKBACK_DAYS)).strftime("%d-%b-%Y")
    imap = imaplib.IMAP4_SSL(cred["imap_host"])
    try:
        imap.login(cred["email"], cred["app_password"])
        imap.select("INBOX", readonly=True)
        _typ, data = imap.search(None, f'(SINCE {since})')
        ids = data[0].split()
        for mid in ids[-100:]:  # cap per run
            _typ, raw = imap.fetch(mid, "(RFC822)")
            if not raw or not raw[0]:
                continue
            m = email.message_from_bytes(raw[0][1])
            out.append({
                "from": _decode(m.get("From", "")),
                "subject": _decode(m.get("Subject", "")),
                "snippet": _body_snippet(m),
                "messageId": m.get("Message-ID", ""),
                "receivedAt": int(time.time()),
            })
    finally:
        try:
            imap.logout()
        except Exception:  # noqa: BLE001
            pass
    return out


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


def _eid(msg):
    """Stable per-email id (Message-ID) so re-scans don't duplicate or re-apply."""
    key = msg.get("messageId") or f"{msg.get('from', '')}|{msg.get('subject', '')}"
    return hashlib.sha1(key.encode("utf-8", "ignore")).hexdigest()


def _existing_event_ids():
    ids, kwargs = set(), {"TableName": EVENTS, "ProjectionExpression": "eventId"}
    while True:
        r = ddb.scan(**kwargs)
        ids.update(i["eventId"]["S"] for i in r.get("Items", []))
        if "LastEvaluatedKey" not in r:
            break
        kwargs["ExclusiveStartKey"] = r["LastEvaluatedKey"]
    return ids


def _apply_to_app(app_id, category, msg):
    """Cross-check the matched application and auto-advance it from the email.
    Forward-only (rejection/offer override). Returns a description, or '' if no change."""
    item = ddb.get_item(TableName=APPS, Key={"appId": {"S": app_id}}).get("Item")
    if not item:
        return ""
    rec = json.loads(item["body"]["S"])
    cur = rec.get("status", "applied")
    action = ""
    if category == "confirmation":
        if not rec.get("confirmed"):
            rec["confirmed"] = True
            action = "verified as submitted"
    elif category == "rejection":
        if cur != "rejected":
            rec["status"] = "rejected"
            action = "moved to rejected"
    elif category == "offer":
        if cur not in ("offer", "rejected"):
            rec["status"] = "offer"
            action = "moved to offer"
    elif category == "interview":
        if STATUS_RANK.get(cur, 0) < STATUS_RANK["interview"]:
            rec["status"] = "interview"
            action = "moved to interview"
    elif category == "recruiter_reply":
        if STATUS_RANK.get(cur, 0) < STATUS_RANK["screen"]:
            rec["status"] = "screen"
            action = "moved to screen"
    if not action:
        return ""
    now = int(time.time())
    events = rec.get("timeline") or []
    events.append({"at": now, "event": f"Auto: {action} — email “{(msg.get('subject') or '')[:80]}”"})
    rec["timeline"] = events[-50:]
    rec["updatedAt"] = now
    ddb.put_item(TableName=APPS, Item={
        "appId": {"S": app_id}, "userId": {"S": rec.get("userId", "")},
        "updatedAt": {"N": str(now)}, "body": {"S": json.dumps(rec)},
    })
    return action


def _load_applications():
    apps, kwargs = [], {"TableName": APPS}
    while True:
        r = ddb.scan(**kwargs)
        apps += [json.loads(i["body"]["S"]) for i in r.get("Items", [])]
        if "LastEvaluatedKey" not in r:
            break
        kwargs["ExclusiveStartKey"] = r["LastEvaluatedKey"]
    return apps


def _match_company(msg, companies, apps):
    from classifier import match_company
    name = match_company(msg["from"], msg["subject"], msg["snippet"], companies)
    if not name:
        return None
    for a in apps:
        if a.get("company") == name:
            return a["appId"]
    return None


def _decode(s):
    parts = decode_header(s)
    out = ""
    for text, enc in parts:
        out += text.decode(enc or "utf-8", "ignore") if isinstance(text, bytes) else text
    return out


def _body_snippet(m):
    if m.is_multipart():
        for part in m.walk():
            if part.get_content_type() == "text/plain":
                try:
                    return part.get_payload(decode=True).decode("utf-8", "ignore")[:500]
                except Exception:  # noqa: BLE001
                    return ""
        return ""
    try:
        return m.get_payload(decode=True).decode("utf-8", "ignore")[:500]
    except Exception:  # noqa: BLE001
        return ""


def _addr(from_header):
    if "<" in from_header and ">" in from_header:
        return from_header.split("<", 1)[1].split(">", 1)[0]
    return from_header.strip()
