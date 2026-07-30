"""Unit tests for the outreach drafter — pure logic, no AWS, no send."""
import lambda_function as L

APP = {"company": "Baylor", "title": "IT Infrastructure Associate"}


def test_linkedin_note_under_limit():
    n = L.linkedin_note(APP, "recruiter", "Zac")
    assert len(n) <= 300
    assert n.startswith("Hi Zac")
    assert "IT Infrastructure Associate" in n and "Baylor" in n


def test_linkedin_note_never_mentions_hcl():
    app = {"company": "X", "title": "Cloud Engineer at HCLTech pipeline"}  # HCL in the title
    n = L.linkedin_note(app, "peer", "Sam")
    assert not __import__("re").search(r"HCL", n, __import__("re").I)


def test_linkedin_note_uh_warmth():
    n = L.linkedin_note(APP, "hiring_manager", "Zac", shared_uh=True)
    assert "University of Houston alum" in n and len(n) <= 300


def test_linkedin_note_trims_long_input_to_limit():
    app = {"company": "A Very Long Company Name Incorporated LLC", "title": "Senior Staff Principal Cloud Infrastructure Reliability Platform Engineer II"}
    n = L.linkedin_note(app, "recruiter", "Alexander")
    assert len(n) <= 300 and n.endswith("- Abheenash")


def test_application_email_structure():
    e = L.application_email(APP, "referral", "Zac")
    assert "referral" in e["subject"].lower()
    assert "Zac" in e["body"] and "Baylor" in e["body"]
    assert e["attachmentChecklist"] and e["note"].startswith("Draft only")


def test_drafts_for_bundles_both():
    d = L.drafts_for(APP, "Zac", "hiring_manager", shared_uh=True)
    assert d["linkedinNote"] and d["email"]["subject"]
    assert d["contactType"] == "hiring_manager"
