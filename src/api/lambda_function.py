"""Job Hunt Command Center — CRUD API.

One Lambda behind an HTTP API `$default` route (Cognito-JWT protected). It routes
internally on method + path:

  POST   /applications                       create
  GET    /applications                       list (this user's)
  GET    /applications/{id}                  read
  PUT    /applications/{id}                  update
  DELETE /applications/{id}                  delete
  GET    /applications/{id}/events           classified inbox events for an app
  POST   /applications/{id}/documents        -> presigned PUT (upload the as-sent file)
  GET    /download?key=<docKey>              -> presigned GET (download a snapshot)

Each application is stored as a JSON `body` string keyed by appId and stamped with
the caller's Cognito `sub` (userId), so the store is already multi-user-ready.
"""
import json
import os
import time
import uuid

import boto3
from botocore.config import Config

s3 = boto3.client("s3", config=Config(signature_version="s3v4"))
ddb = boto3.client("dynamodb")
bedrock = boto3.client("bedrock-runtime")

BEDROCK_MODEL = "us.anthropic.claude-haiku-4-5-20251001-v1:0"

JD_SYSTEM = (
    "You extract structured fields from a job description for an application "
    "tracker. Reply with ONLY a compact JSON object, no prose, no code fences. "
    "Keys (use empty string/array if absent — never invent facts not in the text): "
    '{"company":str,"title":str,"location":str,"state":str (2-letter US code, or '
    '"Remote", or ""),"workMode":str ("Remote"|"Hybrid"|"On-site"|""),"salary":str,'
    '"seniority":str,"tags":str (comma-separated 3-6 keywords),'
    '"requiredSkills":str (comma-separated),"niceToHave":str (comma-separated)}'
)

MATCH_SYSTEM = (
    "You are a technical recruiter comparing a candidate's résumé to a job "
    "description. Judge fit honestly and strictly from the two texts. Reply with "
    "ONLY a compact JSON object, no prose, no code fences: "
    '{"matchPercent":int 0-100,"matched":[up to 8 requirements the résumé clearly '
    'satisfies],"missing":[up to 8 important JD requirements absent or weak in the '
    'résumé],"summary":"1-2 sentence honest assessment and the single biggest gap"}'
)


class NotFound(Exception):
    pass

APPS = os.environ["APPS_TABLE"]
EVENTS = os.environ["EVENTS_TABLE"]
BUCKET = os.environ["DOCS_BUCKET"]
PRESIGN_TTL = 300


def handler(event, _ctx):
    method = event["requestContext"]["http"]["method"]
    path = event.get("rawPath", "/")
    parts = [p for p in path.split("/") if p]
    try:
        user = event["requestContext"]["authorizer"]["jwt"]["claims"]["sub"]
    except (KeyError, TypeError):
        return _r(401, {"error": "unauthorized"})

    qs = event.get("queryStringParameters") or {}
    body = _parse_body(event)

    try:
        # /download?key=...
        if parts[:1] == ["download"] and method == "GET":
            return download_url(user, qs.get("key", ""))

        # /parse-jd  (AI field extraction)
        if parts[:1] == ["parse-jd"] and method == "POST":
            return parse_jd(body)

        if parts[:1] == ["applications"]:
            if len(parts) == 1:
                if method == "POST":
                    return create_app(user, body)
                if method == "GET":
                    return list_apps(user)
            elif len(parts) == 2:
                app_id = parts[1]
                if method == "GET":
                    return get_app(user, app_id)
                if method == "PUT":
                    return update_app(user, app_id, body)
                if method == "DELETE":
                    return delete_app(user, app_id)
            elif len(parts) == 3 and parts[2] == "events" and method == "GET":
                return list_events(user, parts[1])
            elif len(parts) == 3 and parts[2] == "documents" and method == "POST":
                return upload_url(user, parts[1], body)
            elif len(parts) == 3 and parts[2] == "match" and method == "POST":
                return match_resume(user, parts[1])
        return _r(404, {"error": "not found"})
    except NotFound:
        return _r(404, {"error": "application not found"})
    except PermissionError:
        return _r(403, {"error": "forbidden"})
    except Exception as e:  # noqa: BLE001
        print(f"error: {type(e).__name__}: {e}")
        return _r(500, {"error": "internal error"})


# --- applications ------------------------------------------------------------

def create_app(user, data):
    now = int(time.time())
    app_id = str(uuid.uuid4())
    record = dict(data or {})
    record.update({"appId": app_id, "userId": user, "createdAt": now, "updatedAt": now})
    record.setdefault("status", "applied")
    record.setdefault("timeline", [{"at": now, "event": "logged application"}])
    _put(app_id, user, record)
    return _r(201, record)


def list_apps(user):
    items = []
    kwargs = {"TableName": APPS, "FilterExpression": "userId = :u",
              "ExpressionAttributeValues": {":u": {"S": user}}}
    while True:
        resp = ddb.scan(**kwargs)
        items += [json.loads(i["body"]["S"]) for i in resp.get("Items", [])]
        if "LastEvaluatedKey" not in resp:
            break
        kwargs["ExclusiveStartKey"] = resp["LastEvaluatedKey"]
    items.sort(key=lambda a: a.get("updatedAt", 0), reverse=True)
    return _r(200, {"applications": items})


def get_app(user, app_id):
    return _r(200, _owned(user, app_id))


def update_app(user, app_id, data):
    record = _owned(user, app_id)
    record.update(data or {})
    record.update({"appId": app_id, "userId": user, "updatedAt": int(time.time())})
    _put(app_id, user, record)
    return _r(200, record)


def delete_app(user, app_id):
    _owned(user, app_id)  # ownership check
    ddb.delete_item(TableName=APPS, Key={"appId": {"S": app_id}})
    return _r(200, {"deleted": app_id})


def list_events(user, app_id):
    _owned(user, app_id)
    resp = ddb.query(TableName=EVENTS, IndexName="byApp",
                     KeyConditionExpression="appId = :a",
                     ExpressionAttributeValues={":a": {"S": app_id}})
    events = [json.loads(i["body"]["S"]) for i in resp.get("Items", [])]
    events.sort(key=lambda e: e.get("receivedAt", 0), reverse=True)
    return _r(200, {"events": events})


# --- documents (presigned URLs) ----------------------------------------------

def upload_url(user, app_id, data):
    _owned(user, app_id)
    filename = (data or {}).get("filename", "document")
    kind = (data or {}).get("kind", "resume")
    safe = "".join(c for c in filename if c.isalnum() or c in "._- ").strip() or "document"
    key = f"documents/{user}/{app_id}/{uuid.uuid4()}-{safe}"
    url = s3.generate_presigned_url("put_object",
                                    Params={"Bucket": BUCKET, "Key": key}, ExpiresIn=PRESIGN_TTL)
    return _r(200, {"uploadUrl": url, "docKey": key, "kind": kind, "filename": filename})


def download_url(user, key):
    # Ownership is enforced by the key prefix: documents/<user>/...
    if not key or not key.startswith(f"documents/{user}/"):
        raise PermissionError()
    filename = key.rsplit("-", 1)[-1] or "document"
    url = s3.generate_presigned_url(
        "get_object",
        Params={"Bucket": BUCKET, "Key": key,
                "ResponseContentDisposition": f'attachment; filename="{filename}"'},
        ExpiresIn=PRESIGN_TTL)
    return _r(200, {"downloadUrl": url})


# --- helpers -----------------------------------------------------------------

def _put(app_id, user, record):
    ddb.put_item(TableName=APPS, Item={
        "appId": {"S": app_id},
        "userId": {"S": user},
        "updatedAt": {"N": str(record.get("updatedAt", int(time.time())))},
        "body": {"S": json.dumps(record)},
    })


def _owned(user, app_id):
    item = ddb.get_item(TableName=APPS, Key={"appId": {"S": app_id}}).get("Item")
    if not item:
        raise NotFound()
    record = json.loads(item["body"]["S"])
    if record.get("userId") != user:
        raise PermissionError()
    return record


def parse_jd(body):
    """Extract structured application fields from a pasted job description."""
    jd = str((body or {}).get("jd") or "").strip()
    if len(jd) < 20:
        return _r(400, {"error": "paste a longer job description"})
    payload = {
        "anthropic_version": "bedrock-2023-05-31",
        "max_tokens": 600,
        "system": JD_SYSTEM,
        "messages": [{"role": "user", "content": [{"type": "text", "text": jd[:12000]}]}],
    }
    try:
        resp = bedrock.invoke_model(modelId=BEDROCK_MODEL, body=json.dumps(payload))
        text = json.loads(resp["body"].read())["content"][0]["text"]
        fields = json.loads(_first_json(text))
    except Exception as e:  # noqa: BLE001
        print(f"parse-jd failed: {type(e).__name__}: {e}")
        return _r(502, {"error": "couldn't parse that JD, try again"})
    # keep only known keys
    allowed = {"company", "title", "location", "state", "workMode", "salary",
               "seniority", "tags", "requiredSkills", "niceToHave"}
    return _r(200, {"fields": {k: v for k, v in fields.items() if k in allowed}})


def _first_json(text):
    start = text.find("{")
    end = text.rfind("}")
    return text[start:end + 1] if start != -1 and end != -1 else "{}"


def match_resume(user, app_id):
    """Compare the application's JD against the uploaded résumé and score fit."""
    item = _owned(user, app_id)
    jd = item.get("jd", "")
    if len(jd) < 20:
        return _r(400, {"error": "add the job description to this application first"})
    docs = item.get("documents", [])
    resume = next((d for d in docs if d.get("kind") == "resume"), docs[0] if docs else None)
    if not resume:
        return _r(400, {"error": "attach the résumé you applied with first"})
    text = _pdf_text(resume["docKey"])
    if len(text) < 30:
        return _r(422, {"error": "couldn't read text from that PDF (is it a scan/image?)"})

    payload = {
        "anthropic_version": "bedrock-2023-05-31", "max_tokens": 700, "system": MATCH_SYSTEM,
        "messages": [{"role": "user", "content": [{"type": "text",
            "text": f"JOB DESCRIPTION:\n{jd[:8000]}\n\nRESUME:\n{text[:8000]}"}]}],
    }
    try:
        resp = bedrock.invoke_model(modelId=BEDROCK_MODEL, body=json.dumps(payload))
        out = json.loads(resp["body"].read())["content"][0]["text"]
        result = json.loads(_first_json(out))
    except Exception as e:  # noqa: BLE001
        print(f"match failed: {type(e).__name__}: {e}")
        return _r(502, {"error": "match check failed, try again"})

    # persist a lightweight summary on the record so cards/detail can show it
    item["matchPercent"] = int(result.get("matchPercent") or 0)
    item["matchSummary"] = str(result.get("summary") or "")[:400]
    item["matchMatched"] = result.get("matched") or []
    item["matchMissing"] = result.get("missing") or []
    item["matchedAt"] = int(time.time())
    item["updatedAt"] = int(time.time())
    _put(app_id, user, item)
    return _r(200, {"match": result})


def _pdf_text(key):
    try:
        import io
        import pypdf
        obj = s3.get_object(Bucket=BUCKET, Key=key)
        reader = pypdf.PdfReader(io.BytesIO(obj["Body"].read()))
        return "\n".join((p.extract_text() or "") for p in reader.pages)
    except Exception as e:  # noqa: BLE001
        print(f"pdf_text failed: {type(e).__name__}: {e}")
        return ""


def _parse_body(event):
    raw = event.get("body")
    if not raw:
        return {}
    if event.get("isBase64Encoded"):
        import base64
        raw = base64.b64decode(raw).decode()
    try:
        return json.loads(raw)
    except (TypeError, ValueError):
        return {}


def _r(status, body):
    return {"statusCode": status, "headers": {"Content-Type": "application/json"},
            "body": json.dumps(body)}
