"""Unit tests for the cover-letter generator — pure logic (prompt build + fallback).
Bedrock is stubbed so no network / model call happens."""
import os

os.environ.setdefault("MAX_LETTER_CHARS", "2600")

import lambda_function as L  # noqa: E402


def test_pick_angle_maps_kubernetes():
    assert L.pick_angle("We run Kubernetes on EKS with Helm.") == "aws-eks-platform"


def test_pick_angle_maps_security():
    assert L.pick_angle("Focus on DevSecOps, hardening, and secrets management.") == "secure-container-pipeline"


def test_keywords_to_mirror():
    kw = L.keywords_to_mirror("Terraform, AWS, CI/CD and on-call reliability.")
    assert "terraform" in kw and "aws" in kw and "on-call" in kw


def test_build_prompt_is_grounded_and_capped():
    p = L.build_prompt("Kubernetes on EKS.", "Baseten", "Cloud Platform Engineer")
    assert "ONLY the facts" in p["system"]
    assert "Baseten" in p["user"] and "Cloud Platform Engineer" in p["user"]
    assert p["anglePid"] == "aws-eks-platform"


def test_fallback_letter_is_real_and_addressed():
    letter = L.fallback_letter("Observability and SRE.", "Nuro", "SRE")
    assert "Dear Nuro Team" in letter
    assert "Abheenash Rajolu" in letter
    assert "observability" in letter.lower() or "sre" in letter.lower()


def test_generate_uses_fallback_when_bedrock_down(monkeypatch=None):
    # force the model chain to fail -> deterministic fallback
    L._invoke = lambda system, user: None
    out = L.generate("Kubernetes EKS role.", "Airbnb", "Cloud Networking")
    assert out["source"] == "deterministic-fallback"
    assert "Dear Airbnb Team" in out["letter"]
    assert out["anchorProject"] == "aws-eks-platform"


def test_generate_returns_bedrock_when_available():
    L._invoke = lambda system, user: "Dear team, here is a grounded letter. Best, Abheenash Rajolu"
    out = L.generate("SRE role", "Reddit", "SRE")
    assert out["source"] == "bedrock" and "grounded letter" in out["letter"]
