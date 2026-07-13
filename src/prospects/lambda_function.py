"""Prospects Lambda — configurable job-feed ingestion (surface, never auto-apply).

On a schedule it fetches a feed you point it at (FEED_URL — a JSON array/object of
postings, or an RSS/Atom feed), parses out title/company/url/location/description,
dedupes against what it's already seen, and stores a capped list in S3. The dashboard
shows these as a review queue where YOU click "Track" to start an application — the
tool never applies for you.

No-ops safely when FEED_URL is unset, so the schedule is harmless until you configure
a source (respecting each board's ToS is on you).
"""
import hashlib
import json
import os
import re
import urllib.request
import xml.etree.ElementTree as ET

import boto3

s3 = boto3.client("s3")

BUCKET = os.environ["DOCS_BUCKET"]
FEED = (os.environ.get("FEED_URL") or "").strip()
KEY = "prospects/list.json"
CAP = 200


def handler(event, _ctx):
    feed = (event or {}).get("feedUrl") or FEED
    if not feed:
        print("no FEED_URL configured — skipping (harmless no-op).")
        return {"configured": False, "added": 0}
    try:
        req = urllib.request.Request(feed, headers={"User-Agent": "jobhunt-prospects/1.0"})
        raw = urllib.request.urlopen(req, timeout=20).read().decode("utf-8", "ignore")
    except Exception as e:  # noqa: BLE001
        print(f"feed fetch failed: {type(e).__name__}: {e}")
        return {"configured": True, "error": "fetch failed", "added": 0}

    items = _parse(raw)
    existing = _load()
    seen = {p.get("id") for p in existing}
    added = 0
    for it in items:
        pid = hashlib.sha1((it.get("url") or it.get("title", "")).encode("utf-8", "ignore")).hexdigest()[:16]
        if not (it.get("title") and pid) or pid in seen:
            continue
        it["id"] = pid
        existing.append(it)
        seen.add(pid)
        added += 1
    existing = existing[-CAP:]
    s3.put_object(Bucket=BUCKET, Key=KEY, Body=json.dumps(existing).encode("utf-8"), ContentType="application/json")
    print(f"prospects: fetched {len(items)}, added {added}, total {len(existing)}")
    return {"configured": True, "fetched": len(items), "added": added, "total": len(existing)}


def _parse(raw):
    raw = raw.strip()
    if raw[:1] in ("[", "{"):
        try:
            data = json.loads(raw)
        except Exception:  # noqa: BLE001
            return []
        arr = data if isinstance(data, list) else (data.get("jobs") or data.get("items") or data.get("results") or [])
        out = []
        for j in arr[:100]:
            if not isinstance(j, dict):
                continue
            out.append({
                "title": (j.get("title") or j.get("position") or j.get("name") or "").strip(),
                "company": (j.get("company") or j.get("company_name") or j.get("employer") or "").strip(),
                "url": (j.get("url") or j.get("apply_url") or j.get("link") or j.get("redirect_url") or "").strip(),
                "location": (j.get("location") or j.get("candidate_required_location") or "").strip(),
                "description": re.sub("<[^>]+>", "", str(j.get("description") or j.get("snippet") or ""))[:3000],
            })
        return out
    # RSS / Atom
    out = []
    try:
        root = ET.fromstring(raw)
    except Exception as e:  # noqa: BLE001
        print(f"feed parse failed: {e}")
        return out
    for item in root.iter():
        tag = item.tag.lower()
        if not (tag.endswith("item") or tag.endswith("entry")):
            continue
        title = (item.findtext("title") or "").strip()
        link = (item.findtext("link") or "").strip()
        if not link:
            le = item.find("{http://www.w3.org/2005/Atom}link")
            link = le.get("href", "") if le is not None else ""
        desc = item.findtext("description") or item.findtext("{http://www.w3.org/2005/Atom}summary") or ""
        out.append({"title": title, "company": "", "url": link, "location": "",
                    "description": re.sub("<[^>]+>", "", desc)[:3000]})
        if len(out) >= 100:
            break
    return out


def _load():
    try:
        return json.loads(s3.get_object(Bucket=BUCKET, Key=KEY)["Body"].read())
    except Exception:  # noqa: BLE001
        return []
