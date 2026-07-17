"""Openings Radar — scan target companies' ATS JSON for entry-level cloud / DevOps
/ SRE / cloud-support / systems roles in the US, flag visa sponsorship, score fit
against the owner's profile, and store the top matches.

Runs daily on EventBridge (and on demand via the API). Only documented JSON
endpoints are used — Greenhouse, Ashby, amazon.jobs, and Workday (per company).
No scraping of JS-rendered pages. Every source is best-effort: one failing source
never sinks the scan, and Bedrock scoring degrades to the deterministic score.
"""
import hashlib
import json
import os
import re
import time
import urllib.parse
import urllib.request

import boto3

ddb = boto3.client("dynamodb")
TABLE = os.environ["OPENINGS_TABLE"]
APPS_TABLE = os.environ.get("APPS_TABLE", "")  # to skip roles already in the tracker
UA = {"User-Agent": "Mozilla/5.0 (jobhunt openings radar)"}
# Freshness / auto-expiry. Each scan an opening still appears in pushes its clock
# forward; once it stops appearing it ages out this many days later. Strong matches
# get more runway, low-value ones (blocked / low fit) go sooner. Removal itself is
# DynamoDB TTL on expireAt, and the API also hides anything already past expiry.
FRESH_DAYS = 7          # normal openings live 7 days after they were last seen
STALE_DAYS = 3          # blocked / sponsorship-risk openings age out faster
STRONG_DAYS = 14        # strong, sponsor-friendly matches get more runway
STRONG_FIT = 75         # fit at/above this = "strong"
KEEP_MIN_FIT = 50       # QUALITY BAR — only keep openings scoring >= this match %
MAX_STORE = 60          # keep the best ~60 (quality over volume)
# Scoring is deterministic (JD-content overlap vs the candidate's stack) — NO Bedrock,
# so the daily scan costs ~$0 in AI. Deep, AI-quality matching happens on demand via
# Claude (Max plan) when the candidate sits down to apply.

# --- target companies by ATS (sponsor-friendly; known no-sponsors excluded) ---
# Live-fetchable via each ATS's public JSON board. Curated to sponsor-friendly
# tech / infra / data / security / fintech; GitLab & Zapier deliberately excluded
# (documented no-sponsor). The per-role JD scan still flags any no-sponsor posting.
GREENHOUSE = [
    "twilio", "cloudflare", "datadog", "databricks", "mongodb", "elastic", "stripe",
    "coinbase", "robinhood", "airbnb", "reddit", "dropbox", "pinterest", "instacart",
    "affirm", "brex", "figma", "samsara", "gusto", "sofi", "discord", "roblox", "asana",
    "lyft", "grafanalabs", "pagerduty", "newrelic", "sumologic", "cockroachlabs",
    "fivetran", "starburst", "dremio", "vercel", "fastly", "scaleai", "flexport", "faire",
    "airtable", "webflow", "verkada", "nuro", "chime", "anthropic", "rubrik",
    "purestorage", "okta", "zscaler", "yugabyte"]
ASHBY = ["confluent", "snowflake", "openai", "ramp", "notion", "linear", "perplexity",
         "cursor", "replit", "render", "supabase", "posthog"]
WORKDAY = [{"name": "Red Hat", "tenant": "redhat", "dc": "wd5", "site": "Jobs"}]
AMAZON_QUERIES = ["cloud support engineer", "support engineer", "site reliability engineer", "devops engineer"]

# --- role / level / location matching ----------------------------------------
# We DON'T gate on the title — every non-intern posting is scored by how much of the
# candidate's stack its full JD mentions (see _content_score). This is just a SOFT
# engineering-role hint that nudges the score, never a filter.
ROLE_HINT_RE = re.compile(
    r"\b(engineer|developer|sre|devops|architect|operations|administrator|reliability|"
    r"infrastructure|platform|cloud|systems|sysadmin|support|automation|network)\b", re.I)
SENIOR_RE = re.compile(r"\b(senior|staff|principal|lead|sr\.?|manager|director|distinguished|head of|vp|iii|iv)\b", re.I)
# Clearly off-target titles — a SOFT score penalty (not a hard gate; the JD still counts).
# Restores some precision now that there's no AI judge on the daily scan.
OFFTARGET_RE = re.compile(
    r"\b(front[- ]?end|frontend|back[- ]?end|backend|full[- ]?stack|mobile|ios|android|"
    r"(machine learning|\bml\b|\bai\b) engineer|data scien|product manager|program manager|"
    r"sales|account executive|solutions engineer|marketing|recruit|business|financial analyst|"
    r"operations (associate|manager|specialist|analyst|coordinator)|security (engineer|software engineer))\b", re.I)
JUNIOR_RE = re.compile(r"\b(associate|junior|jr\.?|entry[-\s]?level|new[-\s]?grad|graduate|early[-\s]?career|university|early in career|level 1|\bl1\b|\bi\b|apprentice)\b", re.I)
INTERN_RE = re.compile(r"\bintern(ship)?\b", re.I)
NONUS_RE = re.compile(
    r"\b(india|bangalore|bengaluru|pune|hyderabad|chennai|gurgaon|noida|ireland|dublin|"
    r"united kingdom|england|london|canada|toronto|vancouver|ontario|germany|berlin|munich|"
    r"france|paris|australia|sydney|melbourne|singapore|japan|tokyo|brazil|mexico|poland|"
    r"krakow|warsaw|spain|madrid|barcelona|netherlands|amsterdam|romania|bucharest|portugal|"
    r"lisbon|israel|tel aviv|china|shanghai|philippines|manila|pakistan|costa rica|colombia|"
    r"argentina|chile|new zealand|switzerland|sweden|denmark|norway|finland|austria|belgium|"
    r"italy|greece|turkey|uae|dubai|saudi|egypt|nigeria|kenya|south africa|korea|seoul|"
    r"taiwan|vietnam|thailand|indonesia|malaysia|emea|apac|latam)\b", re.I)
US_POS_RE = re.compile(r"\b(united states|u\.?s\.?a?\.?|americas|north america)\b", re.I)
US_STATES = ("AL AK AZ AR CA CO CT DE FL GA HI ID IL IN IA KS KY LA ME MD MA MI MN MS MO MT "
             "NE NV NH NJ NM NY NC ND OH OK OR PA RI SC SD TN TX UT VT VA WA WV WI WY DC").split()
US_STATE_RE = re.compile(r",\s*(" + "|".join(US_STATES) + r")\b")  # "Austin, TX" (case-sensitive code)
# Candidate is Houston, TX and will relocate anywhere in the US. Ranking priority is
# Texas first, then remote-US, then the rest of the US. _geo_tier drives the ordering
# (lower = higher priority); fit is the tiebreaker within each tier.
TX_RE = re.compile(r"(,\s*tx\b)|\btexas\b|\b(houston|austin|dallas|san antonio|fort worth|"
                   r"el paso|plano|irving|frisco|mckinney|round rock|the woodlands|sugar land|"
                   r"richardson|westlake|las colinas)\b", re.I)
# Major US tech hubs (no state code) so a bare-city location still counts as US, not unknown.
US_CITY_RE = re.compile(r"\b(san francisco|sf bay|bay area|new york|nyc|brooklyn|seattle|bellevue|"
                        r"redmond|boston|chicago|denver|boulder|atlanta|los angeles|santa monica|"
                        r"san jose|palo alto|mountain view|sunnyvale|cupertino|menlo park|san mateo|"
                        r"santa clara|oakland|reston|herndon|mclean|philadelphia|pittsburgh|miami|"
                        r"portland|salt lake|phoenix|nashville|charlotte|raleigh|durham|columbus|"
                        r"minneapolis|san diego|sacramento|irvine|culver city|jersey city)\b", re.I)

# Candidate's stack — used to score JD CONTENT overlap (the funnel), not the title.
SKILL_KW = ["aws", "lambda", "s3", "dynamodb", "ec2", "ecs", "fargate", "eks", "kubernetes",
            "k8s", "terraform", "cloudformation", "ansible", "ci/cd", "cicd", "github actions",
            "gitops", "argocd", "jenkins", "docker", "container", "helm", "cloudwatch", "x-ray",
            "datadog", "prometheus", "grafana", "splunk", "devops", "devsecops", "sre",
            "reliability", "slo", "sla", "on-call", "pagerduty", "incident", "runbook",
            "observability", "monitoring", "logging", "cloud", "linux", "unix", "python",
            "boto3", "bash", "shell", "c++", "go ", "golang", "sql", "postgres", "mysql",
            "iam", "vpc", "subnet", "route 53", "load balanc", "alb", "nginx", "network",
            "dns", "tcp", "serverless", "api gateway", "step functions", "sqs", "sns",
            "eventbridge", "rds", "secrets manager", "kms", "waf", "guardduty", "security hub",
            "config", "inspector", "cognito", "troubleshoot", "automation", "scripting",
            "distributed systems", "microservices", "rest api", "oidc", "least privilege"]

# sponsorship kill-phrases (mirrors the /sponsorship checker's negative scan)
NEG_RE = re.compile(
    r"not\s+(?:be\s+)?able\s+to\s+sponsor|un(?:able|willing)\s+to\s+sponsor|"
    r"(?:will|can|do|does|are)\s*n[o']?t\s+(?:currently\s+|be\s+able\s+to\s+)?(?:provide|offer|sponsor)\w*|"
    r"no\s+(?:visa\s+)?sponsorship|without\s+(?:the\s+need\s+for\s+|current\s+or\s+future\s+|requiring\s+|now\s+or\s+in\s+the\s+future\s+for\s+)?(?:employer\s+|visa\s+)?sponsorship|"
    r"sponsorship\s+is\s+not\s+(?:available|offered|provided)|not\s+eligible\s+for\s+(?:visa\s+)?sponsorship|"
    r"we\s+(?:do|are)\s+not\s+(?:able\s+to\s+)?sponsor|must\s+not\s+require\s+sponsorship", re.I)
CITIZEN_RE = re.compile(r"u\.?s\.?\s+citizen|citizenship\s+(?:is\s+)?required|security\s+clearance|\bitar\b|must\s+be\s+a\s+u\.?s\.?\s+person|public\s+trust|green\s+card\s+required", re.I)


def _get(url, timeout=10, data=None, headers=None):
    h = dict(UA)
    if headers:
        h.update(headers)
    req = urllib.request.Request(url, data=data, headers=h)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def _is_us(loc):
    """Keep unless the location clearly names a non-US country with no US signal."""
    if not loc:
        return True
    if NONUS_RE.search(loc) and not US_POS_RE.search(loc):
        return False
    return True


def _clean_html(s):
    s = re.sub(r"<[^>]+>", " ", s or "")
    return re.sub(r"\s+", " ", s).strip()


def _collectible(title):
    """We match on the JD, not the title — so collect every non-intern posting that has
    a real title, and let _content_score + Bedrock judge fit against the portfolio."""
    t = title or ""
    return bool(t.strip()) and not INTERN_RE.search(t)


# --- source collectors (each returns a list of raw opening dicts) ------------

def from_greenhouse(token):
    out = []
    d = json.loads(_get(f"https://boards-api.greenhouse.io/v1/boards/{token}/jobs?content=true"))
    company = (d.get("jobs") or [{}])[0].get("company_name") or token.title()
    for j in d.get("jobs", []):
        title = j.get("title", "")
        if not _collectible(title):
            continue
        loc = (j.get("location") or {}).get("name", "")
        if not _is_us(loc):
            continue
        out.append({"company": company, "title": title, "location": loc,
                    "url": j.get("absolute_url", ""), "source": "greenhouse",
                    "jd": _clean_html(j.get("content", ""))})
    return out


def from_ashby(org):
    out = []
    d = json.loads(_get(f"https://api.ashbyhq.com/posting-api/job-board/{org}"))
    for j in d.get("jobs", []):
        title = j.get("title", "")
        if not _collectible(title):
            continue
        loc = j.get("location", "") or (", ".join(j.get("secondaryLocations", []) or []))
        if not _is_us(loc):
            continue
        out.append({"company": org.title(), "title": title, "location": loc,
                    "url": j.get("jobUrl") or j.get("applyUrl", ""), "source": "ashby",
                    "jd": (j.get("descriptionPlain") or _clean_html(j.get("descriptionHtml", "")))})
    return out


def from_amazon():
    out, seen = [], set()
    for q in AMAZON_QUERIES:
        url = ("https://www.amazon.jobs/en/search.json?" + urllib.parse.urlencode(
            {"base_query": q, "country": "USA", "result_limit": 20, "sort": "recent"}))
        d = json.loads(_get(url))
        for j in d.get("jobs", []):
            title = j.get("title", "")
            if j.get("is_intern") or not _collectible(title):
                continue
            path = j.get("job_path", "")
            if path in seen:
                continue
            seen.add(path)
            out.append({"company": "Amazon / AWS", "title": title,
                        "location": j.get("normalized_location", ""),
                        "url": "https://www.amazon.jobs" + path, "source": "amazon",
                        "jd": _clean_html((j.get("basic_qualifications") or "") + " " + (j.get("description") or ""))})
    return out


def from_workday(cfg):
    out = []
    base = f"https://{cfg['tenant']}.{cfg['dc']}.myworkdayjobs.com/wday/cxs/{cfg['tenant']}/{cfg['site']}"
    body = json.dumps({"appliedFacets": {}, "limit": 20, "offset": 0, "searchText": "engineer"}).encode()
    d = json.loads(_get(base + "/jobs", data=body, headers={"Content-Type": "application/json", "Accept": "application/json"}))
    for p in (d.get("jobPostings") or [])[:20]:
        title = p.get("title", "")
        loc = p.get("locationsText", "")
        if not _collectible(title) or not _is_us(loc):
            continue
        jd = ""
        try:  # JD needs a per-posting detail call
            det = json.loads(_get(base + "/job" + p.get("externalPath", "")))
            info = det.get("jobPostingInfo", {})
            jd = _clean_html(info.get("jobDescription", ""))
            loc = info.get("location", loc)
            ext = info.get("externalUrl")
        except Exception:  # noqa: BLE001
            ext = None
        if not _is_us(loc):
            continue
        url = ext or f"https://{cfg['tenant']}.{cfg['dc']}.myworkdayjobs.com/{cfg['site']}" + p.get("externalPath", "")
        out.append({"company": cfg["name"], "title": title, "location": loc,
                    "url": url, "source": "workday", "jd": jd})
    return out


def collect():
    raw, errors = [], []
    for tok in GREENHOUSE:
        try:
            raw += from_greenhouse(tok)
        except Exception as e:  # noqa: BLE001
            errors.append(f"gh:{tok}:{type(e).__name__}")
    for org in ASHBY:
        try:
            raw += from_ashby(org)
        except Exception as e:  # noqa: BLE001
            errors.append(f"ashby:{org}:{type(e).__name__}")
    for cfg in WORKDAY:
        try:
            raw += from_workday(cfg)
        except Exception as e:  # noqa: BLE001
            errors.append(f"wd:{cfg['name']}:{type(e).__name__}")
    try:
        raw += from_amazon()
    except Exception as e:  # noqa: BLE001
        errors.append(f"amazon:{type(e).__name__}")
    # dedupe by url; cap JD length (we now collect whole boards, not just title matches)
    seen, uniq = set(), []
    for o in raw:
        if o["url"] and o["url"] not in seen:
            seen.add(o["url"])
            o["jd"] = (o.get("jd") or "")[:6000]
            uniq.append(o)
    return uniq, errors


# --- scoring -----------------------------------------------------------------

def _sponsor(jd):
    if NEG_RE.search(jd):
        return True, "JD excludes visa sponsorship"
    if CITIZEN_RE.search(jd):
        return True, "Requires US citizen / clearance"
    return False, ""


def _norm(s):
    return re.sub(r"[^a-z0-9]+", " ", (s or "").lower()).strip()


def _load_suppressions():
    """What NOT to re-scrape: (1) opening IDs the user dismissed or tracked (never
    resurface them), and (2) company+title of everything already in the tracker (don't
    re-scrape a role you're already applying to)."""
    ids, applied = set(), set()
    try:
        kw = {"TableName": TABLE, "ProjectionExpression": "openingId, dismissed, tracked"}
        while True:
            r = ddb.scan(**kw)
            for it in r.get("Items", []):
                if it.get("dismissed", {}).get("BOOL") or it.get("tracked", {}).get("BOOL"):
                    ids.add(it["openingId"]["S"])
            if "LastEvaluatedKey" not in r:
                break
            kw["ExclusiveStartKey"] = r["LastEvaluatedKey"]
    except Exception as e:  # noqa: BLE001 — suppression is best-effort, never sink the scan
        print(f"suppress load (openings) failed: {type(e).__name__}: {e}")
    if APPS_TABLE:
        try:
            kw = {"TableName": APPS_TABLE, "ProjectionExpression": "body"}
            while True:
                r = ddb.scan(**kw)
                for it in r.get("Items", []):
                    try:
                        a = json.loads(it["body"]["S"])
                        applied.add((_norm(a.get("company")), _norm(a.get("title"))))
                    except Exception:  # noqa: BLE001
                        pass
                if "LastEvaluatedKey" not in r:
                    break
                kw["ExclusiveStartKey"] = r["LastEvaluatedKey"]
        except Exception as e:  # noqa: BLE001
            print(f"suppress load (apps) failed: {type(e).__name__}: {e}")
    return ids, applied


def _geo_tier(o):
    """0 = Texas, 1 = remote-US, 2 = elsewhere in the US, 3 = unknown. Texas wins even
    if the role is also remote (candidate is TX-based)."""
    loc = o.get("location") or ""
    if TX_RE.search(loc):
        return 0
    if "remote" in loc.lower():
        return 1
    if US_POS_RE.search(loc) or US_STATE_RE.search(loc) or US_CITY_RE.search(loc):
        return 2
    return 3


def _content_score(o):
    """Deterministic match score (0-100): how much of the candidate's stack the JD
    mentions, plus a soft role-word hint and seniority handling. This IS the stored
    fit — no AI call, so the scan is ~free. Titles don't gate; JD content drives it."""
    title = (o.get("title") or "").lower()
    jd = (o.get("jd") or "").lower()
    text = title + " \n " + jd
    hits = sum(1 for k in SKILL_KW if k in text)   # distinct stack terms across the JD
    s = min(78, hits * 6)                          # JD overlap is the main signal
    if ROLE_HINT_RE.search(title):
        s += 12
    if JUNIOR_RE.search(title):
        s += 8
    if SENIOR_RE.search(title):
        s -= 42          # candidate is early-career — push senior/staff/lead below the bar
    if OFFTARGET_RE.search(title):
        s -= 40          # frontend/backend/sales/ops-associate/etc. — soft demote
    if o.get("blocked"):
        s -= 40
    return max(0, min(100, s))


def _reason(o):
    """A short, honest 'why it matched' from the stack terms actually present in the JD."""
    text = ((o.get("title") or "") + " " + (o.get("jd") or "")).lower()
    matched = [k for k in SKILL_KW if k in text]
    if not matched:
        return ""
    shown = ", ".join(matched[:6])
    more = f" +{len(matched) - 6} more" if len(matched) > 6 else ""
    return f"Matches your stack: {shown}{more}. Verify the full JD before applying."


def handler(event, _ctx):
    openings, errors = collect()
    suppressed_ids, applied = _load_suppressions()
    for o in openings:
        o["oid"] = hashlib.sha1(o["url"].encode()).hexdigest()[:16]
        o["blocked"], o["sponsorNote"] = _sponsor(o.get("jd") or "")
        o["content"] = _content_score(o)   # JD-overlap funnel — who gets a real match
        o["geo"] = _geo_tier(o)
    # Drop before scoring: dismissed/tracked openings (user said no — never resurface),
    # roles already in the tracker (same company+title), and near-duplicate postings
    # (same company+title, different URL) — keeping the best-overlap one of each.
    best, dropped = {}, {"suppressed": 0, "applied": 0, "dup": 0}
    for o in sorted(openings, key=lambda o: -o["content"]):
        if o["oid"] in suppressed_ids:
            dropped["suppressed"] += 1
            continue
        ct = (_norm(o.get("company")), _norm(o.get("title")))
        if ct in applied:
            dropped["applied"] += 1
            continue
        if ct in best:
            dropped["dup"] += 1
            continue
        best[ct] = o
    # Deterministic scoring: the content-overlap score IS the fit (no AI call). Blocked
    # (no-sponsorship / clearance) roles are capped low. Every candidate is scored — it's
    # free — then the quality bar + geo priority pick what's stored.
    for o in best.values():
        o["fit"] = min(o["content"], 25) if o["blocked"] else o["content"]
        o["reason"] = _reason(o)
        o["sponsorRisk"] = "high" if o["blocked"] else "low"

    # Quality bar + geo priority (TX -> remote -> rest-of-US), best match first, capped.
    keep = [o for o in best.values() if o["fit"] >= KEEP_MIN_FIT]
    keep.sort(key=lambda o: (o["geo"], -o["fit"]))
    keep = keep[:MAX_STORE]

    now = int(time.time())
    stored = 0
    for o in keep:
        oid = o["oid"]
        fit = o["fit"]
        if o.get("blocked"):
            ttl_days = STALE_DAYS
        elif fit >= STRONG_FIT and o.get("sponsorRisk") == "low":
            ttl_days = STRONG_DAYS
        else:
            ttl_days = FRESH_DAYS
        expire = now + ttl_days * 86400
        rec = {k: o.get(k) for k in ("company", "title", "location", "url", "source",
                                     "fit", "geo", "reason", "blocked", "sponsorNote", "sponsorRisk")}
        rec["jd"] = (o.get("jd") or "")[:6000]
        rec["scored"] = True  # marks a real weighted-rubric score (vs legacy heuristic rows)
        # Upsert-as-merge: refresh the scan-derived fields and push the freshness clock
        # forward, but NEVER clobber the user's own state (tracked / dismissed) or the
        # original firstSeenAt. This is what turns a rescan into "add the new, keep the
        # rest" instead of a wipe-and-replace.
        ddb.update_item(
            TableName=TABLE,
            Key={"openingId": {"S": oid}},
            UpdateExpression=("SET body = :b, fit = :f, lastSeenAt = :n, "
                              "expireAt = :e, firstSeenAt = if_not_exists(firstSeenAt, :n)"),
            ExpressionAttributeValues={
                ":b": {"S": json.dumps(rec)},
                ":f": {"N": str(fit)},
                ":n": {"N": str(now)},
                ":e": {"N": str(expire)},
            },
        )
        stored += 1
    print(f"openings scan: collected={len(openings)} deduped={len(best)} stored={stored} "
          f"dropped={dropped} errors={errors[:12]}")
    return {"collected": len(openings), "stored": stored, "dropped": dropped, "errors": errors[:12]}
