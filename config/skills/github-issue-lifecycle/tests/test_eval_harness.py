"""The eval runners with the model replaced: how they build configs, grade and decide."""

import importlib.util
import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest
from conftest import SKILL_DIR

CONFIG_DIR = SKILL_DIR.parents[1]


def load(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def harness():
    return load(SKILL_DIR / "evals" / "run_behaviour_evals.py", "harness")


@pytest.fixture
def trigger():
    return load(SKILL_DIR / "evals" / "run_trigger_evals.py", "trigger")


@pytest.fixture
def fake_home(tmp_path, harness, monkeypatch):
    """A home with credentials, a live settings.json carrying hooks, plugins and one live
    skill that must never reach an eval config."""
    home = tmp_path / "home"
    claude = home / ".claude"
    (claude / "plugins").mkdir(parents=True)
    (claude / "skills" / "live-only").mkdir(parents=True)
    (claude / ".credentials.json").write_text("{}")
    settings = {"hooks": {"PostToolUse": []}, "enabledPlugins": {"p@m": True}}
    (claude / "settings.json").write_text(json.dumps(settings))
    (home / ".claude.json").write_text("{}")
    monkeypatch.setattr(harness, "HOME", home)
    monkeypatch.setattr(harness, "HOME_CLAUDE", claude)
    return home


def repo_skill_names(config_dir):
    return {p.name for p in (config_dir / "skills").iterdir() if p.is_dir()}


def check_config(cfg, config_dir, global_config):
    skills = cfg / "skills"
    assert {p.name for p in skills.iterdir()} == repo_skill_names(config_dir)
    assert not (skills / "github-issue-lifecycle").is_symlink()
    assert (skills / "github-issue-lifecycle" / "SKILL.md").exists()
    others = [p for p in skills.iterdir() if p.name != "github-issue-lifecycle"]
    assert others and all(p.is_symlink() for p in others)
    assert all(p.resolve().parent == (config_dir / "skills").resolve() for p in others)
    settings = json.loads((cfg / "settings.json").read_text())
    assert "hooks" not in settings and settings["enabledPlugins"] == {"p@m": True}
    if global_config:
        expected = (config_dir / "CLAUDE.global.md").read_text()
        assert (cfg / "CLAUDE.md").read_text() == expected
        assert any((cfg / "skill-overlays").iterdir())
    else:
        assert not (cfg / "CLAUDE.md").exists()


@pytest.mark.parametrize("global_config", [False, True])
def test_config_is_built_from_the_repo_not_the_live_skills_or_hooks(
    tmp_path, harness, fake_home, global_config
):
    with harness.isolated_config(tmp_path / "root", global_config=global_config) as cfg:
        check_config(cfg, CONFIG_DIR, global_config)
    assert not (tmp_path / "root" / "config").exists()


def test_config_is_built_the_same_from_an_extracted_head_tree(tmp_path, monkeypatch):
    tree = tmp_path / "head" / "config"
    shutil.copytree(CONFIG_DIR / "skills", tree / "skills")
    shutil.copytree(CONFIG_DIR / "skill-overlays", tree / "skill-overlays")
    shutil.copy2(CONFIG_DIR / "CLAUDE.global.md", tree / "CLAUDE.global.md")
    copied = load(tree / "skills/github-issue-lifecycle/evals/run_behaviour_evals.py", "copied")
    home = tmp_path / "home"
    (home / ".claude" / "plugins").mkdir(parents=True)
    (home / ".claude" / ".credentials.json").write_text("{}")
    monkeypatch.setattr(copied, "HOME", home)
    monkeypatch.setattr(copied, "HOME_CLAUDE", home / ".claude")
    with copied.isolated_config(tmp_path / "root", global_config=True) as cfg:
        assert (cfg / "CLAUDE.md").read_text() == (tree / "CLAUDE.global.md").read_text()
        links = [p for p in (cfg / "skills").iterdir() if p.is_symlink()]
        assert links and all(p.resolve().parent == (tree / "skills").resolve() for p in links)


def test_no_run_can_reach_github_with_the_owners_login(tmp_path, harness, fake_home, monkeypatch):
    monkeypatch.setenv("CLAUDE_CODE_SSE_PORT", "1234")
    with harness.isolated_config(tmp_path / "root") as cfg:
        env = harness.base_env(cfg)
        assert env["GIT_TERMINAL_PROMPT"] == "0"
        gh_dir = Path(env["GH_CONFIG_DIR"])
        assert gh_dir.is_dir() and not any(gh_dir.iterdir())
        assert env["CLAUDE_CONFIG_DIR"] == str(cfg)
        assert not [k for k in env if k.startswith("CLAUDE") and k != "CLAUDE_CONFIG_DIR"]


def shim_env(tmp_path, harness):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    (bin_dir / "git").write_text(harness.FAKE_GIT)
    (bin_dir / "git").chmod(0o755)
    log = tmp_path / "git.log"
    env = {
        **os.environ,
        "PATH": f"{bin_dir}:{os.environ['PATH']}",
        "GIT_LOG": str(log),
        "REAL_GIT": shutil.which("git"),
    }
    return env, log


def test_git_shim_refuses_network_remotes_but_allows_the_local_origin(tmp_path, harness):
    case = {"id": 1, "origin_url": "https://github.com/o/r.git"}
    ws = harness.make_workspace(case, tmp_path)
    env, log = shim_env(tmp_path, harness)
    refused = subprocess.run(["git", "push", "origin", "main"], cwd=ws, env=env, text=True,
                             capture_output=True)  # fmt: skip
    assert refused.returncode == 1 and "refused" in refused.stderr
    assert ["push", "origin", "main"] in [json.loads(x) for x in log.read_text().splitlines()]
    network = ("https://example.com/x.git", "ssh://example.invalid/o/r.git", "host.invalid:o/r")
    for args in [["fetch", url] for url in network] + [["pull", "--rebase", network[2]]]:
        assert subprocess.run(["git", *args], cwd=ws, env=env, capture_output=True).returncode == 1
    local = harness.make_workspace({"id": 2}, tmp_path / "local")
    ok = subprocess.run(["git", "push", "-q", "origin", "main"], cwd=local, env=env,
                        capture_output=True, text=True)  # fmt: skip
    assert ok.returncode == 0, ok.stderr


def calls(*items):
    return [{"name": name, "input": data} for name, data in items]


@pytest.mark.parametrize(
    "order,passed",
    [
        ([("Skill", {"skill": "writing-docs"}), ("Bash", {"command": "gh issue comment 42"})], 1),
        ([("Bash", {"command": "gh issue comment 42"}), ("Skill", {"skill": "writing-docs"})], 0),
        ([("Bash", {"command": "gh issue comment 42"})], 0),
        ([("Skill", {"skill": "writing-docs"})], 0),
        ([("Skill", {"skill": "x:writing-docs"}), ("Bash", {"command": "gh issue comment 4"})], 1),
    ],
)
def test_loaded_before_compares_the_order_of_tool_calls(harness, order, passed):
    exp = "loaded-before: writing-docs ||| gh issue comment"
    graded = harness.grade([exp], [], [], "", [], calls=calls(*order))
    assert graded[0]["passed"] is bool(passed)


def result(case_id, passed_flags, critical=False):
    exps = [{"text": f"e{i}", "passed": ok} for i, ok in enumerate(passed_flags)]
    return {"id": case_id, "critical": critical, "passed": all(passed_flags), "expectations": exps}


def test_any_failing_critical_expectation_fails_the_suite(harness):
    results = [result(1, [True, False], critical=True), result(2, [True] * 10)]
    summary = harness.verdict(results)
    assert summary["pass"] is False and summary["criticalFailures"] == [1]


@pytest.mark.parametrize("passes,ok", [(9, True), (8, False)])
def test_other_cases_need_ninety_percent_of_their_expectations(harness, passes, ok):
    # Five two-expectation cases; each failure lands in a different case, so none collapses.
    failing = 10 - passes
    others = [result(i + 2, [i >= failing, True]) for i in range(5)]
    results = [result(1, [True], critical=True), *others]
    summary = harness.verdict(results)
    assert (summary["otherPassed"], summary["otherTotal"]) == (passes, 10)
    assert summary["pass"] is ok


def test_a_case_failing_more_than_half_its_expectations_fails_the_suite(harness):
    results = [result(1, [False, False, True]), result(2, [True] * 27)]
    summary = harness.verdict(results)
    assert summary["otherShare"] >= 0.9
    assert summary["pass"] is False and summary["collapsedCases"] == [1]


FAKE_CLAUDE = r"""#!/usr/bin/env python3
import json, os, sys
with open(os.environ["FAKE_CLAUDE_LOG"], "a") as f:
    f.write(json.dumps(sys.argv[1:]) + "\n")
plan = json.load(open(os.environ["FAKE_CLAUDE_PLAN"]))
n = sum(1 for _ in open(os.environ["FAKE_CLAUDE_LOG"]))
step = plan[min(n - 1, len(plan) - 1)]
print(json.dumps({"type": "system", "subtype": "init", "session_id": "s1",
                  "skills": step.get("skills", ["github-issue-lifecycle"])}))
if step.get("skill"):
    print(json.dumps({"type": "assistant", "message": {"content": [
        {"type": "tool_use", "name": "Skill", "input": {"skill": step["skill"]}}]}}))
print(json.dumps({"type": "result", "subtype": step.get("subtype", "success"), "result": "done"}))
"""


@pytest.fixture
def fake_claude(tmp_path, monkeypatch):
    """A `claude` on PATH that answers each call from a plan and logs its arguments."""
    bin_dir = tmp_path / "fake-bin"
    bin_dir.mkdir()
    (bin_dir / "claude").write_text(FAKE_CLAUDE)
    (bin_dir / "claude").chmod(0o755)
    log, plan = tmp_path / "claude.log", tmp_path / "plan.json"
    monkeypatch.setenv("PATH", f"{bin_dir}:{os.environ['PATH']}")
    monkeypatch.setenv("FAKE_CLAUDE_LOG", str(log))
    monkeypatch.setenv("FAKE_CLAUDE_PLAN", str(plan))

    def set_plan(steps):
        plan.write_text(json.dumps(steps))
        return log

    return set_plan


def test_a_failing_case_is_never_run_twice(tmp_path, harness, fake_home, fake_claude):
    log = fake_claude([{}])
    case = {"id": 7, "name": "two-turns", "turns": ["a", "b"], "expectations": ["gh-called: x"]}
    with harness.isolated_config(tmp_path / "root") as cfg:
        out = harness.run_case(case, cfg, None)
    assert out["passed"] is False and out["critical"] is False
    assert len(log.read_text().splitlines()) == 2


def decision(trigger, outcomes):
    """Run trigger.decide over a fixed list of (triggered, capped) outcomes."""
    it = iter(outcomes)
    calls_made = []

    def run():
        calls_made.append(1)
        return next(it)

    return trigger.decide(run), len(calls_made)


@pytest.mark.parametrize(
    "outcomes,runs,triggered",
    [
        ([(True, False), (True, False)], 2, True),
        ([(False, False), (False, False)], 2, False),
        ([(True, False), (False, False), (True, False)], 3, True),
        ([(False, False), (True, False), (False, True)], 3, False),
    ],
)
def test_a_third_run_happens_only_when_the_first_two_disagree(trigger, outcomes, runs, triggered):
    d, made = decision(trigger, outcomes)
    assert made == runs and d["runs"] == runs and d["triggered"] is triggered


def test_runs_cut_off_at_the_turn_limit_are_counted(trigger):
    d, _ = decision(trigger, [(False, True), (False, True)])
    assert d["capped"] == 2 and d["triggered"] is False


def test_a_capped_run_is_read_from_the_stream(trigger):
    capped = json.dumps({"type": "result", "subtype": "error_max_turns"})
    use = {"type": "tool_use", "name": "Skill", "input": {"skill": "github-issue-lifecycle"}}
    loaded = json.dumps({"type": "assistant", "message": {"content": [use]}})
    assert trigger.read_run(capped) == (False, True)
    assert trigger.read_run(loaded + "\n" + capped) == (True, False)


def test_the_trigger_verdict_is_unchanged(trigger):
    queries = [{"query": f"q{i}", "should_trigger": i < 10} for i in range(20)]
    hits = [True] * 9 + [False] + [False] * 10
    decisions = [{"runs": 2, "triggers": 2 * h, "capped": 0, "triggered": h} for h in hits]
    summary = trigger.summarise(queries, decisions)["summary"]
    assert summary["accuracy"] == 0.95 and summary["ok"] is True
    decisions[12] = {"runs": 3, "triggers": 2, "capped": 0, "triggered": True}
    summary = trigger.summarise(queries, decisions)["summary"]
    assert summary["near_misses_triggering"] == ["q12"] and summary["ok"] is False


def run_main(module, monkeypatch, out, *extra):
    monkeypatch.setattr("sys.argv", ["runner", "--out", str(out), "--workers", "1", *extra])
    return module.main()


def test_behaviour_main_writes_results_and_a_summary(
    tmp_path, harness, fake_home, fake_claude, monkeypatch
):
    fake_claude([{}])
    out = tmp_path / "results.json"
    code = run_main(harness, monkeypatch, out, "--ids", "1")
    data = json.loads(out.read_text())
    assert data["probe"]["ok"] is True and [r["id"] for r in data["results"]] == [1]
    assert set(data["summary"]) >= {"pass", "criticalFailures", "otherShare"}
    assert code == (0 if data["summary"]["pass"] else 1)


@pytest.mark.parametrize("module", ["harness", "trigger"])
def test_a_failed_isolation_probe_stops_before_any_case(
    tmp_path, request, module, fake_home, fake_claude, monkeypatch
):
    runner = request.getfixturevalue(module)
    if module == "trigger":
        monkeypatch.setattr(runner.harness, "HOME", fake_home)
        monkeypatch.setattr(runner.harness, "HOME_CLAUDE", fake_home / ".claude")
    log = fake_claude([{"skills": ["something-else"]}])
    out = tmp_path / "results.json"
    assert run_main(runner, monkeypatch, out) == 2
    assert json.loads(out.read_text())["results"] == []
    assert len(log.read_text().splitlines()) == 1


def test_trigger_main_samples_and_summarises(
    tmp_path, trigger, fake_home, fake_claude, monkeypatch
):
    monkeypatch.setattr(trigger.harness, "HOME", fake_home)
    monkeypatch.setattr(trigger.harness, "HOME_CLAUDE", fake_home / ".claude")
    log = fake_claude([{"skill": "github-issue-lifecycle"}])
    out = tmp_path / "trigger.json"
    code = run_main(trigger, monkeypatch, out)
    data = json.loads(out.read_text())
    queries = json.loads((SKILL_DIR / "evals" / "trigger-evals.json").read_text())
    assert all(r["runs"] == 2 for r in data["results"])
    assert len(log.read_text().splitlines()) == 1 + 2 * len(queries)
    assert code == 1 and data["summary"]["near_misses_triggering"]


@pytest.mark.parametrize(
    "bodies,passed",
    [([], True), (["see skills/foo/SPEC.md"], True), (["FR-1: the summary names the file"], False)],
)
def test_body_never_matches_passes_with_no_matching_call(harness, bodies, passed):
    gh_calls = [{"args": ["issue", "edit", "42", "--body-file", "b.md"], "body": b} for b in bodies]
    exp = "gh-body-never-matches: ^issue edit 42 ::: summary names the file"
    assert harness.grade([exp], gh_calls, [], "", [])[0]["passed"] is passed
