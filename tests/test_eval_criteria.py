"""Pass bars, run counts and grader models live in eval/criteria.json, an input (#77).

So a looser bar or another grader changes an input and needs the full tier,
while an edit to the harness code alone needs only the smoke tier.
"""

import json

import pytest

from eval import run_eval as re_
from tests import test_run_eval as run_tests
from tests.checker_helpers import REPO_ROOT
from tests.criteria_probe import (
    assert_reads_are_selected,
    criteria_pointers,
    probe_reads,
    uncovered,
)
from tests.eval_fakes import FakeClaude, make_tree, scenario
from tools.cfg import eval_records as er

env = run_tests.env
fresh_tempdir = run_tests.fresh_tempdir
TODAY = {
    "graderModel": "sonnet",
    "rubric": {"pass": 80, "tries": 2},
    "scenario": {"tries": 2},
    "trigger": {"recall": 0.7, "firstRuns": 2, "needed": 2, "maxTurns": 4},
    "coldReader": {"tries": 2},
    "docTrigger": {"firstRuns": 2, "needed": 2, "ordinaryTurns": 3, "delegatedTurns": 4},
    "docReader": {"rightShare": 0.9},
    "hooks": {"tries": 2, "timingRuns": 20, "smokeTimingRuns": 5, "p95LimitMs": 500},
}


def test_the_criteria_file_holds_the_bars_the_harness_used_before():
    assert json.loads((REPO_ROOT / re_.CRITERIA).read_text()) == TODAY


EXPECTED_POINTERS = {
    "scenario": ["/graderModel", "/scenario/tries"],
    "rubric": ["/rubric"],
    "hook-overlay": ["/hooks"],
    "hook-merge": ["/hooks"],
    "hook-session-start": ["/hooks"],
    "plain-prose-trigger": ["/trigger"],
    "plain-prose-behaviour": ["/graderModel", "/coldReader"],
    "writing-docs-trigger": ["/docTrigger", "/trigger/recall"],
    "writing-docs-behaviour": ["/graderModel", "/docReader"],
}


def test_every_suite_whose_runner_reads_the_criteria_lists_only_the_keys_it_reads():
    # Script suites run a skill's own eval script, which has its own bars.
    suites = er.parse_gates((REPO_ROOT / er.GATES_PATH).read_text())
    readers = {n for n, s in suites.items() if s.runner != "script"}
    assert readers == set(EXPECTED_POINTERS)
    assert all(re_.CRITERIA not in s.paths for s in suites.values())
    assert {n: criteria_pointers(n) for n in readers} == EXPECTED_POINTERS


def test_a_runner_that_reads_a_key_the_selectors_leave_out_is_named():
    assert uncovered(["/hooks"], {"/hooks/tries", "/rubric/pass"}) == ["/rubric/pass"]
    assert uncovered(["/trigger/recall"], {"/trigger/recall"}) == []
    assert uncovered(["/trigger"], {"/trigger/recall", "/triggers/x"}) == ["/triggers/x"]


def test_the_probe_fails_a_run_whose_suite_does_not_select_what_it_reads():
    with pytest.raises(AssertionError, match=r"hook-merge reads \['/rubric/pass'\]"):
        assert_reads_are_selected("hook-merge", {"/hooks/tries", "/rubric/pass"})
    with pytest.raises(AssertionError, match="proved nothing"):
        assert_reads_are_selected("rubric", set())
    assert_reads_are_selected("rubric", {"/rubric/pass", "/rubric/tries"})


def context(env, runner, **criteria):
    head = make_tree(env["tmp"] / "head", scenarios=[scenario("S-1")])
    if criteria:
        (head / re_.CRITERIA).write_text(json.dumps({**TODAY, **criteria}))
    base = make_tree(env["tmp"] / "base", md="# Old\n", scenarios=[scenario("S-1")])
    return re_.Context(live=env["live"], root=env["root"], runner=runner, model=None, workers=1,
                       out=env["tmp"] / "out", head_tree=head, base_tree=base)  # fmt: skip


def test_a_tree_without_the_file_uses_the_harness_copy(env):
    ctx = context(env, FakeClaude())
    assert not (ctx.head_tree / re_.CRITERIA).exists()
    assert re_.eval_criteria(ctx) == TODAY


def test_the_rubric_bar_comes_from_the_tree_under_test(env):
    ctx = context(env, FakeClaude(rubric_scores=(85, 85)), rubric={"pass": 90, "tries": 2})
    assert re_.run_rubric(ctx)["pass"] is False


def test_the_rubric_keeps_the_default_model(env):
    fake = FakeClaude(rubric_scores=(85,))
    re_.run_rubric(context(env, fake))
    assert all("--model" not in c["cmd"] for c in fake.calls)


def test_the_scenario_grader_uses_the_criteria_model_and_the_arms_do_not(env):
    fake = FakeClaude()
    re_.run_scenario(context(env, fake, graderModel="sonnet"))
    graded = [c for c in fake.calls if c["stdin"].startswith("You are grading")]
    answered = [c for c in fake.calls if c["stdin"].startswith(re_.PREAMBLE)]
    assert graded and all(c["cmd"][c["cmd"].index("--model") + 1] == "sonnet" for c in graded)
    assert answered and all("--model" not in c["cmd"] for c in answered)


def test_the_rubric_run_reads_only_keys_its_selectors_cover(env, monkeypatch):
    reads = probe_reads(monkeypatch)
    re_.run_rubric(context(env, FakeClaude(rubric_scores=(85,))))
    assert_reads_are_selected("rubric", reads)


def test_the_scenario_run_reads_only_keys_its_selectors_cover(env, monkeypatch):
    reads = probe_reads(monkeypatch)
    re_.run_scenario(context(env, FakeClaude()))
    assert_reads_are_selected("scenario", reads)
