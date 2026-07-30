"""Unit tests for the digest analytics — pure logic, no SES/DynamoDB."""
import os

os.environ.setdefault("APPS_TABLE", "t")
os.environ.setdefault("SES_SENDER", "me@x.com")
os.environ.setdefault("OWNER_EMAIL", "me@x.com")

import lambda_function as L  # noqa: E402


def test_source_key_normalizes():
    assert L._source_key({"source": "Job sweep (Greenhouse)"}) == "Greenhouse"
    assert L._source_key({"source": "LinkedIn"}) == "LinkedIn"
    assert L._source_key({}) == "direct"


def test_analytics_advance_and_reject_rates():
    apps = [
        {"source": "Job sweep (Greenhouse)", "status": "interview"},
        {"source": "Job sweep (Greenhouse)", "status": "applied"},
        {"source": "Job sweep (Greenhouse)", "status": "rejected"},
        {"source": "LinkedIn", "status": "screen"},
        {"source": "LinkedIn", "status": "offer"},
    ]
    ana = L.compute_analytics(apps)
    gh = next(d for d in ana["bySource"] if d["source"] == "Greenhouse")
    li = next(d for d in ana["bySource"] if d["source"] == "LinkedIn")
    assert gh["total"] == 3 and gh["advanced"] == 1 and gh["advanceRate"] == 33 and gh["rejectRate"] == 33
    assert li["total"] == 2 and li["advanceRate"] == 100
    assert ana["funnel"]["interview"] == 1 and ana["funnel"]["offer"] == 1


def test_analytics_flags_low_yield_channel():
    apps = [{"source": "Adzuna", "status": "applied"} for _ in range(6)]
    ana = L.compute_analytics(apps)
    assert any("0 advanced" in p and "Adzuna" in p for p in ana["patterns"])


def test_analytics_names_best_channel():
    apps = ([{"source": "Referral", "status": "interview"} for _ in range(3)]
            + [{"source": "Board", "status": "applied"} for _ in range(3)])
    ana = L.compute_analytics(apps)
    assert any("Best channel: Referral" in p for p in ana["patterns"])


def test_analytics_html_is_symbol_free():
    ana = L.compute_analytics([{"source": "LinkedIn", "status": "offer"}])
    html = L._analytics_html(ana)
    # no pictographic emoji in the output
    assert all(ord(ch) < 0x2190 or ch in "→↔·—–" for ch in html)
