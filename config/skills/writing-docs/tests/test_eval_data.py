"""The skill's eval case files have the shape and mix the eval runners and the spec expect."""

import json
import re
from collections import Counter
from pathlib import Path

EVALS = Path(__file__).resolve().parents[1] / "evals"
SHOULD_KINDS = {"new-doc": 6, "doc-edit": 2, "prose": 4, "code": 3}
NEAR_KINDS = {"for-claude": 5, "chat-only": 3}  # minimums


def trigger_cases():
    return json.loads((EVALS / "trigger-evals.json").read_text())


def test_the_trigger_file_has_fifteen_positives_and_ten_near_misses_of_the_right_kinds():
    cases = trigger_cases()
    assert len(cases) == 25
    assert len({c["query"] for c in cases}) == 25
    for c in cases:
        assert set(c) <= {"query", "should_trigger", "delegated", "kind"}, c
        assert isinstance(c["query"], str) and c["query"].strip()
        assert c["kind"] in (SHOULD_KINDS if c["should_trigger"] else NEAR_KINDS), c
    kinds = Counter(c["kind"] for c in cases)
    assert {k: kinds[k] for k in SHOULD_KINDS} == SHOULD_KINDS
    assert all(kinds[k] >= n for k, n in NEAR_KINDS.items()), kinds
    assert sum(not c["should_trigger"] for c in cases) == 10
    delegated = [c for c in cases if c.get("delegated")]
    assert len(delegated) == 2 and all(c["should_trigger"] for c in delegated)


def test_some_queries_that_should_trigger_never_say_doc():
    plain = [c for c in trigger_cases() if c["should_trigger"] and "doc" not in c["query"].lower()]
    assert len(plain) >= 2


def test_the_new_document_queries_span_at_least_four_kinds_of_document():
    words = ("readme", "adr", "how-to", "release notes", "tutorial", "restore")
    queries = [c["query"].lower() for c in trigger_cases() if c["kind"] == "new-doc"]
    assert len({w for w in words for q in queries if w in q}) >= 4


def test_the_behaviour_file_has_three_write_and_three_rewrite_cases():
    cases = json.loads((EVALS / "behaviour-evals.json").read_text())
    kinds = [c["kind"] for c in cases]
    assert kinds.count("write") >= 3
    assert kinds.count("rewrite") >= 3
    assert len({c["id"] for c in cases}) == len(cases)
    for c in cases:
        required = {"id", "kind", "task", "facts", "questions", "mustKeep"}
        assert set(c) == required | ({"draft"} if c["kind"] == "rewrite" else set()), c["id"]
        assert 3 <= len(c["questions"]) <= 4, c["id"]
        assert c["facts"] and c["mustKeep"], c["id"]


def test_case_data_holds_no_home_paths_emails_or_tokens():
    text = (EVALS / "trigger-evals.json").read_text() + (EVALS / "behaviour-evals.json").read_text()
    home_root = "/" + "home/"  # built here so the checker's own path rule doesn't flag this test
    assert home_root not in text
    assert not re.search(r"[\w.+-]+@[\w-]+\.[\w.]+", text)
    assert not re.search(r"\b(?:ghp|gho|sk)_[A-Za-z0-9]{16,}", text)
