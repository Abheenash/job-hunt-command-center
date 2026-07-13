"""Weekly digest Lambda — a Monday-morning summary of the whole search via SES.

EventBridge triggers it weekly. It scans every application and emails a concise
pipeline snapshot: counts by stage, response rate, follow-ups due this week, and
stale applications worth a nudge — so the search stays on the rails without opening
the dashboard. Read-only over DynamoDB; sends one email to the owner.
"""
import json
import os
import time
from datetime import datetime, timezone

import boto3

ddb = boto3.client("dynamodb")
ses = boto3.client("ses")

APPS = os.environ["APPS_TABLE"]
SENDER = os.environ["SES_SENDER"]
OWNER = os.environ["OWNER_EMAIL"]
DASH = os.environ.get("DASH_URL", "https://jobs.abheenash.com")
STALE_DAYS = int(os.environ.get("STALE_DAYS", "10"))

ACTIVE = ("applied", "screen", "interview")
RESPONDED = ("screen", "interview", "offer", "rejected")


def handler(event, _ctx):
    apps = _load_apps()
    if not apps:
        print("no applications — skipping digest")
        return {"sent": False, "reason": "no apps"}

    now = int(time.time())
    today = datetime.now(timezone.utc).date()
    by = lambda s: sum(1 for a in apps if a.get("status") == s)  # noqa: E731
    total = len(apps)
    active = sum(1 for a in apps if a.get("status") in ACTIVE)
    responded = sum(1 for a in apps if a.get("status") in RESPONDED)
    rate = round(100 * responded / total) if total else 0

    due, stale = [], []
    for a in apps:
        nd = a.get("nextDue")
        if nd and _within(nd, today, 7) and a.get("status") in ACTIVE:
            due.append(a)
        upd = a.get("updatedAt") or a.get("createdAt") or now
        if a.get("status") == "applied" and (now - upd) > STALE_DAYS * 86400:
            stale.append(a)

    subject = f"📋 Job search — {active} active · {by('interview')} interview · {rate}% response"
    html = _html(total, active, by, rate, due, stale)
    ses.send_email(
        Source=SENDER,
        Destination={"ToAddresses": [OWNER]},
        Message={"Subject": {"Data": subject},
                 "Body": {"Html": {"Data": html}, "Text": {"Data": _text(total, active, by, rate, due, stale)}}},
    )
    print(f"digest sent: {total} apps, {len(due)} due, {len(stale)} stale")
    return {"sent": True, "total": total, "due": len(due), "stale": len(stale)}


def _within(date_str, today, days):
    try:
        d = datetime.strptime(date_str[:10], "%Y-%m-%d").date()
        delta = (d - today).days
        return -3 <= delta <= days  # slightly overdue through the next week
    except Exception:  # noqa: BLE001
        return False


def _row(a):
    co = a.get("company", "?")
    ti = a.get("title", "")
    nd = a.get("nextDue", "")
    na = a.get("nextAction", "")
    return f"<li><b>{_esc(co)}</b> — {_esc(ti)}{f' · <i>{_esc(na)}</i>' if na else ''}{f' (due {_esc(nd)})' if nd else ''}</li>"


def _html(total, active, by, rate, due, stale):
    def block(title, items):
        if not items:
            return ""
        return f"<h3 style='margin:16px 0 6px'>{title}</h3><ul style='margin:0;padding-left:18px'>{''.join(_row(a) for a in items[:15])}</ul>"
    stats = "".join(
        f"<td style='padding:8px 14px;text-align:center'><div style='font-size:22px;font-weight:700'>{v}</div>"
        f"<div style='font-size:11px;color:#5f6b7a;text-transform:uppercase'>{k}</div></td>"
        for k, v in [("Total", total), ("Active", active), ("Interview", by("interview")),
                     ("Offers", by("offer")), ("Response", f"{rate}%")])
    return (
        f"<div style='font-family:Arial,sans-serif;max-width:600px;margin:auto;color:#0f141a'>"
        f"<h2>Your weekly job-search digest</h2>"
        f"<table style='border-collapse:collapse;background:#f7f8fa;border-radius:10px'><tr>{stats}</tr></table>"
        f"{block('⏰ Follow-ups due this week', due) or '<p style=color:#5f6b7a>No follow-ups due this week. ✅</p>'}"
        f"{block(f'💤 Stale — applied &gt;{STALE_DAYS}d, no response', stale)}"
        f"<p style='margin-top:20px'><a href='{DASH}' style='background:#ec7211;color:#fff;padding:9px 16px;border-radius:8px;text-decoration:none'>Open the tracker →</a></p>"
        f"<p style='font-size:11px;color:#9aa5b1'>Sent weekly by your Job Hunt Command Center.</p></div>"
    )


def _text(total, active, by, rate, due, stale):
    lines = [f"Weekly digest: {total} apps · {active} active · {by('interview')} interview · {by('offer')} offers · {rate}% response", ""]
    if due:
        lines.append("Follow-ups due this week:")
        lines += [f"  - {a.get('company','?')} — {a.get('nextAction','') or a.get('title','')}" for a in due[:15]]
    if stale:
        lines.append(f"Stale (applied >{STALE_DAYS}d):")
        lines += [f"  - {a.get('company','?')} — {a.get('title','')}" for a in stale[:15]]
    lines += ["", DASH]
    return "\n".join(lines)


def _esc(s):
    return (str(s or "")).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _load_apps():
    apps, kwargs = [], {"TableName": APPS}
    while True:
        r = ddb.scan(**kwargs)
        apps += [json.loads(i["body"]["S"]) for i in r.get("Items", [])]
        if "LastEvaluatedKey" not in r:
            break
        kwargs["ExclusiveStartKey"] = r["LastEvaluatedKey"]
    return apps
