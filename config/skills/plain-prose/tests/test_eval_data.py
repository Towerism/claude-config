"""The skill's eval case files have the shape the eval runners expect."""

import json
import re
from pathlib import Path

EVALS = Path(__file__).resolve().parents[1] / "evals"


def test_the_trigger_file_has_twenty_queries_ten_positive_two_delegated():
    cases = json.loads((EVALS / "trigger-evals.json").read_text())
    assert len(cases) == 20
    assert all(set(c) <= {"query", "should_trigger", "delegated"} for c in cases)
    assert all(isinstance(c["query"], str) and c["query"].strip() for c in cases)
    positive = [c for c in cases if c["should_trigger"]]
    assert len(positive) == 10
    delegated = [c for c in cases if c.get("delegated")]
    assert len(delegated) == 2
    assert all(c["should_trigger"] for c in delegated)
    assert len({c["query"] for c in cases}) == 20


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
