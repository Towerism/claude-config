"""The smoke tier in the scenario and rubric suites, and the limit on calls open at once (#77)."""

import dataclasses
import os
import subprocess
import sys
import tempfile
import threading
import time

import pytest

from eval import run_eval as re_
from tests.eval_fakes import FakeClaude, make_live_home, make_tree, scenario


@pytest.fixture(autouse=True)
def fresh_tempdir(monkeypatch):
    monkeypatch.setattr(tempfile, "tempdir", None)


@pytest.fixture
def env(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.delenv("CLAUDE_CONFIG_DIR", raising=False)
    root = tmp_path / "tmproot"
    root.mkdir()
    return {"live": make_live_home(tmp_path), "root": root, "tmp": tmp_path}


def context(env, runner, scenarios, **kw):
    tmp = env["tmp"]
    return re_.Context(
        live=env["live"],
        root=env["root"],
        runner=runner,
        model=None,
        workers=1,
        out=tmp / "out",
        head_tree=make_tree(tmp / "head", md="# New\n", scenarios=scenarios),
        base_tree=make_tree(tmp / "base", md="# Old\n", scenarios=scenarios),
        **kw,
    )


SCENARIOS = [scenario("S-1"), scenario("S-2"), scenario("S-3", tags=["new-rule-only"])]


# Picking the smoke cases


def test_a_full_run_keeps_every_case():
    ctx = dataclasses.replace(context_stub(), tier="full", smoke=(1,))
    assert re_.smoke_pick(ctx, ["a", "b", "c"]) == ["a", "b", "c"]


def test_a_smoke_run_picks_by_position_or_by_id():
    ctx = dataclasses.replace(context_stub(), tier="smoke", smoke=(2, 0))
    assert re_.smoke_pick(ctx, ["a", "b", "c"]) == ["c", "a"]
    ctx = dataclasses.replace(ctx, smoke=("S-2",))
    assert re_.smoke_pick(ctx, SCENARIOS, key=lambda s: s["id"]) == [SCENARIOS[1]]


@pytest.mark.parametrize("smoke", [(5,), ("S-9",)])
def test_a_smoke_case_that_is_not_there_is_refused(smoke):
    ctx = dataclasses.replace(context_stub(), tier="smoke", smoke=smoke)
    key = None if isinstance(smoke[0], int) else (lambda s: s["id"])
    with pytest.raises(re_.Refusal, match=str(smoke[0])):
        re_.smoke_pick(ctx, SCENARIOS, key=key)


def context_stub():
    return re_.Context(live=None, root=None, runner=None, model=None, workers=1, out=None,
                       head_tree=None, base_tree=None)  # fmt: skip


# Scenario and rubric in the smoke tier


def test_a_smoke_scenario_run_answers_only_the_smoke_cases_on_the_new_arm_once(env):
    fake = FakeClaude(verdicts={("new", "S-2"): [False, True]})
    ctx = context(env, fake, SCENARIOS, tier="smoke", smoke=("S-2", "S-3"))
    result = re_.run_scenario(ctx)
    graded = [c["stdin"].split("ANSWER:\nanswer ", 1)[1].split()[:2]
              for c in fake.calls if c["stdin"].startswith("You are grading")]  # fmt: skip
    assert sorted(graded) == [["new", "S-2"], ["new", "S-3"]]
    assert [c["id"] for c in result["cases"]] == ["S-2", "S-3"]
    assert result["pass"] is False and result["rerunsUsed"] == 0
    assert result["cases"][0] == {"id": "S-2", "new": "fail",
                                  "graderReason": result["cases"][0]["graderReason"]}  # fmt: skip


def test_a_smoke_scenario_run_passes_when_every_smoke_case_passes(env):
    ctx = context(env, FakeClaude(), SCENARIOS, tier="smoke", smoke=("S-1",))
    assert re_.run_scenario(ctx)["pass"] is True


def test_a_smoke_scenario_run_still_probes_the_new_arm(env):
    fake = FakeClaude(leak="CANARY-NEW")
    ctx = context(env, fake, SCENARIOS, tier="smoke", smoke=("S-1",))
    with pytest.raises(re_.IsolationError):
        re_.run_scenario(ctx)


def test_a_smoke_rubric_run_grades_once(env):
    fake = FakeClaude(rubric_scores=(60, 90))
    result = re_.run_rubric(context(env, fake, [], tier="smoke"))
    assert result["scores"] == [60] and result["pass"] is False


# The limit on calls open at once


def test_no_more_calls_than_the_limit_are_ever_open(env):
    open_now, most, lock = [0], [0], threading.Lock()

    def runner(cmd, stdin, env_, cwd, timeout):
        with lock:
            open_now[0] += 1
            most[0] = max(most[0], open_now[0])
        time.sleep(0.02)
        with lock:
            open_now[0] -= 1
        return 0, '{"result": ""}'

    ctx = context(env, runner, [])
    cfg = re_.make_config_dir(ctx, "x", "")
    re_.set_call_limit(3)
    try:
        threads = [threading.Thread(target=re_.run_claude, args=(ctx, cfg, ["-p"], "q"))
                   for _ in range(12)]  # fmt: skip
        for t in threads:
            t.start()
        for t in threads:
            t.join()
    finally:
        re_.set_call_limit(re_.DEFAULT_CALLS)
        re_.remove_config_dir(cfg)
    assert most[0] == 3


def test_a_block_of_permits_is_held_together_and_given_back(env):
    re_.set_call_limit(3)
    try:
        with re_.permits(2):
            got = re_._calls.acquire(blocking=False)
            assert got is True
            assert re_._calls.acquire(blocking=False) is False
            re_._calls.release()
        assert all(re_._calls.acquire(blocking=False) for _ in range(3))
    finally:
        re_.set_call_limit(re_.DEFAULT_CALLS)


def test_the_limit_can_be_read_back():
    re_.set_call_limit(5)
    try:
        assert re_.call_limit() == 5
    finally:
        re_.set_call_limit(re_.DEFAULT_CALLS)


# Stopping every running claude


def test_the_default_runner_returns_the_exit_code_and_stdout(tmp_path):
    code, out = re_.default_runner([sys.executable, "-c", "import sys; print(sys.stdin.read())"],
                                   "hi", dict(os.environ), tmp_path, 10)  # fmt: skip
    assert (code, out) == (0, "hi\n")


def test_a_timed_out_run_is_killed_with_its_process_group(tmp_path):
    with pytest.raises(subprocess.TimeoutExpired):
        re_.default_runner([sys.executable, "-c", "import time; time.sleep(30)"], "",
                           dict(os.environ), tmp_path, 0.2)  # fmt: skip
    assert re_.running() == 0


def test_a_process_that_starts_after_stop_all_is_ended_at_once(tmp_path):
    re_.stop_all()
    try:
        begun = time.monotonic()
        code, _, _ = re_.run_tracked([sys.executable, "-c", "import time; time.sleep(30)"], "",
                                     dict(os.environ), tmp_path, 60)  # fmt: skip
        assert code != 0 and time.monotonic() - begun < 10
        assert re_.stopped() is True
    finally:
        re_.reset_stop()
    assert re_.stopped() is False


def test_stop_all_ends_running_processes_and_refuses_new_ones(tmp_path, env):
    results = []
    cmd = [sys.executable, "-c", "import time; time.sleep(30)"]
    t = threading.Thread(target=lambda: results.append(
        re_.default_runner(cmd, "", dict(os.environ), tmp_path, 60)))  # fmt: skip
    t.start()
    for _ in range(100):
        if re_.running():
            break
        time.sleep(0.02)
    re_.stop_all()
    t.join(5)
    try:
        assert not t.is_alive() and results and results[0][0] != 0
        ctx = context(env, re_.default_runner, [])
        cfg = re_.make_config_dir(ctx, "x", "")
        assert re_.run_claude(ctx, cfg, ["-p"], "q") == (-1, "<stopped>")
        re_.remove_config_dir(cfg)
    finally:
        re_.reset_stop()
