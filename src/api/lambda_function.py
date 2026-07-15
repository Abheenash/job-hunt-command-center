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
import re
import time
import uuid

import boto3
from botocore.config import Config

import sponsorship as sp

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
    '"requiredSkills":str (comma-separated),"niceToHave":str (comma-separated),'
    '"attributes":[{"key":str,"value":str}] (2-5 JD-specific tags with no dedicated '
    "field above — e.g. Clearance, Visa, Team, Comp, Start date — only if the JD states them)}"
)

MATCH_SYSTEM = (
    "You are a technical recruiter scoring a candidate's résumé against a job description "
    "using a fixed rubric. Judge strictly from the two texts. Score each of these EXACT "
    "dimensions 0-100: 'Required skills' (JD must-haves the résumé evidences), 'Preferred "
    "skills' (nice-to-haves), 'Experience & seniority' (years/level/scope fit), 'Domain "
    "relevance' (role/industry fit), 'ATS keywords' (share of the JD's key hard terms "
    "present in the résumé). Also list the JD's important hard keywords present vs absent. "
    "Reply with ONLY a compact JSON object, no prose, no code fences: "
    '{"scoreBreakdown":[{"dimension":str,"score":int,"note":str}],'
    '"matched":[up to 8 requirements the résumé clearly satisfies],'
    '"atsCovered":[str],"atsMissing":[str],'
    '"summary":"1-2 sentence honest assessment and the single biggest gap"}'
)

# Same weighted rubric as the résumé generator — one scoring standard across the app.
MATCH_WEIGHTS = [
    ("Required skills", 40), ("Preferred skills", 15), ("Experience & seniority", 20),
    ("Domain relevance", 15), ("ATS keywords", 10),
]

INTERVIEW_SYSTEM = (
    "You are an interview coach preparing a candidate for one specific job. Use the "
    "job description (and the résumé, if given) to produce realistic, tailored prep. "
    "Ground talking points ONLY in the résumé/JD provided — never invent the candidate's "
    "experience. Reply with ONLY a compact JSON object, no prose, no code fences: "
    '{"technical":[{"q":str,"hint":str}] (up to 6 role-specific technical questions with a '
    'one-line hint at a strong answer), "behavioral":[{"q":str,"angle":str}] (up to 4, angle '
    '= how to frame the answer), "talkingPoints":[str] (up to 5 — how THIS candidate\'s '
    'background maps to THIS JD), "gaps":[str] (up to 4 likely weak spots to prepare for), '
    '"askThem":[str] (up to 4 sharp questions to ask the interviewer)}'
)

ASK_SYSTEM = (
    "You are the assistant for a personal job-application tracker. Answer the user's "
    "question using ONLY their applications, provided as JSON. Be concise and specific "
    "— name companies, statuses, and dates. If they ask to find or list applications, "
    "pick the matching ones. Never invent applications or facts. Reply with ONLY a "
    'compact JSON object: {"answer": str (plain text, concise, no markdown), '
    '"appIds": [the appId values your answer references, most relevant first, max 12]}'
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

        # /notifications  (classified inbox findings feed)
        if parts[:1] == ["notifications"] and method == "GET":
            return list_notifications(user)

        # /ask  (natural-language Q&A over the user's applications)
        if parts[:1] == ["ask"] and method == "POST":
            return ask_ai(user, body)

        # /sponsorship  (H-1B visa-sponsorship check for a company / JD)
        if parts[:1] == ["sponsorship"] and method == "POST":
            return sponsorship_check(user, body)

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
            elif len(parts) == 3 and parts[2] == "interview-prep" and method == "POST":
                return interview_prep(user, parts[1])
            elif len(parts) == 3 and parts[2] == "attach-generated" and method == "POST":
                return attach_generated(user, parts[1], body)
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
    old_status = record.get("status")
    old_docs = len(record.get("documents") or [])
    old_due = record.get("nextDue")
    record.update(data or {})
    now = int(time.time())
    record.update({"appId": app_id, "userId": user, "updatedAt": now})
    # append meaningful activity events (so the timeline isn't a gap-filler)
    events = record.get("timeline") or []
    new_status = record.get("status")
    if new_status and new_status != old_status:
        events.append({"at": now, "event": f"Status → {new_status}"})
    if len(record.get("documents") or []) > old_docs:
        events.append({"at": now, "event": "Résumé attached"})
    new_due = record.get("nextDue")
    if new_due and new_due != old_due:
        events.append({"at": now, "event": f"Next action set — due {new_due}"})
    record["timeline"] = events[-50:]
    _put(app_id, user, record)
    return _r(200, record)


def list_notifications(user):
    """Recent classified inbox findings across all applications (the bell feed)."""
    events, kwargs = [], {"TableName": EVENTS}
    while True:
        r = ddb.scan(**kwargs)
        events += [json.loads(i["body"]["S"]) for i in r.get("Items", [])]
        if "LastEvaluatedKey" not in r or len(events) > 500:
            break
        kwargs["ExclusiveStartKey"] = r["LastEvaluatedKey"]
    # "other" rows are stored only for scan de-dup — never shown as findings
    events = [e for e in events if e.get("category") and e.get("category") != "other"]
    events.sort(key=lambda e: e.get("receivedAt", 0), reverse=True)
    return _r(200, {"notifications": events[:50]})


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


def attach_generated(user, app_id, data):
    """Copy a résumé PDF produced by the generator (generated/<job>/resume.pdf) into
    the app's own documents space, so it downloads through the normal ownership check.
    Tolerant: if the PDF isn't there yet (compile pending/failed), returns no doc."""
    _owned(user, app_id)
    job = str((data or {}).get("job", ""))
    if not re.fullmatch(r"[0-9a-f]{32}", job):
        return _r(400, {"error": "bad job id"})
    src = f"generated/{job}/resume.pdf"
    dst = f"documents/{user}/{app_id}/{uuid.uuid4()}-resume-tailored.pdf"
    try:
        s3.copy_object(Bucket=BUCKET, CopySource={"Bucket": BUCKET, "Key": src}, Key=dst)
    except Exception as e:  # noqa: BLE001 — PDF may not exist yet; not fatal
        print(f"attach-generated: no PDF to copy ({type(e).__name__})")
        return _r(200, {"doc": None})
    return _r(200, {"doc": {"docKey": dst, "filename": "resume-tailored.pdf", "kind": "resume",
                            "at": int(time.time())}})


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
    attrs = [{"key": str(a.get("key", "")).strip(), "value": str(a.get("value", "")).strip()}
             for a in (fields.get("attributes") or []) if isinstance(a, dict) and str(a.get("key", "")).strip()]
    return _r(200, {"fields": {k: v for k, v in fields.items() if k in allowed}, "attributes": attrs})


def _first_json(text):
    start = text.find("{")
    end = text.rfind("}")
    return text[start:end + 1] if start != -1 and end != -1 else "{}"


def _user_apps(user):
    items, kwargs = [], {"TableName": APPS, "FilterExpression": "userId = :u",
                         "ExpressionAttributeValues": {":u": {"S": user}}}
    while True:
        resp = ddb.scan(**kwargs)
        items += [json.loads(i["body"]["S"]) for i in resp.get("Items", [])]
        if "LastEvaluatedKey" not in resp:
            break
        kwargs["ExclusiveStartKey"] = resp["LastEvaluatedKey"]
    return items


_ASK_FIELDS = ("appId", "company", "title", "status", "priority", "dateApplied", "location",
               "state", "workMode", "salary", "source", "tags", "sponsors", "sponsorVerdict",
               "matchPercent", "nextAction", "nextDue", "contactName", "referredBy",
               "referralStatus", "requiredSkills", "confirmed")


def ask_ai(user, body):
    """Natural-language Q&A over the user's own applications (context-stuffed)."""
    q = str((body or {}).get("question") or "").strip()
    if len(q) < 2:
        return _r(400, {"error": "ask a question"})
    ctx = [{k: a.get(k) for k in _ASK_FIELDS if a.get(k) not in (None, "")}
           for a in _user_apps(user)][:250]
    payload = {
        "anthropic_version": "bedrock-2023-05-31", "max_tokens": 700, "system": ASK_SYSTEM,
        "messages": [{"role": "user", "content": [{"type": "text",
            "text": f"Question: {q}\n\nMy applications (JSON):\n{json.dumps(ctx)}"}]}],
    }
    try:
        resp = bedrock.invoke_model(modelId=BEDROCK_MODEL, body=json.dumps(payload))
        text = json.loads(resp["body"].read())["content"][0]["text"]
        out = json.loads(_first_json(text))
    except Exception as e:  # noqa: BLE001
        print(f"ask failed: {type(e).__name__}: {e}")
        return _r(502, {"error": "couldn't answer that, try again"})
    ids = [i for i in (out.get("appIds") or []) if isinstance(i, str)][:12]
    return _r(200, {"answer": str(out.get("answer") or "")[:2000], "appIds": ids})


def sponsorship_check(user, body):
    """One-stop visa-sponsorship verdict: JD language + curated employer lists +
    a live H-1B (LCA) history lookup. With an appId, persists the verdict onto the
    application (and lights up the OPT/sponsors flag + filter)."""
    data = body or {}
    app_id = data.get("appId")
    company = str(data.get("company") or "").strip()
    jd = str(data.get("jd") or "")
    record = None
    if app_id:
        record = _owned(user, app_id)
        company = company or record.get("company", "")
        jd = jd or record.get("jd", "")
    if len(company) < 2:
        return _r(400, {"error": "add a company name first"})

    h1b = sp.fetch_h1b(company)
    verdict = sp.resolve(company, jd, h1b)
    verdict["links"] = sp.verify_links(company)

    if record is not None:
        now = int(time.time())
        record.update({
            "sponsorVerdict": verdict["level"], "sponsorLabel": verdict["label"],
            "sponsorReasons": verdict["reasons"], "sponsorH1b": verdict["h1b"],
            "sponsorCapExempt": verdict["capExempt"], "sponsors": verdict["sponsors"],
            "sponsorCheckedAt": now, "updatedAt": now,
        })
        events = record.get("timeline") or []
        events.append({"at": now, "event": f"Sponsorship check — {verdict['label']}"})
        record["timeline"] = events[-50:]
        _put(app_id, user, record)
    return _r(200, {"sponsorship": verdict})


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
        # 1500 (was 700): the weighted-rubric schema (5 scored dimensions + notes +
        # matched + ATS lists + summary) overran 700 and truncated the JSON.
        # temperature 0.2 (was unset → default ~1.0): high temp made Haiku emit
        # malformed JSON (unescaped quotes in notes) non-deterministically.
        "anthropic_version": "bedrock-2023-05-31", "max_tokens": 1500, "temperature": 0.2,
        "system": MATCH_SYSTEM,
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

    # weighted rubric (computed here, not the model's number) + ATS keyword rate
    by_dim = {str(d.get("dimension", "")).strip().lower(): d for d in (result.get("scoreBreakdown") or [])}
    breakdown, wsum, tot = [], 0, 0
    for dim, w in MATCH_WEIGHTS:
        sc = max(0, min(100, int((by_dim.get(dim.lower()) or {}).get("score", 0) or 0)))
        breakdown.append({"dimension": dim, "weight": w, "score": sc,
                          "note": str((by_dim.get(dim.lower()) or {}).get("note", ""))[:120]})
        wsum += sc * w
        tot += w
    cov, miss = result.get("atsCovered", []), result.get("atsMissing", [])
    result["matchPercent"] = round(wsum / tot) if tot else 0
    result["scoreBreakdown"] = breakdown
    result["atsScore"] = round(100 * len(cov) / max(1, len(cov) + len(miss)))
    result["missing"] = miss

    # persist a lightweight summary on the record so cards/detail can show it
    item["matchPercent"] = result["matchPercent"]
    item["matchSummary"] = str(result.get("summary") or "")[:400]
    item["matchMatched"] = result.get("matched") or []
    item["matchMissing"] = miss
    item["scoreBreakdown"] = breakdown
    item["atsScore"] = result["atsScore"]
    now = int(time.time())
    item["matchedAt"] = now
    item["updatedAt"] = now
    events = item.get("timeline") or []
    events.append({"at": now, "event": f"Match check — {item['matchPercent']}% fit"})
    item["timeline"] = events[-50:]
    _put(app_id, user, item)
    return _r(200, {"match": result})


def interview_prep(user, app_id):
    """Turn the stored JD (+ résumé if attached) into tailored interview prep."""
    item = _owned(user, app_id)
    jd = item.get("jd", "")
    if len(jd) < 20:
        return _r(400, {"error": "add the job description to this application first"})
    docs = item.get("documents", [])
    resume = next((d for d in docs if d.get("kind") == "resume"), docs[0] if docs else None)
    resume_txt = _pdf_text(resume["docKey"])[:6000] if resume else ""
    user_txt = f"JOB DESCRIPTION:\n{jd[:8000]}"
    user_txt += (f"\n\nCANDIDATE RÉSUMÉ:\n{resume_txt}" if resume_txt else
                 "\n\n(No résumé attached — base talking points on the JD's requirements and "
                 "keep them as prompts the candidate can fill in with their own experience.)")

    payload = {
        "anthropic_version": "bedrock-2023-05-31", "max_tokens": 1900, "temperature": 0.3,
        "system": INTERVIEW_SYSTEM,
        "messages": [{"role": "user", "content": [{"type": "text", "text": user_txt}]}],
    }
    try:
        resp = bedrock.invoke_model(modelId=BEDROCK_MODEL, body=json.dumps(payload))
        out = json.loads(resp["body"].read())["content"][0]["text"]
        prep = json.loads(_first_json(out))
    except Exception as e:  # noqa: BLE001
        print(f"interview-prep failed: {type(e).__name__}: {e}")
        return _r(502, {"error": "prep generation failed, try again"})

    def _qs(arr, second):
        return [{"q": str(x.get("q", ""))[:400], second: str(x.get(second, ""))[:300]}
                for x in (arr or []) if isinstance(x, dict) and x.get("q")]
    clean = {
        "technical": _qs(prep.get("technical"), "hint")[:6],
        "behavioral": _qs(prep.get("behavioral"), "angle")[:4],
        "talkingPoints": [str(x)[:300] for x in (prep.get("talkingPoints") or [])][:5],
        "gaps": [str(x)[:300] for x in (prep.get("gaps") or [])][:4],
        "askThem": [str(x)[:300] for x in (prep.get("askThem") or [])][:4],
    }
    now = int(time.time())
    item.update({"interviewPrep": clean, "interviewPrepAt": now, "updatedAt": now})
    events = item.get("timeline") or []
    events.append({"at": now, "event": "Interview prep generated"})
    item["timeline"] = events[-50:]
    _put(app_id, user, item)
    return _r(200, {"prep": clean})


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
