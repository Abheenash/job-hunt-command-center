"""offer — offer-stage companion: salary-gap analysis, a contract clause-walk with a
lawyer question list, and negotiation scripts. All DETERMINISTIC ($0, no AI): the
value here is structure and honest math, not generation. Behind the Cognito JWT via
the API. Pure functions (see test_offer.py); the handler is a thin wrapper.

Routes (mounted by the api Lambda / its own function URL):
  POST /offer/salary-gap  {desired, advertised, actual, location?}  -> gap analysis
  POST /offer/clause-walk {jd_or_offer_text}                        -> clauses + questions
  POST /offer/scripts     {scenario, context}                       -> negotiation scripts
"""
import json
import re

# US metro cost-of-living index (100 = national avg). Used to translate an offer across
# locations so "is $150k in SF better than $130k in Houston?" gets an honest answer.
# Houston is the candidate's base; relocation-anywhere-US is fine, so this is advisory.
COL_INDEX = {
    "houston": 96, "dallas": 101, "austin": 108, "san antonio": 92,
    "san francisco": 179, "sf": 179, "bay area": 172, "san jose": 175, "palo alto": 178,
    "new york": 168, "nyc": 168, "seattle": 152, "boston": 148, "los angeles": 146,
    "san diego": 142, "denver": 118, "chicago": 116, "washington": 152, "dc": 152,
    "atlanta": 107, "raleigh": 104, "charlotte": 102, "phoenix": 107, "portland": 130,
    "remote": 100, "national": 100,
}


def _col(location):
    loc = (location or "").lower()
    for city, idx in COL_INDEX.items():
        if city in loc:
            return idx, city
    return 100, "national"


def salary_gap(desired, advertised, actual, location="", base_location="houston"):
    """Honest three-way read: what you want vs the posted band vs a concrete offer,
    cost-of-living-adjusted to your home base. `advertised` may be a (low, high) tuple/list
    or a single number. Returns a structured verdict + a one-line recommendation."""
    adv_lo = adv_hi = None
    if isinstance(advertised, (list, tuple)) and advertised:
        adv_lo, adv_hi = min(advertised), max(advertised)
    elif isinstance(advertised, (int, float)) and advertised:
        adv_lo = adv_hi = int(advertised)

    idx, city = _col(location)
    base_idx, _ = _col(base_location)
    # Adjust the offer's buying power to the candidate's home base.
    adj_actual = round(actual * base_idx / idx) if actual and idx else actual

    out = {
        "desired": desired or None,
        "advertisedLow": adv_lo, "advertisedHigh": adv_hi,
        "actual": actual or None,
        "location": city, "colIndex": idx,
        "adjustedToBase": adj_actual, "baseLocation": base_location,
        "flags": [], "verdict": "", "recommendation": "",
    }
    if actual and adv_hi and actual < adv_lo:
        out["flags"].append(f"Offer (${actual:,}) is BELOW the posted band (${adv_lo:,}-${adv_hi:,}) — ask why.")
    if actual and adv_hi and actual >= adv_hi:
        out["flags"].append("Offer is at/above the top of the posted band — strong.")
    if actual and desired and actual < desired:
        short = desired - actual
        pct = round(short / desired * 100)
        out["flags"].append(f"${short:,} ({pct}%) below your target — room to counter.")
    if idx > 120 and actual:
        out["flags"].append(f"{city.title()} cost-of-living is {idx} vs national 100 — ${actual:,} feels like ~${adj_actual:,} at your Houston base.")

    if not actual:
        out["verdict"] = "no-offer-yet"
        out["recommendation"] = ("Anchor on the posted band's midpoint or above; let them name a number first."
                                 if adv_hi else "No band posted — research comps (levels.fyi / Glassdoor) before the call and let them anchor.")
    elif desired and actual >= desired:
        out["verdict"] = "at-or-above-target"
        out["recommendation"] = "Meets your target. You can still counter once for signing bonus / start date / PTO."
    elif adv_hi and actual < adv_hi:
        gap_to_top = adv_hi - actual
        out["verdict"] = "below-band-top"
        out["recommendation"] = f"Counter toward the band top — there's ${gap_to_top:,} of posted headroom, so ask for it citing your fit."
    else:
        out["verdict"] = "below-target"
        out["recommendation"] = "Counter with a specific number backed by your comps and the value you bring; be ready to trade equity/bonus."
    return out


# Contract clauses that matter to an early-career candidate — especially an F-1/OPT one.
# Each: what it is, why it matters, and the exact question to ask (or take to a lawyer).
CLAUSE_LIBRARY = [
    ("at-will / termination", r"at[-\s]?will|terminat|for cause",
     "How much notice? Any severance? What counts as 'for cause'?"),
    ("non-compete", r"non[-\s]?compet|restrictive covenant",
     "Scope, geography, and duration? Is it enforceable in this state? Get it reviewed before signing."),
    ("non-solicit", r"non[-\s]?solicit",
     "Does it stop you recruiting former colleagues or clients, and for how long?"),
    ("IP assignment", r"intellectual property|\bIP\b|inventions?|assign(?:ment)?|work product",
     "Does it claim side projects / open-source done on your own time and equipment? Carve out your portfolio repos."),
    ("equity / vesting", r"equity|stock|options?|RSU|vesting|cliff",
     "Strike price, 409A valuation, vesting schedule + cliff, and what happens to unvested shares if you leave?"),
    ("sign-on / clawback", r"sign[-\s]?on|signing bonus|clawback|repay",
     "Is any bonus clawed back if you leave within N months? Get the repayment terms in writing."),
    ("relocation", r"relocat|moving (?:expense|allowance)",
     "Lump sum vs reimbursement? Clawback if you leave early? Timing of payment?"),
    ("PTO / leave", r"\bPTO\b|paid time off|vacation|sick leave|parental",
     "Accrual vs unlimited, rollover, and payout of unused PTO on exit?"),
    ("arbitration", r"arbitrat|dispute resolution|class action waiver",
     "Are you waiving the right to sue / join a class action? This is common but know you're agreeing to it."),
    ("visa / sponsorship", r"sponsor|visa|h-?1b|work authorization|immigration",
     "Get the sponsorship commitment (H-1B filing timing, premium processing, legal fees, green-card support) IN WRITING — verbal promises don't bind."),
    ("start date / contingencies", r"start date|contingent|background check|drug (?:test|screen)",
     "What are the offer contingencies, and is the start date compatible with your OPT / work-auth timeline?"),
]


def clause_walk(text):
    """Scan an offer letter / contract text and surface the clauses present, why each
    matters, and the exact question to ask. Deterministic — a checklist, not legal advice."""
    t = text or ""
    found = []
    for name, rx, question in CLAUSE_LIBRARY:
        if re.search(rx, t, re.I):
            found.append({"clause": name, "question": question})
    # visa is always worth flagging for this candidate even if the letter is silent on it
    if not any(f["clause"] == "visa / sponsorship" for f in found):
        found.append({"clause": "visa / sponsorship (NOT in the letter)",
                      "question": "The letter is silent on sponsorship — get the H-1B commitment added in writing before you sign."})
    return {
        "clausesFound": found,
        "lawyerList": [f["question"] for f in found],
        "disclaimer": "This is a structured checklist, not legal advice. For a real offer, have an immigration/employment attorney review it.",
    }


SCRIPTS = {
    "comp": (
        "Thank you — I'm genuinely excited about the role and the team. Based on my fit "
        "for {role} and comparable roles I'm seeing, I was targeting closer to ${target}. "
        "Is there flexibility to get the base to that number?"),
    "competing": (
        "I want to be transparent: I have another offer at ${other}. {company} is my "
        "first choice, and if we can get closer on base I'd sign today. Can we work toward ${target}?"),
    "geo": (
        "I understand the band may be set for {location}, but I'll be delivering the same "
        "value wherever I'm based, and my cost of living doesn't change the quality of my work. "
        "Could we base comp on the role rather than the metro?"),
    "lowball": (
        "I appreciate the offer. It's below the range I'd expected for this scope — the posting "
        "listed up to ${band_top}. Given my hands-on {skills} experience, could we move the base "
        "toward the top of that band?"),
    "non-monetary": (
        "If base is fixed, could we look at a signing bonus, an earlier comp review at 6 months, "
        "additional PTO, or a remote/hybrid arrangement to close the gap?"),
}


def scripts(scenario, ctx=None):
    ctx = ctx or {}
    key = (scenario or "").lower()
    if key not in SCRIPTS:
        return {"error": f"unknown scenario '{scenario}'", "available": sorted(SCRIPTS)}
    tmpl = SCRIPTS[key]
    filled = tmpl
    for k, v in ctx.items():
        filled = filled.replace("{" + k + "}", str(v))
    return {"scenario": key, "script": filled,
            "note": "Deliver warm, once, with a specific number. Silence after the ask is your friend."}


def handler(event, _ctx):
    path = event.get("rawPath", "")
    try:
        body = json.loads(event.get("body") or "{}")
    except (ValueError, TypeError):
        body = {}
    if path.endswith("/salary-gap"):
        res = salary_gap(body.get("desired"), body.get("advertised"),
                         body.get("actual"), body.get("location", ""))
    elif path.endswith("/clause-walk"):
        res = clause_walk(body.get("text", ""))
    elif path.endswith("/scripts"):
        res = scripts(body.get("scenario", ""), body.get("context"))
    else:
        return {"statusCode": 404, "body": json.dumps({"error": "not found"})}
    return {"statusCode": 200, "headers": {"Content-Type": "application/json"},
            "body": json.dumps(res)}
