"""Unit tests for the enrich task's pure matching/parsing logic — no AWS calls."""
import os

os.environ.setdefault("APPS_TABLE", "apps")
os.environ.setdefault("EVENTS_TABLE", "events")

import lambda_function as L  # noqa: E402


APPS = [
    {"appId": "1", "company": "Acme Cloud", "contactEmail": "rec@acme.com"},
    {"appId": "2", "company": "Globex", "contactEmail": ""},
]


def test_match_by_name_exact_and_substring():
    assert L._match_by_name("Acme Cloud", APPS) == "1"
    assert L._match_by_name("acme", APPS) == "1"        # substring, case-insensitive
    assert L._match_by_name("Globex Corp", APPS) == "2"  # corpus name inside the query


def test_match_by_name_no_match_or_too_short():
    assert L._match_by_name("Initech", APPS) is None
    assert L._match_by_name("a", APPS) is None           # < 2 chars


def test_is_date():
    assert L._is_date("2026-07-13")
    assert not L._is_date("July 13")
    assert not L._is_date("")


def test_addr_extracts_email():
    assert L._addr("Jane Doe <jane@x.com>") == "jane@x.com"
    assert L._addr("bob@y.com") == "bob@y.com"


def test_status_rank_is_forward_only_ordering():
    assert L.STATUS_RANK["applied"] < L.STATUS_RANK["screen"] < L.STATUS_RANK["interview"] < L.STATUS_RANK["offer"]
