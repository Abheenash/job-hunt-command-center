"""Scanner Lambda — the ingest edge of the event-driven inbox pipeline.

EventBridge invokes this every 6 hours. It reads recent mail over read-only IMAP
(Google App Password in Secrets Manager), drops anything already processed
(idempotency via the email-events table) or obviously non-job (a cheap keyword /
ATS-domain pre-filter), and hands each surviving candidate to SQS as one message.

It does NO classification and touches NO application data — that happens
downstream in the Step Functions workflow. Keeping the expensive Bedrock +
DynamoDB work off this function means a slow mailbox or a Bedrock hiccup can't
stall ingestion, and every email is retried and DLQ-isolated independently.

If the secret is still a placeholder (no real credential yet) it logs and no-ops,
so the schedule stays harmless until the one-time credential is added.
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

ddb = boto3.client("dynamodb")
secrets = boto3.client("secretsmanager")
sqs = boto3.client("sqs")

EVENTS = os.environ["EVENTS_TABLE"]
SECRET_ID = os.environ["SECRET_ID"]
QUEUE_URL = os.environ["QUEUE_URL"]
LOOKBACK_DAYS = int(os.environ.get("LOOKBACK_DAYS", "3"))

# Cheap pre-filter: only enqueue emails that plausibly touch a job search, so
# Bedrock is never spent on newsletters / receipts / CI noise downstream.
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
        return {"queued": 0, "configured": False}

    messages = _fetch_recent(cred)
    seen = _existing_event_ids()   # idempotency: don't re-enqueue the same email
    queued = 0
    for msg in messages:
        eid = _eid(msg)
        if eid in seen:
            continue
        # cheap pre-filter: skip obvious non-job mail before it costs a Bedrock call
        if not _maybe_job(msg):
            continue
        seen.add(eid)
        msg["eventId"] = eid
        sqs.send_message(QueueUrl=QUEUE_URL, MessageBody=json.dumps(msg))
        queued += 1
    print(f"scanned {len(messages)} msgs, queued {queued} candidate(s) for processing")
    return {"scanned": len(messages), "queued": queued, "configured": True}


def _maybe_job(msg):
    if _JOB_HINT.search(f"{msg.get('subject', '')} {msg.get('snippet', '')}"):
        return True
    dom = (msg.get("from", "").split("@")[-1] or "").lower()
    return any(a in dom for a in _ATS)


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
            try:
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
            except Exception as e:  # noqa: BLE001 — a single odd email never sinks the whole scan
                print(f"skip email {mid}: {type(e).__name__}: {e}")
                continue
    finally:
        try:
            imap.logout()
        except Exception:  # noqa: BLE001
            pass
    return out


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


def _safe_bytes_decode(b, enc):
    """Decode email bytes even when the declared charset is unknown/invalid. Some servers
    label parts 'unknown-8bit' (RFC 1428) or use bogus charsets that aren't in Python's codec
    registry — bytes.decode() then raises LookupError at codec lookup (before 'ignore' applies).
    Fall back through utf-8 then latin-1 (which never fails), so one odd email can't sink a scan."""
    for cand in (enc, "utf-8", "latin-1"):
        if not cand:
            continue
        try:
            return b.decode(cand, "ignore")
        except (LookupError, UnicodeDecodeError):
            continue
    return b.decode("latin-1", "ignore")


def _decode(s):
    out = ""
    try:
        parts = decode_header(s)
    except Exception:  # noqa: BLE001 — malformed header → return the raw string
        return s if isinstance(s, str) else ""
    for text, enc in parts:
        out += _safe_bytes_decode(text, enc) if isinstance(text, bytes) else text
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
