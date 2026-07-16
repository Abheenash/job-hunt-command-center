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
bedrock = boto3.client("bedrock-runtime")
BEDROCK_MODEL = "us.anthropic.claude-haiku-4-5-20251001-v1:0"
TABLE = os.environ["OPENINGS_TABLE"]
UA = {"User-Agent": "Mozilla/5.0 (jobhunt openings radar)"}
# Freshness / auto-expiry. Each scan an opening still appears in pushes its clock
# forward; once it stops appearing it ages out this many days later. Strong matches
# get more runway, low-value ones (blocked / low fit) go sooner. Removal itself is
# DynamoDB TTL on expireAt, and the API also hides anything already past expiry.
FRESH_DAYS = 7          # normal openings live 7 days after they were last seen
STALE_DAYS = 3          # low-value (blocked / low fit) age out faster
STRONG_DAYS = 14        # strong, sponsor-friendly matches get more runway
STRONG_FIT = 75         # fit at/above this = "strong"
STORE_MIN_FIT = 40      # below this = low value -> the faster STALE_DAYS clock
MAX_STORE = 150         # safety cap on upserts per scan
MAX_BEDROCK = 30        # cap Haiku calls per scan (cost control)

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
ROLE_RE = re.compile(
    r"\b(cloud|devops|sre|site\s*reliability|support engineer|systems? engineer|"
    r"platform engineer|infrastructure engineer|cloud engineer|solutions? architect|"
    r"technical support|reliability engineer|systems? admin|software (developer|engineer)|"
    r"network operations|data engineer)\b", re.I)
SENIOR_RE = re.compile(r"\b(senior|staff|principal|lead|sr\.?|manager|director|distinguished|head of|vp|iii|iv)\b", re.I)
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

SKILL_KW = ["aws", "lambda", "s3", "dynamodb", "ec2", "ecs", "eks", "kubernetes", "terraform",
            "ci/cd", "cicd", "github actions", "docker", "cloudwatch", "devops", "sre",
            "reliability", "cloud", "linux", "python", "c++", "iam", "vpc", "network",
            "incident", "observability", "serverless", "api gateway", "rds", "sql", "bash",
            "monitoring", "troubleshoot", "containers", "helm", "prometheus", "grafana"]

PROFILE = (
    "Entry-level candidate: M.S. Computer & Systems Engineering (Dec 2025), AWS Solutions "
    "Architect Associate, ~1-2 years DevOps / cloud-operations + C++ systems experience. "
    "Skills: AWS (Lambda, S3, DynamoDB, ECS, EKS, CloudWatch), Terraform, Docker/Kubernetes, "
    "CI/CD (GitHub Actions), Linux, Python, C++, IAM/VPC/networking, observability/SRE, "
    "incident response. On F-1 OPT — needs future H-1B sponsorship. Houston TX; remote or relocate."
)

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


def _relevant(title):
    """A role we care about, and not an internship (candidate isn't a student)."""
    return bool(ROLE_RE.search(title or "")) and not INTERN_RE.search(title or "")


# --- source collectors (each returns a list of raw opening dicts) ------------

def from_greenhouse(token):
    out = []
    d = json.loads(_get(f"https://boards-api.greenhouse.io/v1/boards/{token}/jobs?content=true"))
    company = (d.get("jobs") or [{}])[0].get("company_name") or token.title()
    for j in d.get("jobs", []):
        title = j.get("title", "")
        if not _relevant(title):
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
        if not _relevant(title):
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
            if j.get("is_intern") or not _relevant(title):
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
        if not _relevant(title) or not _is_us(loc):
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
    # dedupe by url
    seen, uniq = set(), []
    for o in raw:
        if o["url"] and o["url"] not in seen:
            seen.add(o["url"])
            uniq.append(o)
    return uniq, errors


# --- scoring -----------------------------------------------------------------

def _sponsor(jd):
    if NEG_RE.search(jd):
        return True, "JD excludes visa sponsorship"
    if CITIZEN_RE.search(jd):
        return True, "Requires US citizen / clearance"
    return False, ""


def _geo_tier(o):
    """0 = Texas, 1 = remote-US, 2 = elsewhere in the US, 3 = unknown. Texas wins even
    if the role is also remote (candidate is TX-based)."""
    loc = o.get("location") or ""
    if TX_RE.search(loc):
        return 0
    if "remote" in loc.lower():
        return 1
    if US_POS_RE.search(loc) or US_STATE_RE.search(loc):
        return 2
    return 3


def _base_score(o):
    title, jd = o["title"], (o.get("jd") or "").lower()
    s = 40 if ROLE_RE.search(title) else 0
    if JUNIOR_RE.search(title):
        s += 22
    if SENIOR_RE.search(title):
        s -= 38
    hits = sum(1 for k in SKILL_KW if k in jd)
    s += min(26, hits * 3)
    if o.get("blocked"):
        s -= 70
    low = (o.get("location") or "").lower()
    if "remote" in low:
        s += 6
    if US_POS_RE.search(o.get("location") or "") or US_STATE_RE.search(o.get("location") or ""):
        s += 8
    return max(0, min(100, s))


def _bedrock_fit(o):
    prompt = (
        f"CANDIDATE:\n{PROFILE}\n\nJOB:\nCompany: {o['company']}\nTitle: {o['title']}\n"
        f"Location: {o['location']}\nDescription (excerpt):\n{(o.get('jd') or '')[:2500]}\n\n"
        "Score how well this job fits the candidate for their NEXT role. Reply with ONLY a "
        "compact JSON object, no prose: {\"fit\": int 0-100, \"reason\": str (one short "
        "sentence), \"sponsorRisk\": \"low\"|\"med\"|\"high\" (high if the JD hints at "
        "no-sponsorship / citizenship / clearance)}")
    payload = {"anthropic_version": "bedrock-2023-05-31", "max_tokens": 200, "temperature": 0.2,
               "messages": [{"role": "user", "content": [{"type": "text", "text": prompt}]}]}
    resp = bedrock.invoke_model(modelId=BEDROCK_MODEL, body=json.dumps(payload))
    txt = json.loads(resp["body"].read())["content"][0]["text"]
    a, b = txt.find("{"), txt.rfind("}")
    r = json.loads(txt[a:b + 1])
    return {"fit": max(0, min(100, int(r.get("fit", 0)))), "reason": str(r.get("reason", ""))[:200],
            "sponsorRisk": r.get("sponsorRisk", "med")}


def handler(event, _ctx):
    openings, errors = collect()
    for o in openings:
        o["blocked"], o["sponsorNote"] = _sponsor(o.get("jd") or "")
        o["fit"] = _base_score(o)
        o["geo"] = _geo_tier(o)
    # Order by geo tier (TX > remote > rest-of-US), fit as tiebreaker — so TX and the
    # best-fit roles get the bounded Bedrock refinement, and the stored order matches.
    openings.sort(key=lambda o: (o["geo"], -o["fit"]))

    # Bedrock-refine the top candidates (bounded); keep the deterministic tail.
    for o in openings[:MAX_BEDROCK]:
        try:
            r = _bedrock_fit(o)
            o["fit"] = r["fit"] if not o["blocked"] else min(r["fit"], 25)
            o["reason"] = r["reason"]
            o["sponsorRisk"] = "high" if o["blocked"] else r["sponsorRisk"]
        except Exception as e:  # noqa: BLE001 — degrade to deterministic score
            o.setdefault("reason", "")
            o.setdefault("sponsorRisk", "high" if o["blocked"] else "med")
            errors.append(f"bedrock:{type(e).__name__}")
    for o in openings[MAX_BEDROCK:]:
        o.setdefault("reason", "")
        o["sponsorRisk"] = "high" if o["blocked"] else "med"

    openings.sort(key=lambda o: (o["geo"], -o["fit"]))
    keep = openings[:MAX_STORE]

    now = int(time.time())
    stored = 0
    for o in keep:
        oid = hashlib.sha1(o["url"].encode()).hexdigest()[:16]
        fit = o["fit"]
        if o.get("blocked") or fit < STORE_MIN_FIT:
            ttl_days = STALE_DAYS
        elif fit >= STRONG_FIT and o.get("sponsorRisk") == "low":
            ttl_days = STRONG_DAYS
        else:
            ttl_days = FRESH_DAYS
        expire = now + ttl_days * 86400
        rec = {k: o.get(k) for k in ("company", "title", "location", "url", "source",
                                     "fit", "geo", "reason", "blocked", "sponsorNote", "sponsorRisk")}
        rec["jd"] = (o.get("jd") or "")[:6000]
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
    print(f"openings scan: collected={len(openings)} stored={stored} errors={errors[:12]}")
    return {"collected": len(openings), "stored": stored, "errors": errors[:12]}
