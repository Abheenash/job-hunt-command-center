"""Truncated-model-reply repair (no AWS: the module's clients are built at import but never called)."""
import json
import os

os.environ.setdefault("AWS_DEFAULT_REGION", "us-east-1")
os.environ.setdefault("APPS_TABLE", "t")
os.environ.setdefault("EVENTS_TABLE", "e")
os.environ.setdefault("DOCS_BUCKET", "b")
import lambda_function as api  # noqa: E402

FULL = ('{"scoreBreakdown":[{"dimension":"Required skills","score":80,"note":"good"}],'
        '"matched":["Terraform","AWS"],"atsCovered":["aws","terraform"],"atsMissing":["gcp"],'
        '"summary":"Strong fit; GCP is the gap"}')


def test_complete_reply_is_returned_intact():
    assert json.loads(api._first_json("Sure! " + FULL + " Hope that helps.")) == json.loads(FULL)


def test_reply_with_no_json_gives_empty_object():
    assert api._first_json("I cannot help with that.") == "{}"


def test_cut_inside_a_string_drops_the_partial_element():
    cut = FULL[:FULL.index('"atsMissing"') + 20]  # mid-way through the atsMissing list/string
    got = json.loads(api._first_json(cut))
    assert got["scoreBreakdown"][0]["score"] == 80
    assert got["matched"] == ["Terraform", "AWS"]
    assert "summary" not in got  # never reached; not invented


def test_cut_inside_a_number_keeps_completed_values():
    cut = '{"a":[1,2,3],"b":{"x":12'
    got = json.loads(api._first_json(cut))
    assert got["a"] == [1, 2, 3]


def test_cut_after_a_comma_and_after_a_key():
    assert json.loads(api._first_json('{"a":1,"b":2,')) == {"a": 1, "b": 2}
    assert json.loads(api._first_json('{"a":1,"b":')) == {"a": 1}


def test_escaped_quotes_do_not_confuse_the_scanner():
    s = '{"note":"he said \\"hi\\" and left","list":["a","b'
    got = json.loads(api._first_json(s))
    assert got["note"] == 'he said "hi" and left'
    assert got["list"] == ["a"]


def test_nested_truncation_closes_every_level_without_inventing_values():
    s = '{"scoreBreakdown":[{"dimension":"A","score":50,"note":"n"},{"dimension":"B","sc'
    got = json.loads(api._first_json(s))
    assert got["scoreBreakdown"][0] == {"dimension": "A", "score": 50, "note": "n"}
    # the cut-off element keeps only the fields that were complete — never a made-up score
    for d in got["scoreBreakdown"][1:]:
        assert set(d) <= {"dimension"}


def test_hopeless_fragment_degrades_to_empty_object():
    assert api._first_json('{"') == "{}"
    assert api._first_json("{") == "{}"
