"""Unit tests for the offer-stage companion — pure logic, no AWS, no AI."""
import lambda_function as L


def test_salary_gap_below_band_flags_and_counsels():
    r = L.salary_gap(desired=150000, advertised=[140000, 170000], actual=132000, location="Houston, TX")
    assert r["verdict"] == "below-band-top"
    assert any("BELOW the posted band" in f for f in r["flags"])
    assert "headroom" in r["recommendation"]


def test_salary_gap_at_target():
    r = L.salary_gap(desired=140000, advertised=[130000, 160000], actual=150000, location="Remote")
    assert r["verdict"] == "at-or-above-target"


def test_salary_gap_col_adjustment_sf():
    r = L.salary_gap(desired=None, advertised=None, actual=170000, location="San Francisco, CA")
    assert r["colIndex"] == 179
    # SF 170k adjusted to Houston base (96) buys much less
    assert r["adjustedToBase"] < 170000
    assert any("cost-of-living" in f for f in r["flags"])


def test_salary_gap_no_offer_yet():
    r = L.salary_gap(desired=140000, advertised=[130000, 160000], actual=None, location="Austin")
    assert r["verdict"] == "no-offer-yet" and "midpoint" in r["recommendation"]


def test_clause_walk_finds_visa_and_noncompete():
    text = "This is an at-will offer. You agree to a non-compete. Sponsorship for H-1B will be provided."
    r = L.clause_walk(text)
    clauses = {c["clause"] for c in r["clausesFound"]}
    assert "non-compete" in clauses
    assert any("visa" in c for c in clauses)
    assert r["disclaimer"]


def test_clause_walk_flags_missing_visa():
    r = L.clause_walk("Standard at-will employment. Salary $120,000.")
    assert any("NOT in the letter" in c["clause"] for c in r["clausesFound"])


def test_scripts_fills_template():
    r = L.scripts("comp", {"role": "Cloud Engineer", "target": "150,000"})
    assert "Cloud Engineer" in r["script"] and "150,000" in r["script"]


def test_scripts_unknown_scenario():
    r = L.scripts("bogus")
    assert "error" in r and "comp" in r["available"]
