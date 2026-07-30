"""Unit tests for the on-demand deep-eval + research module. Bedrock stubbed."""
import lambda_function as L


def test_deterministic_eval_gaps_and_level():
    r = L.deterministic_eval("Senior role. GCP and Spark required. 8+ years.", "Acme", "Senior SRE")
    assert r["dimensions"]["levelStrategy"]["level"] == "reach"
    assert "GCP" in r["dimensions"]["gaps"] and "Spark" in r["dimensions"]["gaps"]
    assert r["source"] == "deterministic"


def test_deterministic_eval_on_target_entry():
    r = L.deterministic_eval("Associate cloud role, 0-2 years.", "Acme", "Associate Cloud Engineer")
    assert r["dimensions"]["levelStrategy"]["level"] == "on-target"


def test_deterministic_eval_finds_salary():
    r = L.deterministic_eval("Comp $140,000 - $170,000.", "Acme", "SRE")
    assert "$140" in r["dimensions"]["comp"]


def test_deterministic_eval_always_reminds_sponsorship():
    r = L.deterministic_eval("Cloud role.", "Acme", "Cloud Engineer")
    assert "sponsorship" in r["sponsorshipReminder"].lower()


def test_build_eval_prompt_is_grounded():
    p = L.build_eval_prompt("k8s", "Baseten", "Cloud Platform Engineer")
    assert "never invent" in p["system"].lower()
    assert "Baseten" in p["user"] and "CANDIDATE" in p["user"]


def test_first_json_extracts_object():
    assert L._first_json('noise {"a": 1} tail') == {"a": 1}
    assert L._first_json("no json here") is None


def test_evaluate_falls_back_without_bedrock():
    L._invoke_json = lambda *a, **k: None
    r = L.evaluate("GCP role", "Acme", "Cloud Engineer")
    assert r["source"] == "deterministic" and "ai" not in r


def test_evaluate_merges_ai_when_available():
    L._invoke_json = lambda *a, **k: {"fitSummary": "good", "score": 4}
    r = L.evaluate("k8s role", "Acme", "SRE")
    assert r["source"] == "bedrock+deterministic" and r["ai"]["score"] == 4


def test_research_checklist_has_six_axes():
    L._invoke_json = lambda *a, **k: None
    r = L.research("Nuro", "SRE")
    assert len(r["axes"]) == 6 and r["source"] == "deterministic-checklist"


def test_research_uses_ai_when_available():
    L._invoke_json = lambda *a, **k: {"axes": [], "candidateAngle": "x"}
    r = L.research("Reddit", "SWE")
    assert r["source"] == "bedrock" and "verifyNote" in r
