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
MAX_STORE = 500         # effectively uncapped — filters + sort handle the volume
# TWO-STAGE scoring: (1) free deterministic pre-filter (_content_score) does all the
# dedup/suppression and narrows ~10k postings to the best few hundred; (2) Bedrock reads
# each real JD and returns a TRUSTWORTHY match % + sponsorship read (the number that
# actually agrees with pasting the JD into Claude). Only the top MAX_AI real-JD candidates
# are AI-scored (cost control ~$8-11/mo); title-only feed rows keep the deterministic score.
MAX_AI = 120            # Bedrock calls per scan (cost cap)
AI_PREBAR = 42          # only AI-score candidates whose deterministic pre-score clears this
# Weighted early-career rubric — the Lambda computes the % from the model's dimension scores.
FIT_WEIGHTS = {"required": 40, "preferred": 15, "experience": 20, "domain": 15, "ats": 10}

# The candidate profile the JD is matched against (kept in sync with the résumé corpus).
PROFILE = (
    "CANDIDATE: entry-level Cloud / DevOps engineer. M.S. Computer & Systems Engineering "
    "(Dec 2025); AWS Solutions Architect Associate + Cloud Practitioner. ~2 years as a DevOps "
    "Engineer in AWS cloud operations at HCLTech (ECS Fargate/ALB/RDS/Route53, weekly on-call "
    "w/ CloudWatch+PagerDuty & RCAs, GitHub Actions CI/CD, Terraform + drift detection, golden-"
    "signal alarms, Boto3/Lambda/EventBridge/SSM automation, Config/Inspector/GuardDuty/Security "
    "Hub remediation). Portfolio: serverless zero-knowledge file-share, Amazon EKS platform "
    "(K8s/HPA/IRSA), secure container pipeline (ECS/WAF/DevSecOps gates), cloud observability "
    "(CloudWatch/X-Ray/Synthetics/SLOs), cloud-ops recovery lab (EC2/RDS/incident drills). "
    "Systems foundation: C++ multithreading, POSIX sockets. Skills: AWS, Terraform, Docker, "
    "Kubernetes, CI/CD, Linux, Python/Boto3, Bash, SQL, networking, observability/SRE, incident "
    "response. WORK AUTH: F-1 OPT, needs future H-1B sponsorship. Houston TX; relocates anywhere "
    "in the US, remote welcome. BEST-FIT: Cloud / Cloud Support / DevOps / SRE / Platform / "
    "Infrastructure / Systems Engineer, Cloud Operations, Solutions Architect (associate/entry). "
    "WEAK fit: pure frontend/mobile/product-backend, data-science/ML, data engineering, dedicated "
    "security engineering, embedded/hardware, and ANY senior/staff/principal or 4+ years-required role."
)

# Adzuna job-search AGGREGATOR (free key at developer.adzuna.com) — set both env vars to
# enable; it pulls listings from many sources (incl. reposts of LinkedIn/Indeed roles).
ADZUNA_APP_ID = os.environ.get("ADZUNA_APP_ID", "")
ADZUNA_APP_KEY = os.environ.get("ADZUNA_APP_KEY", "")
ADZUNA_QUERIES = ["cloud engineer", "devops engineer", "site reliability engineer",
                  "cloud support engineer", "platform engineer", "infrastructure engineer",
                  "systems engineer", "cloud operations"]

# Public GitHub new-grad listing feeds — each row carries an explicit sponsorship flag (🛂).
GITHUB_FEEDS = [
    ("https://raw.githubusercontent.com/SimplifyJobs/New-Grad-Positions/dev/.github/scripts/listings.json", "github-simplify"),
    ("https://raw.githubusercontent.com/vanshb03/New-Grad-2027/dev/.github/scripts/listings.json", "github-vansh"),
]

# --- target companies by ATS (sponsor-friendly; known no-sponsors excluded) ---
GREENHOUSE = [
    "twilio", "cloudflare", "datadog", "databricks", "mongodb", "elastic", "stripe",
    "coinbase", "robinhood", "airbnb", "reddit", "dropbox", "pinterest", "instacart",
    "affirm", "brex", "figma", "samsara", "gusto", "sofi", "discord", "roblox", "asana",
    "lyft", "grafanalabs", "pagerduty", "newrelic", "sumologic", "cockroachlabs",
    "fivetran", "starburst", "dremio", "vercel", "fastly", "scaleai", "flexport", "faire",
    "airtable", "webflow", "verkada", "nuro", "chime", "anthropic", "rubrik",
    "purestorage", "okta", "zscaler", "yugabyte",
    "cribl", "circleci", "gemini"]
ASHBY = ["confluent", "snowflake", "openai", "ramp", "notion", "linear", "perplexity",
         "cursor", "replit", "render", "supabase", "posthog", "temporal"]
WORKDAY = [
    {"name": "Red Hat", "tenant": "redhat", "dc": "wd5", "site": "Jobs"},
    {"name": "Nvidia", "tenant": "nvidia", "dc": "wd5", "site": "NVIDIAExternalCareerSite"}]
LEVER = ["palantir", "plaid"]
AMAZON_QUERIES = ["cloud support engineer", "support engineer", "site reliability engineer", "devops engineer"]

# --- sponsorship policy (user requires sponsor-ENABLED or LIKELY; drop confirmed no-sponsor) ---
NO_SPONSOR = {"capital one", "jpmorgan", "jpmorgan chase", "gitlab", "zapier"}
BIG_SPONSORS = {"amazon", "amazon web services", "aws", "microsoft", "google", "ibm",
    "nvidia", "salesforce", "cisco", "adobe", "intel", "qualcomm", "paypal", "visa",
    "mastercard", "stripe", "databricks", "snowflake", "mongodb", "confluent", "cloudflare",
    "datadog", "oracle", "sap", "servicenow", "atlassian", "deloitte", "accenture",
    "capgemini", "cognizant", "infosys", "tcs", "wipro", "kyndryl", "red hat", "lseg"}
# ATS-sourced companies are curated sponsor-friendly, so treat them as "likely" too.
SPONSOR_FRIENDLY = {c.lower() for c in GREENHOUSE + ASHBY + LEVER} | BIG_SPONSORS
CAP_EXEMPT_RE = re.compile(
    r"\b(university|univ\.|college|institute of technology|\binstitute\b|school of|"
    r"hospital|health system|healthcare|medical center|medical college|cancer center|"
    r"\bclinic\b|md anderson|nonprofit|research institute|methodist|baylor college)\b", re.I)
# Staffing / bodyshop firms (aggregators flood with these) — flagged + down-ranked.
STAFFING_RE = re.compile(
    r"\b(staffing|consultanc|consulting|recruit|talent|resourc(es|ing)|it services|"
    r"infotech|soft\s?tech|placements?|manpower|teksystems|apex systems|cybercoders|"
    r"technologies\s+llc|solutions\s+llc|systems\s+llc|global soft)\b", re.I)
STAFFING_SUFFIX_RE = re.compile(r"\b(llc|inc\.?|corp\.?|corporation|group)\s*$", re.I)


def _is_staffing(company):
    c = company or ""
    if STAFFING_RE.search(c):
        return True
    return bool(STAFFING_SUFFIX_RE.search(c)) and _norm(c) not in SPONSOR_FRIENDLY


# Strong target titles — used to score JD-less feed rows (they carry no description).
TARGET_TITLE_RE = re.compile(
    r"(cloud|devops|dev ops|sre|site\s*reliability|reliability|platform|infrastructure|"
    r"systems?)[\w /,-]*\b(engineer|administrator|architect|operations|developer)\b|"
    r"\b(cloud support|technical support|cloud operations|solutions? architect|systems? admin)", re.I)

# --- role / level / location matching ----------------------------------------
# We DON'T gate on the title — every non-intern posting is scored by how much of the
# candidate's stack its full JD mentions (see _content_score). This is just a SOFT
# engineering-role hint that nudges the score, never a filter.
ROLE_HINT_RE = re.compile(
    r"\b(engineer|developer|sre|devops|architect|operations|administrator|reliability|"
    r"infrastructure|platform|cloud|systems|sysadmin|support|automation|network)\b", re.I)
# TIGHTER cloud/infra hint — used to gate JD-less feed rows (title-only) so generic
# "Electrical Engineer" / "Silicon" new-grad roles don't clear the bar on a bare title.
CLOUD_HINT_RE = re.compile(
    r"\b(cloud|devops|dev ops|sre|site\s*reliability|reliability|infrastructure|platform|"
    r"systems?|sysadmin|kubernetes|k8s|observability|devsecops|release engineer|"
    r"cloud support|technical support|network operations|\bnoc\b)\b", re.I)
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


def from_lever(company):
    out = []
    d = json.loads(_get(f"https://api.lever.co/v0/postings/{company}?mode=json"))
    for j in d:
        title = j.get("text", "")
        if not _collectible(title):
            continue
        loc = (j.get("categories") or {}).get("location", "") or ""
        if not _is_us(loc):
            continue
        out.append({"company": company.title(), "title": title, "location": loc,
                    "url": j.get("hostedUrl") or j.get("applyUrl", ""), "source": "lever",
                    "jd": _clean_html(j.get("descriptionPlain") or j.get("description", "")),
                    "postedAt": int((j.get("createdAt") or 0) / 1000)})
    return out


def from_github_feed(url, source):
    """Public new-grad listing feeds (Simplify / vanshb03) — carry an explicit sponsorship
    field, so we hard-exclude no-sponsor rows at the source. No JD (title-only)."""
    out = []
    data = json.loads(_get(url, timeout=15))
    rows = data if isinstance(data, list) else data.get("listings", [])
    for j in rows:
        if j.get("active") is False or j.get("is_visible") is False:
            continue
        title = j.get("title", "")
        if not _collectible(title):
            continue
        locs = j.get("locations") or []
        loc = ", ".join(locs) if isinstance(locs, list) else str(locs)
        if not _is_us(loc):
            continue
        sp = (j.get("sponsorship") or "").strip()
        if sp in ("Does Not Offer Sponsorship", "U.S. Citizenship is Required"):
            continue  # hard sponsorship exclude at the source
        out.append({"company": j.get("company_name", "") or "", "title": title,
                    "location": loc, "url": j.get("url", "") or "", "source": source,
                    "jd": "", "feedSponsor": sp,
                    "postedAt": int(j.get("date_posted") or j.get("date_updated") or 0)})
    return out


def from_adzuna():
    """Aggregator (opt-in via env key) — pulls broadly across sources, incl. reposts of
    LinkedIn/Indeed roles. No sponsorship field, so sponsorship is inferred downstream."""
    out = []
    if not (ADZUNA_APP_ID and ADZUNA_APP_KEY):
        return out
    for q in ADZUNA_QUERIES:
        try:
            url = ("https://api.adzuna.com/v1/api/jobs/us/search/1?" + urllib.parse.urlencode({
                "app_id": ADZUNA_APP_ID, "app_key": ADZUNA_APP_KEY, "results_per_page": 50,
                "what": q, "max_days_old": 21, "content-type": "application/json"}))
            d = json.loads(_get(url, timeout=15))
        except Exception:  # noqa: BLE001 — one query failing never sinks the source
            continue
        for j in d.get("results", []):
            title = _clean_html(j.get("title", ""))
            if not _collectible(title):
                continue
            loc = (j.get("location") or {}).get("display_name", "")
            if not _is_us(loc):
                continue
            out.append({"company": (j.get("company") or {}).get("display_name", "") or "",
                        "title": title, "location": loc, "url": j.get("redirect_url", ""),
                        "source": "adzuna", "jd": _clean_html(j.get("description", "")),
                        "postedAt": 0})
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
    for co in LEVER:
        try:
            raw += from_lever(co)
        except Exception as e:  # noqa: BLE001
            errors.append(f"lever:{co}:{type(e).__name__}")
    for cfg in WORKDAY:
        try:
            raw += from_workday(cfg)
        except Exception as e:  # noqa: BLE001
            errors.append(f"wd:{cfg['name']}:{type(e).__name__}")
    for feed_url, src in GITHUB_FEEDS:
        try:
            raw += from_github_feed(feed_url, src)
        except Exception as e:  # noqa: BLE001
            errors.append(f"{src}:{type(e).__name__}")
    try:
        raw += from_amazon()
    except Exception as e:  # noqa: BLE001
        errors.append(f"amazon:{type(e).__name__}")
    try:
        raw += from_adzuna()
    except Exception as e:  # noqa: BLE001
        errors.append(f"adzuna:{type(e).__name__}")
    # dedupe by url; cap JD length (we now collect whole boards, not just title matches)
    seen, uniq = set(), []
    for o in raw:
        if o["url"] and o["url"] not in seen:
            seen.add(o["url"])
            o["jd"] = (o.get("jd") or "")[:6000]
            uniq.append(o)
    return uniq, errors


# --- scoring -----------------------------------------------------------------

def _sponsor_verdict(o):
    """Enforce the sponsor-ENABLED-or-LIKELY rule. Returns (blocked, risk, reason, cap_exempt):
    blocked=True → confirmed no-sponsorship (DROPPED); risk 'low' = enabled/likely, 'med' = unverified."""
    jd = o.get("jd") or ""
    company_raw = o.get("company") or ""
    company = _norm(company_raw)
    cap = bool(CAP_EXEMPT_RE.search(company_raw))
    if NEG_RE.search(jd) or CITIZEN_RE.search(jd):
        return True, "high", "JD excludes sponsorship / needs citizen or clearance", cap
    if company in NO_SPONSOR:
        return True, "high", "Employer documented as not sponsoring", cap
    if cap:
        return False, "low", "Cap-exempt employer — H-1B lottery-proof", True
    if (o.get("feedSponsor") or "") == "Offers Sponsorship":
        return False, "low", "Listing explicitly offers visa sponsorship", False
    if company in SPONSOR_FRIENDLY or o.get("source", "") in ("greenhouse", "ashby", "amazon", "workday", "lever"):
        return False, "low", "Known sponsor-friendly employer", False
    return False, "med", "Sponsorship unverified — check the posting / 🛂 tool", False


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


# Experience requirement in the JD — the #1 honesty fix. An entry candidate against a
# "5+ years" JD is a poor fit no matter how many stack keywords overlap.
REQ_YEARS_RE = re.compile(r"(\d{1,2})\s*\+?\s*(?:to|-|–|—)?\s*\d{0,2}\s*years?\b", re.I)
# Seniority signalled in the JD BODY (not just the title).
SENIOR_JD_RE = re.compile(
    r"\b(senior|staff|principal|\blead\b|expert|advanced|architect|"
    r"extensive experience|proven track record|deep expertise|"
    r"([5-9]|1[0-9])\s*\+?\s*years)\b", re.I)


def _req_years(jd):
    yrs = [int(m) for m in REQ_YEARS_RE.findall(jd) if 0 < int(m) <= 20]
    return max(yrs) if yrs else 0


def _content_score(o):
    """Deterministic match score (0-100) — an HONEST early-career fit signal, not a keyword count.
    High scores require a real cloud/DevOps/SRE/support/systems role AND no seniority/years mismatch;
    the JD's experience requirement and seniority language pull it down (that's why an 'Architect' or
    '5+ years' role scores low even with heavy keyword overlap). Still $0 — no AI call."""
    title = (o.get("title") or "").lower()
    jd = (o.get("jd") or "")
    jdl = jd.lower()
    text = title + " \n " + jdl
    hits = sum(1 for k in SKILL_KW if k in text)   # distinct stack terms present
    strong = bool(TARGET_TITLE_RE.search(title))   # genuine cloud/devops/sre/platform/infra/systems eng
    cloud = bool(CLOUD_HINT_RE.search(title))

    if len(jdl) >= 200:                            # real JD → content-driven, domain-gated
        s = min(66, hits * 5)
        s += 16 if strong else (8 if cloud else -12)  # generic SWE w/ a few infra words: demote
    else:                                          # title-only feed row → a guess, capped lower
        s = 56 if strong else (46 if cloud else 14)
        s += min(12, hits * 4)

    # Seniority / years — the honesty fixes
    yrs = _req_years(jd)
    if yrs >= 6:
        s -= 45
    elif yrs >= 4:
        s -= 28
    elif yrs == 3:
        s -= 12
    if SENIOR_RE.search(title):
        s -= 45          # senior/staff/lead in the title → out of range for an entry candidate
    elif SENIOR_JD_RE.search(jd):
        s -= 20          # seniority signalled in the JD body
    if JUNIOR_RE.search(title):
        s += 12          # explicit entry / associate / new-grad
    if OFFTARGET_RE.search(title):
        s -= 40          # frontend/backend/sales/ops-associate/etc.
    return max(0, min(100, s))


def _ai_score(o):
    """Bedrock reads the FULL JD and scores early-career fit honestly (the number that agrees
    with pasting the JD into Claude). Returns {fit, reason, sponsorRisk}. Raises on API error
    so the caller can fall back to the deterministic score."""
    prompt = (
        f"CANDIDATE PROFILE:\n{PROFILE}\n\n"
        f"JOB POSTING:\nCompany: {o.get('company','')}\nTitle: {o.get('title','')}\n"
        f"Location: {o.get('location','')}\nFull description:\n{(o.get('jd') or '')[:3500]}\n\n"
        "Act as a strict ATS + technical recruiter. READ THE FULL JD — titles lie both ways. Score "
        "how well THIS early-career candidate fits THIS job. Be harsh about seniority and required "
        "years: a role needing 4+ years, or a senior/staff/principal/architect role, is a POOR fit "
        "even with heavy keyword overlap. Rate each 0-100:\n"
        "- required: JD must-have hard skills the candidate evidences\n"
        "- preferred: nice-to-have coverage\n"
        "- experience: years/level/scope fit (candidate is ~2 yrs + M.S., entry/associate only)\n"
        "- domain: is this genuinely cloud/DevOps/SRE/support/systems (vs frontend/backend/ML/data/security)\n"
        "- ats: share of the JD's key hard terms present in the profile\n"
        "Reply ONLY compact JSON: {\"required\":int,\"preferred\":int,\"experience\":int,"
        "\"domain\":int,\"ats\":int,\"reason\":str (one short sentence, name the biggest strength or "
        "gap),\"sponsorRisk\":\"low\"|\"med\"|\"high\" (high if the JD needs citizenship/clearance or "
        "excludes visa sponsorship; low if it welcomes new grads / mentions sponsorship)}")
    payload = {"anthropic_version": "bedrock-2023-05-31", "max_tokens": 260, "temperature": 0.2,
               "messages": [{"role": "user", "content": [{"type": "text", "text": prompt}]}]}
    resp = bedrock.invoke_model(modelId=BEDROCK_MODEL, body=json.dumps(payload))
    txt = json.loads(resp["body"].read())["content"][0]["text"]
    a, b = txt.find("{"), txt.rfind("}")
    r = json.loads(txt[a:b + 1])
    total = sum(max(0, min(100, int(r.get(k, 0) or 0))) * w for k, w in FIT_WEIGHTS.items())
    fit = round(total / sum(FIT_WEIGHTS.values()))
    return {"fit": max(0, min(100, fit)), "reason": str(r.get("reason", ""))[:200],
            "sponsorRisk": r.get("sponsorRisk", "med")}


def _reason(o):
    """A short, honest 'why it matched' — stack terms present in the JD, else the sponsor note."""
    text = ((o.get("title") or "") + " " + (o.get("jd") or "")).lower()
    matched = [k for k in SKILL_KW if k in text]
    if matched:
        shown = ", ".join(matched[:6])
        more = f" +{len(matched) - 6} more" if len(matched) > 6 else ""
        return f"Matches your stack: {shown}{more}. Verify the full JD before applying."
    return o.get("sponsorNote") or ""


def handler(event, _ctx):
    openings, errors = collect()
    suppressed_ids, applied = _load_suppressions()
    for o in openings:
        o["oid"] = hashlib.sha1(o["url"].encode()).hexdigest()[:16]
        o["blocked"], o["sponsorRisk"], o["sponsorNote"], o["capExempt"] = _sponsor_verdict(o)
        o["staffing"] = _is_staffing(o.get("company"))
        o["content"] = _content_score(o)
        o["geo"] = _geo_tier(o)
    # Drop before scoring: confirmed no-sponsorship (user requires sponsor-enabled/likely),
    # dismissed/tracked openings, roles already in the tracker, and near-duplicate postings
    # (same company+title across sources) — keeping the best-overlap one of each.
    best, dropped = {}, {"nosponsor": 0, "suppressed": 0, "applied": 0, "dup": 0}
    for o in sorted(openings, key=lambda o: -o["content"]):
        if o["blocked"]:
            dropped["nosponsor"] += 1
            continue
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
    # Stage 1 — deterministic pre-score (free). This IS the fallback fit. Aggregator/staffing
    # rows are down-ranked so they never top the AI queue.
    for o in best.values():
        pen = 15 if (o["source"] == "adzuna" and o["sponsorRisk"] == "med") else 0
        pen += 12 if o.get("staffing") else 0
        o["pre"] = max(0, o["content"] - pen)
        o["fit"] = o["pre"]
        o["reason"] = _reason(o)
        o["scoredBy"] = "keyword"

    # Stage 2 — AI: Bedrock reads the FULL JD for the top real-JD candidates and returns a
    # trustworthy match % (agrees with pasting the JD into Claude). Title-only feed rows have
    # no JD to read, so they keep the deterministic title score. Bounded by MAX_AI for cost;
    # on any Bedrock error the deterministic score stands (a blip never empties the tab).
    ai_pool = [o for o in best.values() if len(o.get("jd") or "") >= 200 and o["pre"] >= AI_PREBAR]
    ai_pool.sort(key=lambda o: -o["pre"])
    ai_used = 0
    for o in ai_pool[:MAX_AI]:
        try:
            r = _ai_score(o)
            o["fit"], o["reason"] = r["fit"], (r["reason"] or o["reason"])
            o["sponsorRisk"] = "high" if o.get("capExempt") is False and r["sponsorRisk"] == "high" else (
                "low" if o.get("capExempt") else r["sponsorRisk"])
            o["scoredBy"] = "ai"
            ai_used += 1
        except Exception as e:  # noqa: BLE001 — keep the deterministic score on error
            errors.append(f"ai:{type(e).__name__}")

    # Quality bar (>=50% match) + geo priority (TX -> remote -> rest-of-US); NO count cap.
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
        rec = {k: o.get(k) for k in ("company", "title", "location", "url", "source", "fit", "geo",
                                     "reason", "sponsorNote", "sponsorRisk", "capExempt", "staffing",
                                     "scoredBy", "postedAt")}
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
    print(f"openings scan: collected={len(openings)} deduped={len(best)} ai_scored={ai_used} "
          f"stored={stored} dropped={dropped} errors={errors[:12]}")
    return {"collected": len(openings), "aiScored": ai_used, "stored": stored,
            "dropped": dropped, "errors": errors[:12]}
