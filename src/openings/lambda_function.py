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
STALE_DAYS = 3          # blocked / sponsorship-risk openings age out faster
STRONG_DAYS = 14        # strong, sponsor-friendly matches get more runway
STRONG_FIT = 75         # fit at/above this = "strong"
KEEP_MIN_FIT = 50       # QUALITY BAR — only keep openings scoring >= this match %
MAX_STORE = 60          # keep the best ~60 (quality over volume)
MAX_BEDROCK = 120       # rubric-score a deep pool so every STORED opening is fully
                        # scored (many drop below the bar, so score well past MAX_STORE)

# Industry-style match rubric — the SAME weighting the résumé/JD ATS matcher uses,
# so an opening's "fit" is a real weighted match score, not a keyword heuristic.
FIT_WEIGHTS = {"required": 40, "preferred": 15, "experience": 20, "domain": 15, "ats": 10}

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
# Titles that genuinely fit the candidate: entry-level cloud / DevOps / SRE / cloud-
# support / platform / infrastructure / systems. Deliberately NARROW. A generic
# "Software Engineer" only qualifies with an infra signal (SWE_INFRA_RE), and off-target
# specialties (frontend/backend/ML/data/security/etc.) are rejected (OFFTARGET_RE).
ROLE_RE = re.compile(
    r"\b(devops|dev ops|\bsre\b|site\s*reliability|reliability engineer|"
    r"cloud engineer|cloud infrastructure|cloud operations|cloud[- ]?ops|cloud support|"
    r"platform engineer|platform engineering|infrastructure engineer|infrastructure engineering|"
    r"systems? engineer|systems? administrator|sysadmin|"
    r"(cloud|technical|product|it|customer) support engineer|"
    r"solutions? architect|network (operations|engineer)|\bnoc\b|"
    r"build (and|&) release|release engineer|devsecops)\b", re.I)
# A generic "Software Engineer" title only qualifies when paired with an infra signal.
SWE_INFRA_RE = re.compile(
    r"software engineer.{0,45}(infrastructure|platform|cloud|reliability|\bsre\b|devops|"
    r"systems|networking|compute|observability|developer productivity|dev velocity)|"
    r"(infrastructure|platform|cloud|reliability|\bsre\b|devops|compute).{0,25}software engineer", re.I)
# Off-target specialties — reject even if a generic keyword slipped through.
OFFTARGET_RE = re.compile(
    r"\b(front[- ]?end|frontend|full[- ]?stack|back[- ]?end|backend|mobile|ios|android|web developer|"
    r"(machine learning|\bml\b|\bai\b|deep learning) engineer|data scien|applied scien|"
    r"research (scientist|engineer)|data engineer|data analyst|analytics engineer|"
    r"product manager|program manager|project manager|designer|\bux\b|\bui\b|"
    r"game|gameplay|graphics|firmware|embedded|hardware|asic|silicon|"
    r"sales|account executive|solutions engineer|marketing|recruit|"
    r"accountant|financial analyst|security (engineer|software engineer)|detection|"
    r"offensive|red team|malware|blockchain|smart contract|quant)\b", re.I)
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
# Major US tech hubs (no state code) so a bare-city location still counts as US, not unknown.
US_CITY_RE = re.compile(r"\b(san francisco|sf bay|bay area|new york|nyc|brooklyn|seattle|bellevue|"
                        r"redmond|boston|chicago|denver|boulder|atlanta|los angeles|santa monica|"
                        r"san jose|palo alto|mountain view|sunnyvale|cupertino|menlo park|san mateo|"
                        r"santa clara|oakland|reston|herndon|mclean|philadelphia|pittsburgh|miami|"
                        r"portland|salt lake|phoenix|nashville|charlotte|raleigh|durham|columbus|"
                        r"minneapolis|san diego|sacramento|irvine|culver city|jersey city)\b", re.I)

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
    "incident response. On F-1 OPT — needs future H-1B sponsorship. Houston TX; remote or relocate. "
    "TARGET ROLES (score domain HIGH): Cloud Engineer, Cloud Support Engineer, DevOps Engineer, "
    "Site Reliability Engineer (SRE), Platform Engineer, Infrastructure Engineer, Systems Engineer, "
    "Cloud Operations, Solutions Architect (associate/entry). NOT A FIT (score domain LOW): "
    "frontend, mobile, full-stack or backend product engineering, data science / ML / AI, data "
    "engineering, security engineering, embedded / firmware / hardware, and any senior/staff/principal role."
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
    """A role that genuinely fits the candidate (cloud / DevOps / SRE / support /
    platform / infra) — not an internship, and not an off-target specialty."""
    t = title or ""
    if INTERN_RE.search(t) or OFFTARGET_RE.search(t):
        return False
    return bool(ROLE_RE.search(t) or SWE_INFRA_RE.search(t))


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
    if US_POS_RE.search(loc) or US_STATE_RE.search(loc) or US_CITY_RE.search(loc):
        return 2
    return 3


def _base_score(o):
    title, jd = o["title"], (o.get("jd") or "").lower()
    s = 40 if (ROLE_RE.search(title) or SWE_INFRA_RE.search(title)) else 0
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
        f"CANDIDATE PROFILE:\n{PROFILE}\n\n"
        f"JOB POSTING:\nCompany: {o['company']}\nTitle: {o['title']}\n"
        f"Location: {o['location']}\nDescription:\n{(o.get('jd') or '')[:3000]}\n\n"
        "Act as an ATS + technical recruiter screening this candidate for THIS job. Score "
        "strictly and honestly from the two texts — do NOT inflate; an entry-level candidate "
        "applying to a senior/mismatched role should score low. Rate each dimension 0-100:\n"
        "- required: how many of the JD's MUST-HAVE hard skills/tools the profile evidences\n"
        "- preferred: coverage of the JD's nice-to-have skills\n"
        "- experience: years / level / scope fit (candidate is early-career: ~2 yrs + an M.S.)\n"
        "- domain: role/industry relevance (cloud / DevOps / SRE / support / systems)\n"
        "- ats: share of the JD's key hard keywords (exact terms + common synonyms) in the profile\n"
        "Reply with ONLY compact JSON, no prose: {\"required\":int,\"preferred\":int,"
        "\"experience\":int,\"domain\":int,\"ats\":int,\"reason\":str (one short sentence naming "
        "the single biggest strength or gap),\"sponsorRisk\":\"low\"|\"med\"|\"high\" (high if the "
        "JD hints at no-sponsorship / citizenship / clearance)}")
    payload = {"anthropic_version": "bedrock-2023-05-31", "max_tokens": 260, "temperature": 0.2,
               "messages": [{"role": "user", "content": [{"type": "text", "text": prompt}]}]}
    resp = bedrock.invoke_model(modelId=BEDROCK_MODEL, body=json.dumps(payload))
    txt = json.loads(resp["body"].read())["content"][0]["text"]
    a, b = txt.find("{"), txt.rfind("}")
    r = json.loads(txt[a:b + 1])
    # Weighted match score (computed here from the rubric, not a single model guess).
    total = sum(max(0, min(100, int(r.get(k, 0) or 0))) * w for k, w in FIT_WEIGHTS.items())
    fit = round(total / sum(FIT_WEIGHTS.values()))
    return {"fit": max(0, min(100, fit)), "reason": str(r.get("reason", ""))[:200],
            "sponsorRisk": r.get("sponsorRisk", "med")}


def handler(event, _ctx):
    openings, errors = collect()
    for o in openings:
        o["blocked"], o["sponsorNote"] = _sponsor(o.get("jd") or "")
        o["fit"] = _base_score(o)   # cheap screen — picks who gets a real rubric score
        o["geo"] = _geo_tier(o)
    # Rubric-score the strongest candidates by the cheap screen (best first, NOT geo —
    # geo must not starve the scoring budget). Only these get an accurate weighted match
    # score + reason, and only these are eligible to be stored, so nothing shown is a
    # keyword guess.
    openings.sort(key=lambda o: o["fit"], reverse=True)
    scored = openings[:MAX_BEDROCK]
    for o in scored:
        try:
            r = _bedrock_fit(o)
            o["fit"] = min(r["fit"], 25) if o["blocked"] else r["fit"]
            o["reason"] = r["reason"]
            o["sponsorRisk"] = "high" if o["blocked"] else r["sponsorRisk"]
        except Exception as e:  # noqa: BLE001 — unscored -> below the bar, won't store; retried next scan
            o["fit"], o["reason"] = 0, ""
            o["sponsorRisk"] = "high" if o["blocked"] else "med"
            errors.append(f"bedrock:{type(e).__name__}")

    # Quality bar + geo priority (TX -> remote -> rest-of-US), best match first, capped.
    keep = [o for o in scored if o["fit"] >= KEEP_MIN_FIT]
    keep.sort(key=lambda o: (o["geo"], -o["fit"]))
    keep = keep[:MAX_STORE]

    now = int(time.time())
    stored = 0
    for o in keep:
        oid = hashlib.sha1(o["url"].encode()).hexdigest()[:16]
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
    print(f"openings scan: collected={len(openings)} stored={stored} errors={errors[:12]}")
    return {"collected": len(openings), "stored": stored, "errors": errors[:12]}
