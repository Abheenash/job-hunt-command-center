"""nudge Lambda — reminds you about applications going stale.

On a daily EventBridge schedule, it finds applications still in an early stage
(applied/screen) with no update in N days and emails you a digest via SES, so
follow-ups don't fall through the cracks. Single-recipient (the owner), so SES
sandbox is fine.
"""
import json
import os
import time

import boto3

ddb = boto3.client("dynamodb")
ses = boto3.client("ses")

APPS = os.environ["APPS_TABLE"]
SENDER = os.environ["SES_SENDER"]
OWNER = os.environ["OWNER_EMAIL"]
STALE_DAYS = int(os.environ.get("STALE_DAYS", "7"))
STALE_STATUSES = {"applied", "screen"}


def handler(event, _ctx):
    now = int(time.time())
    cutoff = now - STALE_DAYS * 86400
    stale = []
    for a in _scan_apps():
        if a.get("status") in STALE_STATUSES and int(a.get("updatedAt", now)) < cutoff:
            days = (now - int(a.get("updatedAt", now))) // 86400
            stale.append((days, a))
    if not stale:
        print("no stale applications — nothing to nudge")
        return {"stale": 0}

    stale.sort(reverse=True)
    lines = [f"• {a.get('company','?')} — {a.get('title','?')} "
             f"({a.get('status')}, {days}d quiet)" +
             (f" · next: {a.get('nextAction')}" if a.get("nextAction") else "")
             for days, a in stale]
    body = ("Applications that have gone quiet — time for a follow-up:\n\n"
            + "\n".join(lines)
            + "\n\nOpen your Command Center to update them.")
    try:
        ses.send_email(
            Source=SENDER,
            Destination={"ToAddresses": [OWNER]},
            Message={"Subject": {"Data": f"⏰ {len(stale)} application(s) need a nudge"},
                     "Body": {"Text": {"Data": body}}},
        )
        print(f"nudged about {len(stale)} stale applications")
    except Exception as e:  # noqa: BLE001
        print(f"SES send failed ({type(e).__name__}): {e}")
    return {"stale": len(stale)}


def _scan_apps():
    apps, kwargs = [], {"TableName": APPS}
    while True:
        r = ddb.scan(**kwargs)
        apps += [json.loads(i["body"]["S"]) for i in r.get("Items", [])]
        if "LastEvaluatedKey" not in r:
            break
        kwargs["ExclusiveStartKey"] = r["LastEvaluatedKey"]
    return apps
