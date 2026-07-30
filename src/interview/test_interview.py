"""Unit tests for the interview-prep companion — pure logic, grounded in real corpus."""
import lambda_function as L


def test_stories_match_pressure_question():
    r = L.match_stories("Tell me about a time you worked under pressure.")
    ids = [m["id"] for m in r["matched"]]
    assert "oncall-incident" in ids
    assert "pressure" in r["competencies"]


def test_stories_match_failure_question_prefers_honesty():
    r = L.match_stories("Tell me about a failure or mistake.")
    ids = [m["id"] for m in r["matched"]]
    assert ids and any(i in ("eks-drill", "restore-test") for i in ids)


def test_stories_flag_conflict_gap():
    r = L.match_stories("Tell me about a conflict with a coworker.")
    assert any("conflict" in g for g in r["prepGap"])


def test_stories_never_empty():
    r = L.match_stories("some unrelated question about your hobbies")
    assert r["matched"]  # always returns at least one


def test_stories_are_from_real_corpus():
    # every seed story must be traceable to a real project/experience (no invented ids)
    real = {"oncall-incident", "eks-drill", "secpipe-secret", "cicd-rebuild", "restore-test"}
    assert {s["id"] for s in L.SEED_STORIES} == real
    for s in L.SEED_STORIES:
        assert all(s[k] for k in ("situation", "task", "action", "result", "reflection"))


def test_redflags_detects_fast_paced_and_family():
    r = L.red_flags("We're a fast-paced startup and we're like a family. Competitive salary.")
    assert r["count"] >= 3
    assert any("fast-paced" in f for f in r["flags"])


def test_redflags_clean_text():
    r = L.red_flags("Build reliable AWS platforms. Salary band $130k-$160k. Hybrid, 3 days.")
    assert r["count"] == 0 and "No obvious" in r["note"]


def test_prep_plan_sized_to_days():
    r = L.prep_plan("Cloud Engineer", 2)
    assert r["days"] == 2 and len(r["plan"]) == 2
    assert r["plan"][0]["day"] == "T-minus 2"


def test_debrief_has_fit_rating_prompt():
    r = L.debrief_template("SRE")
    assert any("Rate fit" in c for c in r["capture"]) and r["followUp"]
