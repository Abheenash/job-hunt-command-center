"""Prospects Lambda — configurable, multi-source job-feed ingestion (surface only).

On a schedule it fetches every feed in FEED_URL (comma/newline-separated — JSON
array/object or RSS/Atom), parses title/company/url/location/description/source,
dedupes across all of them, and stores a capped list in S3. The dashboard shows a
review queue where YOU click "Track"; the tool never applies for you.

Only legitimate, ToS-permitting sources — open job APIs (e.g. Remotive) and legal
aggregators (e.g. Adzuna, which itself indexes Indeed and others). LinkedIn / Indeed /
Dice have no open feed and scraping them breaks their ToS, so they are not supported.

No-ops safely when FEED_URL is unset.
"""
import hashlib
import json
import os
import re
import urllib.request
from urllib.parse import urlparse
import xml.etree.ElementTree as ET

import boto3

s3 = boto3.client("s3")

BUCKET = os.environ["DOCS_BUCKET"]
FEED = (os.environ.get("FEED_URL") or "").strip()
# Optional relevance filter — keep only postings whose title/description mention one
# of these terms (comma-separated env; empty = keep everything). Trims fuzzy feeds.
FILTER = [k.strip().lower() for k in (os.environ.get("FILTER_KEYWORDS") or "").split(",") if k.strip()]
KEY = "prospects/list.json"
CAP = 300
PER_FEED = 60


def _relevant(it):
    # Match the TITLE only — descriptions name "AWS"/"CI/CD" as nice-to-haves on
    # tons of unrelated roles, so title is what actually signals the role type.
    if not FILTER:
        return True
    title = it.get("title", "").lower()
    return any(k in title for k in FILTER)


def handler(event, _ctx):
    src_cfg = (event or {}).get("feedUrl") or FEED
    feeds = [u.strip() for u in re.split(r"[,\n]+", src_cfg) if u.strip()]
    if not feeds:
        print("no FEED_URL configured — skipping (harmless no-op).")
        return {"configured": False, "added": 0}

    existing = _load()
    seen = {p.get("id") for p in existing}
    added, per_source = 0, {}
    for feed in feeds:
        source = (urlparse(feed).hostname or feed).replace("www.", "").split(".")[0]
        try:
            req = urllib.request.Request(feed, headers={"User-Agent": "jobhunt-prospects/1.0", "Accept": "application/json"})
            raw = urllib.request.urlopen(req, timeout=20).read().decode("utf-8", "ignore")
        except Exception as e:  # noqa: BLE001 — one bad feed shouldn't kill the rest
            print(f"feed failed ({source}): {type(e).__name__}: {e}")
            continue
        items = _parse(raw, source)
        for it in items:
            pid = hashlib.sha1((it.get("url") or it.get("title", "")).encode("utf-8", "ignore")).hexdigest()[:16]
            if not it.get("title") or pid in seen or not _relevant(it):
                continue
            it["id"] = pid
            existing.append(it)
            seen.add(pid)
            added += 1
            per_source[source] = per_source.get(source, 0) + 1

    existing = existing[-CAP:]
    s3.put_object(Bucket=BUCKET, Key=KEY, Body=json.dumps(existing).encode("utf-8"), ContentType="application/json")
    print(f"prospects: {added} added from {len(feeds)} feed(s) {per_source}; total {len(existing)}")
    return {"configured": True, "added": added, "bySource": per_source, "total": len(existing)}


def _str(v, *nested_keys):
    """Value may be a string OR a nested dict (e.g. company:{display_name})."""
    if isinstance(v, dict):
        for k in nested_keys:
            if v.get(k):
                return str(v[k]).strip()
        return ""
    return str(v or "").strip()


def _parse(raw, source):
    raw = raw.strip()
    if raw[:1] in ("[", "{"):
        try:
            data = json.loads(raw)
        except Exception:  # noqa: BLE001
            return []
        arr = data if isinstance(data, list) else (
            data.get("jobs") or data.get("items") or data.get("results") or data.get("data") or [])
        out = []
        for j in arr[:PER_FEED]:
            if not isinstance(j, dict):
                continue
            company = _str(j.get("company") or j.get("company_name") or j.get("employer"), "display_name", "name")
            locs = j.get("locations")
            if isinstance(locs, list) and locs:      # The Muse: locations: [{name}]
                location = _str(locs[0], "name", "display_name")
            else:
                location = _str(j.get("location") or j.get("candidate_required_location") or j.get("job_location"), "display_name", "name")
            url = (j.get("url") or j.get("apply_url") or j.get("link") or j.get("redirect_url")
                   or _str(j.get("refs"), "landing_page") or "")
            out.append({
                "title": _str(j.get("title") or j.get("position") or j.get("name")),
                "company": company, "url": str(url).strip(), "location": location,
                "description": re.sub("<[^>]+>", "", str(j.get("description") or j.get("snippet") or j.get("contents") or ""))[:3000],
                "source": source,
            })
        return out
    # RSS / Atom
    out = []
    try:
        root = ET.fromstring(raw)
    except Exception as e:  # noqa: BLE001
        print(f"feed parse failed ({source}): {e}")
        return out
    for item in root.iter():
        tag = item.tag.lower()
        if not (tag.endswith("item") or tag.endswith("entry")):
            continue
        link = (item.findtext("link") or "").strip()
        if not link:
            le = item.find("{http://www.w3.org/2005/Atom}link")
            link = le.get("href", "") if le is not None else ""
        desc = item.findtext("description") or item.findtext("{http://www.w3.org/2005/Atom}summary") or ""
        out.append({"title": (item.findtext("title") or "").strip(), "company": "", "url": link,
                    "location": "", "description": re.sub("<[^>]+>", "", desc)[:3000], "source": source})
        if len(out) >= PER_FEED:
            break
    return out


def _load():
    try:
        return json.loads(s3.get_object(Bucket=BUCKET, Key=KEY)["Body"].read())
    except Exception:  # noqa: BLE001
        return []
