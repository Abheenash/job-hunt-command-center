"""Unit tests for the scanner's pure logic — no IMAP, no AWS calls."""
import os

os.environ.setdefault("EVENTS_TABLE", "t")
os.environ.setdefault("SECRET_ID", "s")
os.environ.setdefault("QUEUE_URL", "https://sqs.local/q")

import lambda_function as L  # noqa: E402


def test_maybe_job_matches_keywords():
    assert L._maybe_job({"subject": "Interview invitation for Cloud Engineer", "snippet": ""})
    assert L._maybe_job({"subject": "", "snippet": "Thanks for applying to our role"})


def test_maybe_job_matches_ats_domain():
    assert L._maybe_job({"from": "no-reply@greenhouse.io", "subject": "hi", "snippet": ""})


def test_maybe_job_rejects_non_job():
    assert not L._maybe_job({"from": "ship@amazon.com", "subject": "Your order shipped", "snippet": "tracking"})


def test_eid_is_stable_and_id_based():
    a = {"messageId": "<abc@x>", "from": "a@b.com", "subject": "hi"}
    assert L._eid(a) == L._eid(dict(a))
    b = {"messageId": "<def@x>", "from": "a@b.com", "subject": "hi"}
    assert L._eid(a) != L._eid(b)


def test_eid_falls_back_without_message_id():
    a = {"from": "a@b.com", "subject": "hi"}
    assert L._eid(a) == L._eid(dict(a))  # deterministic from from|subject
