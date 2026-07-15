"""Offline unit tests for the sponsorship engine (no network / no AWS)."""
import sponsorship as sp


# --- JD language scan --------------------------------------------------------

def test_scan_detects_negative_kill_phrase():
    jd = "You must be authorized to work in the US now or in the future without sponsorship."
    assert sp.scan_jd(jd)["neg"]


def test_scan_detects_plain_no_sponsorship():
    assert sp.scan_jd("We do not offer visa sponsorship for this role.")["neg"]
    assert sp.scan_jd("Visa sponsorship is not available.")["neg"]


def test_scan_detects_citizenship_and_clearance():
    assert sp.scan_jd("Applicants must be a US citizen.")["citizen"]
    assert sp.scan_jd("An active security clearance is required.")["citizen"]


def test_scan_detects_positive_signal():
    assert sp.scan_jd("Visa sponsorship is available for the right candidate.")["pos"]
    assert sp.scan_jd("We welcome STEM-OPT candidates and will sponsor H-1B.")["pos"]


def test_scan_clean_jd_has_no_hits():
    s = sp.scan_jd("Build cloud infra with Terraform and AWS. Great team, remote-friendly.")
    assert not s["neg"] and not s["citizen"] and not s["pos"]


# --- verdict resolution ------------------------------------------------------

def test_citizenship_beats_everything():
    # even a strong sponsor + H-1B history loses to a US-citizen JD requirement
    h1b = {"ok": True, "count": 500, "techCount": 400}
    v = sp.resolve("Amazon", "Must be a US citizen with clearance.", h1b)
    assert v["level"] == "unlikely" and not v["sponsors"]


def test_known_no_sponsor_new_grad():
    v = sp.resolve("Capital One", "Great new-grad role!", {"ok": True, "count": 300, "techCount": 200})
    assert v["level"] == "unlikely" and not v["sponsors"]


def test_jd_negative_phrase_overrides_history():
    v = sp.resolve("Some Startup", "We are unable to sponsor visas.", {"ok": True, "count": 50, "techCount": 40})
    assert v["level"] == "unlikely"


def test_cap_exempt_named_and_hinted():
    for name in ["MD Anderson Cancer Center", "University of Houston", "Baylor College of Medicine"]:
        v = sp.resolve(name, "", {"ok": True, "count": 0, "techCount": 0})
        assert v["level"] == "capexempt" and v["capExempt"] and v["sponsors"]


def test_strong_sponsor_is_likely():
    v = sp.resolve("Snowflake Inc", "Cloud Support Engineer", {"ok": True, "count": 120, "techCount": 90, "recentYear": 2025})
    assert v["level"] == "likely" and v["sponsors"]


def test_offshore_is_caution():
    v = sp.resolve("Cognizant Technology Solutions", "", {"ok": True, "count": 2000, "techCount": 1500})
    assert v["level"] == "caution"


def test_history_only_scales_by_count():
    assert sp.resolve("Unknown Co", "", {"ok": True, "count": 60, "techCount": 50})["level"] == "likely"
    assert sp.resolve("Unknown Co", "", {"ok": True, "count": 12, "techCount": 8})["level"] == "possible"
    assert sp.resolve("Unknown Co", "", {"ok": True, "count": 2, "techCount": 1})["level"] == "rare"
    assert sp.resolve("Unknown Co", "", {"ok": True, "count": 0, "techCount": 0})["level"] == "none"


def test_tech_zero_downgrades_likely_to_possible():
    # sponsors a lot, but none in tech (the SLB/Exxon petroleum-engineer trap)
    v = sp.resolve("Bigcorp Energy", "", {"ok": True, "count": 100, "techCount": 0})
    assert v["level"] == "possible"
    assert any("tech/engineering" in r for r in v["reasons"])


def test_db_unreachable_is_graceful():
    v = sp.resolve("Mystery Corp", "", {"ok": False, "error": "timeout", "count": 0})
    assert v["level"] == "unknown"
    assert any("Couldn't reach" in r for r in v["reasons"])


# --- h1bdata HTML parsing ----------------------------------------------------

_HTML = """
<table id="myTable"><thead><tr><th>EMPLOYER</th><th>JOB TITLE</th><th>BASE SALARY</th>
<th>LOCATION</th><th>SUBMIT DATE</th><th>START DATE</th></tr></thead><tbody>
<tr><td>DATADOG INC</td><td>SOFTWARE ENGINEER</td><td>160,000</td><td>NEW YORK, NY</td><td>04/23/2025</td><td>10/01/2025</td></tr>
<tr><td>DATADOG INC</td><td>SITE RELIABILITY ENGINEER</td><td>180,000</td><td>NEW YORK, NY</td><td>05/13/2025</td><td>10/01/2025</td></tr>
<tr><td>DATADOG INC</td><td>PRODUCT MANAGER</td><td>150,000</td><td>NEW YORK, NY</td><td>05/14/2024</td><td>09/01/2024</td></tr>
</tbody></table>
"""


def test_parse_h1b_counts_and_medians():
    r = sp.parse_h1b(_HTML)
    assert r["ok"] and r["count"] == 3
    assert r["techCount"] == 2                      # PM is not tech
    assert r["recentYear"] == 2025
    assert r["medianSalary"] == 160_000             # median of 150/160/180k
    assert r["medianTechSalary"] == 170_000         # median of 160/180k
    assert r["employer"] == "Datadog Inc"
    assert r["yearSpan"] == "2024–2025"


def test_parse_h1b_empty():
    r = sp.parse_h1b("<html><body>No records found</body></html>")
    assert r["ok"] and r["count"] == 0 and r["topTitles"] == []


def test_verify_links_shape():
    links = sp.verify_links("Datadog Inc")
    assert links["h1bdata"].startswith("https://h1bdata.info/")
    assert "myvisajobs" in links["myvisajobs"]
