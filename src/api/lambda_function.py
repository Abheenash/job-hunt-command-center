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
