"""Visa-sponsorship intelligence for the job tracker.

Fuses three signals into one verdict so the user checks sponsorship in ONE place
instead of bouncing between h1bdata / myvisajobs / h1bgrader:

  1. JD language  — a deterministic scan for the phrases that grant sponsorship
                    or that deny it / require US citizenship or a clearance.
  2. Known lists  — a curated map of employers whose posture is already
                    documented (strong sponsors, new-grad no-sponsors, cap-exempt
                    orgs, offshore-caution, defense/clearance).
  3. H-1B history — a live lookup of the company's LCA filings on h1bdata.info
                    (public DOL data), parsed for count / year / titles / wage,
                    with a tech-title count so "sponsors, but only petroleum
                    engineers" doesn't read as a green light.

This module is import-safe: no boto3, no network at import time. The only network
call is fetch_h1b(), guarded by a short timeout, so the rest unit-tests offline.
"""
import html
import re
import statistics
import urllib.parse
import urllib.request

# --- curated employer knowledge (from H-1B data + documented policies) --------
# Matched as substrings against a normalized (lowercased, de-punctuated) company
# name. Keep entries lowercase and distinctive to avoid collisions.

STRONG_SPONSORS = {
    "amazon", "aws", "google", "microsoft", "meta", "facebook", "red hat",
    "snowflake", "ibm", "oracle", "dell", "tesla", "datadog", "mongodb",
    "cloudflare", "twilio", "confluent", "nvidia", "salesforce", "adobe",
    "vmware", "intel", "qualcomm", "cisco", "workday", "atlassian", "stripe",
    "databricks", "palo alto networks", "servicenow", "splunk", "elastic",
    "hashicorp", "charles schwab", "fidelity", "usaa", "rackspace", "ntt data",
    "toyota", "texas instruments", "deloitte", "goldman sachs", "bloomberg",
    "visa inc", "mastercard", "paypal", "uber", "airbnb", "linkedin",
}

# Documented as NOT sponsoring new grads (their reputations mislead — verified).
NO_SPONSOR = {
    "capital one": "Capital One's new-grad / TDP postings explicitly refuse sponsorship (incl. OPT/CPT).",
    "jpmorgan": "JPMorgan's Software Engineer Program states no employment-based sponsorship (incl. OPT/CPT).",
    "jp morgan": "JPMorgan's Software Engineer Program states no employment-based sponsorship (incl. OPT/CPT).",
    "gitlab": "GitLab has no H-1B filing history and requires existing US work authorization.",
    "zapier": "Zapier does not sponsor — postings require existing US work authorization.",
}

# High H-1B volume but mostly renewals / offshore transfers, high denial rates,
# below-market wages — long shots for a day-one new-grad cap case.
OFFSHORE_CAUTION = {
    "cognizant", "infosys", "tata consultancy", "tcs", "wipro", "hcl",
    "capgemini", "tech mahindra", "ltimindtree", "mindtree", "mphasis",
    "larsen", "birlasoft", "hexaware", "syntel", "igate",
}

# Defense / clearance primes — roles typically require US-person status.
CLEARANCE_EMPLOYERS = {
    "booz allen", "spacex", "blue origin", "lockheed", "raytheon", "rtx corp",
    "northrop", "general dynamics", "leidos", "saic", "anduril", "l3harris",
    "peraton", "caci", "mitre", "draper", "parsons corp",
}

# Cap-exempt (H-1B lottery-exempt): apply year-round, no lottery gamble.
CAP_EXEMPT_NAMED = {
    "md anderson", "university of houston", "rice university", "baylor college",
    "texas a&m", "ut health", "ut southwestern", "houston methodist",
    "memorial hermann", "texas medical center",
}
CAP_EXEMPT_HINTS = (
    "university", "college", " univ ", "institute of technology", "medical center",
    "cancer center", "children's hospital", "health system", "school district",
    "research institute", "teaching hospital", "state university", " isd",
)

# --- JD language scan ---------------------------------------------------------

_NEG_PATTERNS = [
    r"not\s+(?:be\s+)?able\s+to\s+sponsor",
    r"un(?:able|willing)\s+to\s+sponsor",
    r"(?:does|do|will|can|are)\s*n[o']?t\s+(?:currently\s+|be\s+able\s+to\s+)?(?:provide|offer|sponsor|support)\w*\s+\w*\s*sponsor?\w*",
    r"(?:will|can|do|does)\s*not\s+sponsor",
    r"no\s+(?:visa\s+)?sponsorship(?:\s+is)?(?:\s+available)?",
    r"without\s+(?:the\s+need\s+for\s+|current\s+or\s+future\s+|requiring\s+|now\s+or\s+in\s+the\s+future\s+for\s+)?(?:employer\s+|visa\s+)?sponsorship",
    r"sponsorship\s+is\s+not\s+(?:available|offered|provided)",
    r"not\s+eligible\s+for\s+(?:visa\s+)?sponsorship",
    r"we\s+(?:do|are)\s+not\s+(?:able\s+to\s+)?sponsor",
    r"authoriz\w+\s+to\s+work\s+in\s+the\s+u(?:\.?s\.?|nited\s+states)[^.]{0,70}?without[^.]{0,30}?sponsor",
    r"must\s+not\s+require\s+sponsorship",
    r"unable\s+to\s+provide\s+(?:visa\s+)?sponsorship",
]
_CITIZEN_PATTERNS = [
    r"u\.?s\.?\s+citizen(?:ship)?", r"citizenship\s+(?:is\s+)?required",
    r"must\s+be\s+a\s+u\.?s\.?\s+(?:citizen|person)", r"security\s+clearance",
    r"\bitar\b", r"government\s+clearance", r"active\s+(?:ts/sci|secret|clearance)",
    r"public\s+trust", r"green\s+card\s+(?:holder\s+)?(?:or\s+citizen|required)",
]
_POS_PATTERNS = [
    r"visa\s+sponsorship\s+(?:is\s+)?(?:available|offered|provided)",
    r"will(?:ing\s+to)?\s+sponsor", r"we\s+(?:will\s+)?sponsor",
    r"sponsorship\s+(?:is\s+)?(?:available|provided|offered)",
    r"open\s+to\s+sponsor\w*", r"\bstem[\s-]?opt\b",
    r"\bopt\s+(?:students?|candidates?|welcome|eligible|accepted)",
    r"\bh[\s-]?1[\s-]?b\b\s+sponsor", r"cpt\s*/\s*opt", r"e-verify",
]


def _windows(text, patterns):
    hits = []
    low = text.lower()
    for pat in patterns:
        m = re.search(pat, low)
        if m:
            a, b = max(0, m.start() - 25), min(len(text), m.end() + 25)
            snippet = re.sub(r"\s+", " ", text[a:b]).strip()
            hits.append(snippet)
    return hits


def scan_jd(jd_text):
    """Return {'neg','citizen','pos'} lists of matched JD snippets."""
    jd_text = jd_text or ""
    return {
        "neg": _windows(jd_text, _NEG_PATTERNS),
        "citizen": _windows(jd_text, _CITIZEN_PATTERNS),
        "pos": _windows(jd_text, _POS_PATTERNS),
    }


# --- H-1B history (h1bdata.info) ----------------------------------------------

_TECH_TITLE = re.compile(
    r"engineer|developer|devops|sre|reliab|cloud|software|architect|"
    r"system|sysops|\bdata\b|platform|infrastructure|network|security|"
    r"\bit\b|information technology|programmer|python|java|full[\s-]?stack|"
    r"back[\s-]?end|front[\s-]?end|machine learning|\bml\b|\bai\b|analyst",
    re.I,
)


def parse_h1b(page, cap_rows=4000):
    """Parse an h1bdata.info result page into a compact summary dict."""
    rows = re.findall(r"<tr[^>]*>(.*?)</tr>", page, re.S)
    parsed = []
    for r in rows:
        tds = re.findall(r"<td[^>]*>(.*?)</td>", r, re.S)
        if len(tds) >= 5:
            cells = [html.unescape(re.sub(r"<[^>]+>", "", t)).strip() for t in tds]
            parsed.append(cells)
        if len(parsed) >= cap_rows:
            break
    if not parsed:
        return {"ok": True, "count": 0, "capped": False, "techCount": 0, "recentYear": None,
                "yearSpan": None, "medianSalary": None, "medianTechSalary": None,
                "topTitles": [], "employer": None}

    salaries, tech_salaries, years, titles, employers, tech_n = [], [], [], [], [], 0
    for c in parsed:
        emp, title, sal = c[0], c[1], c[2]
        employers.append(emp)
        titles.append(title)
        is_tech = bool(_TECH_TITLE.search(title))
        if is_tech:
            tech_n += 1
        val = _money(sal)
        if val:
            salaries.append(val)
            if is_tech:
                tech_salaries.append(val)
        # START DATE is the last date-looking column
        for cell in c[3:]:
            ym = re.search(r"\b(20\d{2})\b", cell)
            if ym:
                years.append(int(ym.group(1)))
    yrs = sorted(set(years))
    top = _top(titles, 4)
    return {
        "ok": True,
        "count": len(parsed),
        "capped": len(parsed) >= cap_rows,
        "techCount": tech_n,
        "recentYear": (yrs[-1] if yrs else None),
        "yearSpan": (f"{yrs[0]}–{yrs[-1]}" if len(yrs) > 1 else (str(yrs[0]) if yrs else None)),
        "medianSalary": (int(statistics.median(salaries)) if salaries else None),
        "medianTechSalary": (int(statistics.median(tech_salaries)) if tech_salaries else None),
        "topTitles": top,
        "employer": _top(employers, 1)[0][0] if employers else None,
    }


def _money(s):
    digits = re.sub(r"[^\d]", "", s or "")
    try:
        n = int(digits)
    except ValueError:
        return None
    return n if 10_000 <= n <= 2_000_000 else None


def _top(items, n):
    counts = {}
    for it in items:
        k = re.sub(r"\s+", " ", (it or "").title()).strip()
        if k:
            counts[k] = counts.get(k, 0) + 1
    return sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))[:n]


def _simplify_name(company):
    """Drop 'The' + legal suffixes and normalize 'and'→'&' so a near-miss name
    (h1bdata does a contains-match) gets a second chance."""
    n = re.sub(r"^the\s+", "", company.strip(), flags=re.I)
    n = re.sub(r"\s+(inc|llc|l\.l\.c\.|corp|corporation|company|co|ltd|plc|group|holdings)\.?$", "", n, flags=re.I)
    n = n.replace(" and ", " & ")
    return n.strip()


def _fetch_one(query, timeout):
    url = "https://h1bdata.info/index.php?em=" + urllib.parse.quote(query)
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (job-tracker sponsorship check)"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        raw = resp.read(4_000_000)
    return parse_h1b(raw.decode("utf-8", "replace"))


def fetch_h1b(company, timeout=6.0):
    """Live LCA lookup. Never raises — returns {'ok':False,...} on any failure.
    If the exact name finds nothing, retries once with a simplified name."""
    try:
        res = _fetch_one(company.strip(), timeout)
        if res["count"] == 0:
            simple = _simplify_name(company)
            if simple and simple.lower() != company.strip().lower():
                alt = _fetch_one(simple, timeout)
                if alt["count"] > 0:
                    alt["matchedVia"] = simple
                    return alt
        return res
    except Exception as e:  # noqa: BLE001 — public site; degrade gracefully
        return {"ok": False, "error": type(e).__name__, "count": 0, "capped": False,
                "techCount": 0, "recentYear": None, "yearSpan": None, "medianSalary": None,
                "medianTechSalary": None, "topTitles": [], "employer": None}


# --- verdict resolution -------------------------------------------------------

_LABELS = {
    "likely": "Likely sponsors", "possible": "Possibly sponsors",
    "capexempt": "Cap-exempt — no lottery", "caution": "High volume, low odds",
    "rare": "Rarely sponsors", "none": "No H-1B record", "unlikely": "Unlikely to sponsor",
    "unknown": "Unknown",
}
_POSITIVE = {"likely", "possible", "capexempt"}


def _norm(name):
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9& ]", " ", (name or "").lower())).strip()


def _is_cap_exempt(name):
    if any(k in name for k in CAP_EXEMPT_NAMED):
        return True
    return any(h in f" {name} " for h in CAP_EXEMPT_HINTS)


def _verdict(level, reasons, scan, h1b, cap=False):
    return {
        "level": level, "label": _LABELS[level], "reasons": reasons,
        "sponsors": level in _POSITIVE, "capExempt": cap,
        "jdSignals": scan,
        "h1b": {k: h1b.get(k) for k in ("ok", "count", "capped", "techCount", "recentYear",
                "yearSpan", "medianSalary", "medianTechSalary", "topTitles", "employer",
                "matchedVia")} if h1b else None,
    }


def resolve(company, jd_text="", h1b=None):
    """Combine curated lists + JD scan + H-1B history into one verdict dict."""
    name = _norm(company)
    scan = scan_jd(jd_text or "")

    # --- hard negatives first (highest confidence) ---
    if scan["citizen"]:
        return _verdict("unlikely", [f"JD requires US-person / clearance: “{scan['citizen'][0]}”"], scan, h1b)
    if any(k in name for k in CLEARANCE_EMPLOYERS):
        return _verdict("unlikely", ["Defense / clearance employer — roles usually require US-person status."], scan, h1b)
    if scan["neg"]:
        return _verdict("unlikely", [f"The JD excludes sponsorship: “{scan['neg'][0]}”"], scan, h1b)
    for k, why in NO_SPONSOR.items():
        if k in name:
            return _verdict("unlikely", [why], scan, h1b)

    # --- cap-exempt (a GOOD outcome: lottery-proof) ---
    if _is_cap_exempt(name):
        r = ["University / nonprofit / hospital — H-1B cap-exempt: apply year-round, no lottery gamble (pay often below market)."]
        if h1b and h1b.get("count"):
            r.append(_h1b_line(h1b))
        return _verdict("capexempt", r, scan, h1b, cap=True)

    # --- positive tiers ---
    reasons, level = [], "unknown"
    if any(k in name for k in OFFSHORE_CAUTION):
        level = "caution"
        reasons.append("Files many H-1Bs, but mostly renewals/transfers with high denial rates and below-market pay — apply, don't rely.")
    elif any(k in name for k in STRONG_SPONSORS):
        level = "likely"
        reasons.append("Documented H-1B sponsor that hires early-career.")

    if h1b and h1b.get("ok") and h1b.get("count"):
        reasons.append(_h1b_line(h1b))
        c, tech = h1b["count"], h1b.get("techCount") or 0
        if level in ("unknown", "rare", "none"):
            level = "likely" if c >= 40 else "possible" if c >= 8 else "rare"
        if tech == 0 and c >= 5:
            reasons.append("⚠ None of those filings are for tech/engineering titles — verify this specific role would be sponsored.")
            if level == "likely":
                level = "possible"
    elif h1b and h1b.get("ok"):
        reasons.append("No H-1B filings found on h1bdata.info for this exact name — try the parent-company name, and check myvisajobs before relying.")
        if level == "unknown":
            level = "none"
    elif h1b and not h1b.get("ok"):
        reasons.append("Couldn't reach the H-1B database just now — verdict is based on JD + known lists only.")

    if scan["pos"]:
        reasons.append(f"The JD mentions sponsorship/visa: “{scan['pos'][0]}”.")
        if level in ("unknown", "none", "rare"):
            level = "possible"

    if not reasons:
        reasons.append("No sponsorship signal either way — check the JD wording and myvisajobs for this employer.")
    return _verdict(level, reasons, scan, h1b)


def _h1b_line(h1b):
    c, tech, yr = h1b.get("count", 0), h1b.get("techCount") or 0, h1b.get("recentYear")
    med = h1b.get("medianTechSalary") or h1b.get("medianSalary")
    emp = h1b.get("employer")
    bits = [f"{c}{'+' if h1b.get('capped') else ''} H-1B/LCA filing{'s' if c != 1 else ''}"]
    if emp:
        bits.append(f"for {emp.title()}")
    if yr:
        bits.append(f"({yr})")
    line = " ".join(bits)
    if tech:
        line += f"; {tech} in tech/engineering roles"
    if med:
        line += f"; median wage ~${med:,}"
    return line + "."


def verify_links(company):
    """Deep-link a company into the three H-1B databases for manual drill-down."""
    q = urllib.parse.quote(company.strip())
    slug = re.sub(r"[^a-z0-9]+", "-", company.lower()).strip("-")
    return {
        "h1bdata": f"https://h1bdata.info/index.php?em={q}",
        "myvisajobs": f"https://www.myvisajobs.com/search/?q={q}",
        "h1bgrader": f"https://h1bgrader.com/search?q={q}&slug={slug}",
    }
