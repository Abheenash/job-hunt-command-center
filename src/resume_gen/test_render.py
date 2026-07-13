"""Smoke tests for the deterministic LaTeX renderer — no AWS, no network.

Guards: the render is always a well-formed, self-contained document; AI plain text
is escaped (can't break LaTeX or inject); **bold** is honored; and missing/partial AI
output falls back to the corpus instead of crashing.
"""
import profile as P
import templates as T


def _sel():
    return {
        "summary": "Cloud engineer with a stray & percent % and **bold** emphasis.",
        "skills": [
            {"category": "Cloud (AWS)", "items": "Lambda, API Gateway, DynamoDB, Step Functions"},
            {"category": "DevSecOps & Security", "items": "IAM least privilege, KMS, Checkov"},
        ],
        "experienceBullets": ["Ran on-call and cut alert noise by tuning **composite** alarms."],
        "projects": [
            {"id": "job-hunt-command-center", "name": "Job Hunt Command Center", "tech": "Bedrock, Lambda, SQS",
             "bullets": ["Built an **event-driven** pipeline with SQS + Step Functions.", "Integrated Amazon Bedrock."]},
            {"id": "serverless-file-share", "name": "Serverless File Share", "tech": "WebCrypto, Lambda, S3",
             "bullets": ["Zero-knowledge AES-256-GCM encryption in the browser."]},
            {"id": "aws-eks-platform", "name": "AWS EKS Platform", "tech": "EKS, Terraform",
             "bullets": ["Provisioned EKS with IRSA and an HPA."]},
            {"id": "secure-container-pipeline", "name": "Secure Container Pipeline", "tech": "ECS, WAF",
             "bullets": ["Three fail-the-build security gates."]},
        ],
    }


def test_render_is_wellformed_document():
    tex = T.render_resume(_sel())
    assert tex.count("\\documentclass") == 1
    assert tex.strip().endswith("\\end{document}")
    assert "\\begin{document}" in tex
    assert "Job Hunt Command Center" in tex


def test_ai_prose_escaped_and_bold_converted():
    tex = T.render_resume(_sel())
    assert "percent \\%" in tex          # escaped
    assert "\\textbf{bold}" in tex        # **bold** -> \textbf{}
    assert "&" not in tex.split("\\begin{document}")[1].replace("\\&", "")  # raw & got escaped in body


def test_project_links_come_from_corpus_not_model():
    sel = _sel()
    sel["projects"][0]["name"] = "Totally Renamed"  # model renames; link must still be the real repo
    tex = T.render_resume(sel)
    assert "github.com/Abheenash/job-hunt-command-center" in tex
    assert "Totally Renamed" in tex


def test_empty_sections_fall_back_to_corpus():
    tex = T.render_resume({"summary": "", "skills": [], "experienceBullets": [], "projects": []})
    assert "\\section{Summary}" in tex
    assert "HCLTech" in tex               # experience fallback
    assert P.SKILLS and "Cloud (AWS)" in tex  # skills fallback


def test_cover_letter_escapes_and_wraps():
    cl = T.render_cover_letter("Nimbus & Co", "Cloud Engineer", ["Para one with 50% and **drive**."])
    assert "\\documentclass" in cl and cl.strip().endswith("\\end{document}")
    assert "Nimbus \\& Co" in cl and "50\\%" in cl and "\\textbf{drive}" in cl
