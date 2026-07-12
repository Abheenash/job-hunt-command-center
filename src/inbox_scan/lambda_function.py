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
import imaplib
import json
import os
import time
import uuid
from datetime import datetime, timedelta, timezone
from email.header import decode_header

import boto3

from classifier import classify

ddb = boto3.client("dynamodb")
secrets = boto3.client("secretsmanager")

APPS = os.environ["APPS_TABLE"]
EVENTS = os.environ["EVENTS_TABLE"]
SECRET_ID = os.environ["SECRET_ID"]
LOOKBACK_DAYS = int(os.environ.get("LOOKBACK_DAYS", "3"))


def handler(event, _ctx):
    cred = _load_credential()
    if not cred:
        print("no email credential configured yet — skipping scan (harmless no-op).")
        return {"scanned": 0, "configured": False}

    apps = _load_applications()
    companies = [a.get("company") for a in apps]
    by_email = {(a.get("contactEmail") or "").lower(): a["appId"] for a in apps if a.get("contactEmail")}

    messages = _fetch_recent(cred)
    linked = 0
    for msg in messages:
        category, confidence, _hits = classify(msg["subject"], msg["snippet"], msg["from"])
        if category == "other":
            continue
        app_id = by_email.get(_addr(msg["from"]).lower()) or _match_company(msg, companies, apps)
        _write_event(app_id, msg, category, confidence)
        linked += 1
    print(f"scanned {len(messages)} messages, recorded {linked} job-related events")
    return {"scanned": len(messages), "recorded": linked, "configured": True}


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
                "receivedAt": int(time.time()),
            })
    finally:
        try:
            imap.logout()
        except Exception:  # noqa: BLE001
            pass
    return out


def _write_event(app_id, msg, category, confidence):
    ddb.put_item(TableName=EVENTS, Item={
        "eventId": {"S": str(uuid.uuid4())},
        "appId": {"S": app_id or "unmatched"},
        "body": {"S": json.dumps({
            "from": msg["from"], "subject": msg["subject"],
            "snippet": msg["snippet"][:200], "category": category,
            "confidence": confidence, "receivedAt": msg["receivedAt"],
            "appId": app_id or "unmatched",
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
