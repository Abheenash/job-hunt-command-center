"""Unit tests for the email classifier. Run: pytest src/inbox_scan/"""
from classifier import classify, is_job_related, match_company


def cat(subject, body="", sender=""):
    return classify(subject, body, sender)[0]


def test_interview():
    assert cat("Interview invitation", "Can we schedule a call next week?") == "interview"
    assert cat("Next steps", "Please pick a time on my Calendly") == "interview"
    assert cat("Technical screen", "a technical assessment for the role") == "interview"


def test_offer():
    assert cat("Your offer", "We are pleased to offer you the position") == "offer"
    assert cat("Offer letter attached", "welcome aboard") == "offer"


def test_rejection():
    assert cat("Update", "Unfortunately we are not moving forward with your application") == "rejection"
    assert cat("Your application", "we decided to move forward with other candidates") == "rejection"
    assert cat("Thanks", "you were not a fit for this position; we wish you the best") == "rejection"


def test_confirmation():
    assert cat("Thanks", "Thank you for applying to Acme") == "confirmation"
    assert cat("Received", "We have received your application") == "confirmation"


def test_recruiter_reply():
    assert cat("Hi", "A recruiter came across your profile", "jane@talent.io") == "recruiter_reply"
    assert cat("Opportunity", "an exciting opportunity for you") == "recruiter_reply"


def test_other_is_not_job_related():
    assert cat("Your Amazon order shipped", "Track your package") == "other"
    assert not is_job_related("Lunch?", "wanna grab lunch")


def test_priority_interview_beats_recruiter():
    # both signals present -> interview wins (more specific, earlier rule)
    assert cat("Recruiter reaching out", "let's schedule an interview call") == "interview"


def test_confidence_scales():
    _, conf_one, _ = classify("interview", "")
    _, conf_many, _ = classify("interview", "schedule a call for the next round, a phone screen")
    assert conf_many > conf_one


def test_match_company_by_name_and_domain():
    companies = ["Acme Cloud", "Globex"]
    assert match_company("careers@acmecloud.com", "Re: your application", "", companies) == "Acme Cloud"
    assert match_company("hr@x.com", "Interview at Globex", "", companies) == "Globex"
    assert match_company("a@b.com", "unrelated", "", companies) is None
