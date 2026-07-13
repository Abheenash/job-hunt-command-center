"""Smoke tests for the deterministic LaTeX renderer — no AWS, no network.

Guards the two things that matter: the render always produces a well-formed,
self-contained document, and AI-authored prose is escaped so it can't break LaTeX
or inject commands.
"""
import profile as P
import templates as T


def _sample_selection():
    return {
        "summary": "Plain-text summary with a stray & percent % and underscore _ to escape.",
        "skillsOrder": ["Cloud (AWS)", "GenAI / LLM", "DevSecOps \\& Security"],
        "projects": [
            {"id": "job-hunt-command-center", "bulletIdx": [0, 1, 2]},
            {"id": "serverless-file-share", "bulletIdx": [0, 1]},
            {"id": "aws-eks-platform", "bulletIdx": [0, 2]},
            {"id": "secure-container-pipeline", "bulletIdx": [0, 1, 2]},
        ],
    }


def test_render_is_wellformed_document():
    tex = T.render_resume(_sample_selection())
    assert tex.count("\\documentclass") == 1
    assert tex.strip().endswith("\\end{document}")
    assert "\\begin{document}" in tex
    # all four selected projects appear
    for pid in ["job-hunt-command-center", "serverless-file-share", "aws-eks-platform", "secure-container-pipeline"]:
        assert P.project_by_id(pid)["name"] in tex


def test_ai_prose_is_escaped():
    tex = T.render_resume(_sample_selection())
    # the raw special chars from the summary must have been escaped
    assert "percent \\%" in tex
    assert "underscore \\_" in tex


def test_missing_project_id_is_skipped_not_fatal():
    sel = _sample_selection()
    sel["projects"].append({"id": "does-not-exist", "bulletIdx": [0]})
    tex = T.render_resume(sel)  # must not raise
    assert "\\end{document}" in tex


def test_empty_summary_falls_back_to_base():
    sel = _sample_selection()
    sel["summary"] = ""
    tex = T.render_resume(sel)
    assert "\\section{Summary}" in tex


def test_cover_letter_escapes_and_wraps():
    cl = T.render_cover_letter("Nimbus & Co", "Cloud Engineer", ["Para one with 50% enthusiasm.", "Para two."])
    assert "\\documentclass" in cl and cl.strip().endswith("\\end{document}")
    assert "Nimbus \\& Co" in cl
    assert "50\\%" in cl
