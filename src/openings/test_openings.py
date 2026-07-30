"""Unit tests for the openings radar's pure logic — no AWS calls, no network.
Covers the new deterministic layers: salary extraction, posting-legitimacy /
ghost-job detection, and the structured per-role evaluation breakdown."""
import os

os.environ.setdefault("OPENINGS_TABLE", "t")

import lambda_function as L  # noqa: E402


# --- salary extraction -------------------------------------------------------

def test_salary_range_commas():
    assert L._extract_salary("Comp: $150,000 - $180,000 plus equity") == (150000, 180000)


def test_salary_range_k():
    assert L._extract_salary("Base $120k–$160k DOE") == (120000, 160000)


def test_salary_hourly_annualizes():
    lo, hi = L._extract_salary("Contract at $75/hr")
    assert lo == hi == 75 * 2080


def test_salary_absent():
    assert L._extract_salary("Competitive salary and benefits") is None


# --- legitimacy / ghost-job --------------------------------------------------

def test_legit_ok_when_clean():
    flag, _, sigs = L._legitimacy({"jd": "Build reliable AWS platforms with Terraform."}, 0, 1000)
    assert flag == "ok" and sigs == []


def test_legit_flags_evergreen():
    flag, _, sigs = L._legitimacy(
        {"jd": "Join our talent community — we are always hiring engineers."}, 0, 1000)
    assert flag == "ghost" and any("evergreen" in s for s in sigs)


def test_legit_flags_scam_fee_and_offplatform():
    flag, _, _ = L._legitimacy(
        {"jd": "Start immediately, interview over telegram, pay a small processing fee."}, 0, 1000)
    assert flag == "scam"


def test_legit_flags_stale_posting():
    now = 100 * 86400
    flag, _, sigs = L._legitimacy({"jd": "SRE role", "postedAt": 5 * 86400}, 0, now)
    assert flag == "ghost" and any("likely filled" in s for s in sigs)


def test_legit_ignores_unknown_posted_date():
    flag, _, _ = L._legitimacy({"jd": "SRE role", "postedAt": 0}, 0, 100 * 86400)
    assert flag == "ok"   # postedAt=0 means unknown, not stale


def test_legit_staffing_signal():
    flag, _, sigs = L._legitimacy({"jd": "Cloud engineer", "staffing": True}, 0, 1000)
    assert flag == "ghost" and any("bodyshop" in s for s in sigs)


# --- structured eval breakdown ----------------------------------------------

def test_eval_matches_and_gaps():
    jd = ("Cloud Engineer. Terraform, AWS Lambda, Kubernetes on EKS. "
          "Experience with GCP and Azure and Spark a plus.")
    ev = L._eval_breakdown({"title": "Cloud Engineer", "jd": jd})
    assert "terraform" in ev["matchedSkills"] and "kubernetes" in ev["matchedSkills"]
    assert "gcp" in ev["gaps"] and "azure" in ev["gaps"] and "spark" in ev["gaps"]


def test_eval_level_on_target_for_entry():
    ev = L._eval_breakdown({"title": "Associate Cloud Engineer", "jd": "0-2 years experience."})
    assert ev["levelFit"] == "on-target"


def test_eval_level_reach_for_senior():
    ev = L._eval_breakdown({"title": "Senior SRE", "jd": "8+ years required."})
    assert ev["levelFit"] == "reach" and ev["reqYears"] == 8


def test_eval_level_stretch_for_mid():
    ev = L._eval_breakdown({"title": "Cloud Engineer", "jd": "3 years of experience."})
    assert ev["levelFit"] == "stretch"


def test_eval_angle_picks_real_project():
    ev = L._eval_breakdown({"title": "Platform Engineer", "jd": "Kubernetes, EKS, Helm ingress."})
    assert ev["angleProject"] == "aws-eks-platform" and ev["anglePitch"]


def test_eval_salary_surfaced():
    ev = L._eval_breakdown({"title": "SRE", "jd": "Pay range $140,000 - $170,000."})
    assert ev["salaryRange"] == {"low": 140000, "high": 170000}
