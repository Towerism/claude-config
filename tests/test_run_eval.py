"""The scenario and rubric suites, run against a scripted `claude`.

No model runs here. The tests check what the harness controls: which files
each arm sees, isolation and cleanup, grading, re-runs and the pass rule.
"""

import json
import stat
import tempfile

import pytest

from eval import run_eval as re_
from tests.checker_helpers import REPO_ROOT
from tests.eval_fakes import SKILL, FakeClaude, leftovers, make_live_home, make_tree, scenario


@pytest.fixture(autouse=True)
def fresh_tempdir(monkeypatch):
    """tempfile caches the temp root; clear it so each test's TMPDIR takes effect."""
    monkeypatch.setattr(tempfile, "tempdir", None)


@pytest.fixture
def env(tmp_path, monkeypatch):
    """A fake live home, a temp root outside it, and trees for the merge-base and HEAD."""
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.delenv("CLAUDE_CONFIG_DIR", raising=False)
    live = make_live_home(tmp_path)
    root = tmp_path / "tmproot"
    root.mkdir()
    return {"live": live, "root": root, "tmp": tmp_path}


def context(env, runner, scenarios, base_md="# Old\n", head_md="# New\n", base_tree=True, **kw):
    tmp = env["tmp"]
    head = make_tree(tmp / "head", md=head_md, scenarios=scenarios, **kw)
    base = make_tree(tmp / "base", md=base_md, scenarios=scenarios) if base_tree else None
    return re_.Context(
        live=env["live"],
        root=env["root"],
        runner=runner,
        model=None,
        workers=1,
        out=tmp / "out",
        head_tree=head,
        base_tree=base,
    )


# Isolation


def test_each_arm_sees_its_own_claude_md_with_its_canary(env):
    fake = FakeClaude()
    re_.run_scenario(context(env, fake, [scenario("S-01")]))
    answers = json.loads((env["tmp"] / "out" / "probes.json").read_text())
    assert "CANARY-OLD-5150" in answers["old"]["answer"]
    assert "CANARY-NEW-6260" in answers["new"]["answer"]


def test_arm_claude_md_comes_from_the_arm_tree(env):
    seen = {}

    def runner(cmd, stdin, env_, cwd, timeout):
        cfg = env_["CLAUDE_CONFIG_DIR"]
        seen.setdefault(cfg, (open(f"{cfg}/CLAUDE.md").read()))
        return FakeClaude()(cmd, stdin, env_, cwd, timeout)

    re_.run_scenario(context(env, runner, [scenario("S-01")]))
    texts = sorted(seen.values())
    assert any(t.startswith("# Old\n") and "CANARY-OLD-5150" in t for t in texts)
    assert any(t.startswith("# New\n") and "CANARY-NEW-6260" in t for t in texts)
    assert any(t.startswith("# Grader") for t in texts)


def test_arm_settings_drop_the_live_hooks_and_keep_the_rest(env):
    fake = FakeClaude()
    re_.run_scenario(context(env, fake, [scenario("S-01")]))
    assert all("hooks" not in c["settings"] for c in fake.calls)
    assert all(c["settings"]["enabledPlugins"] == {"x@y": True} for c in fake.calls)


def test_credentials_are_private_while_claude_runs_and_gone_afterwards(env):
    fake = FakeClaude()
    re_.run_scenario(context(env, fake, [scenario("S-01")]))
    assert fake.creds_modes and set(fake.creds_modes) == {0o600}
    assert leftovers(env["root"]) == []


def test_config_directories_are_private(env):
    modes = set()

    def runner(cmd, stdin, env_, cwd, timeout):
        from pathlib import Path

        modes.add(stat.S_IMODE(Path(env_["CLAUDE_CONFIG_DIR"]).stat().st_mode))
        return FakeClaude()(cmd, stdin, env_, cwd, timeout)

    re_.run_scenario(context(env, runner, [scenario("S-01")]))
    assert modes == {0o700}


def test_every_claude_run_works_in_a_temporary_directory_under_the_temp_root(env):
    fake = FakeClaude()
    re_.run_scenario(context(env, fake, [scenario("S-01")]))
    assert all(c["cwd"].parent == env["root"] for c in fake.calls)
    assert all(c["cfg"].parent == env["root"] for c in fake.calls)


def test_cleanup_runs_when_the_run_is_interrupted(env):
    fake = FakeClaude(raise_on="prompt S-02")
    ctx = context(env, fake, [scenario("S-01"), scenario("S-02")])
    with pytest.raises(KeyboardInterrupt):
        re_.run_scenario(ctx)
    assert leftovers(env["root"]) == []


def test_a_probe_that_shows_the_other_arms_canary_stops_the_run(env):
    fake = FakeClaude(leak="CANARY-NEW")
    with pytest.raises(re_.IsolationError):
        re_.run_scenario(context(env, fake, [scenario("S-01")]))
    assert not any("prompt S-01" in c["stdin"] for c in fake.calls)
    assert leftovers(env["root"]) == []


def test_missing_credentials_are_refused_before_claude_runs(env):
    (env["live"] / ".credentials.json").unlink()
    fake = FakeClaude()
    with pytest.raises(re_.Refusal, match="log in"):
        re_.run_scenario(context(env, fake, [scenario("S-01")]))
    assert fake.calls == []


def test_the_live_home_is_claude_config_dir_when_set(monkeypatch, tmp_path):
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path))
    assert re_.live_home() == tmp_path
    monkeypatch.delenv("CLAUDE_CONFIG_DIR")
    monkeypatch.setenv("HOME", str(tmp_path / "h"))
    assert re_.live_home() == tmp_path / "h" / ".claude"


# The temp root guard


def test_a_temp_root_inside_home_is_refused(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("TMPDIR", str(tmp_path / "t"))
    (tmp_path / "t").mkdir()
    with pytest.raises(re_.Refusal, match="TMPDIR=/tmp"):
        re_.temp_root(REPO_ROOT)


def test_a_temp_root_inside_the_repo_is_refused(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    (repo / "t").mkdir(parents=True)
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("TMPDIR", str(repo / "t"))
    with pytest.raises(re_.Refusal, match="inside the repo"):
        re_.temp_root(repo)


def test_a_temp_root_outside_home_and_repo_is_accepted(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("TMPDIR", str(tmp_path / "t"))
    (tmp_path / "t").mkdir()
    assert re_.temp_root(tmp_path / "repo") == (tmp_path / "t").resolve()


# Overlay context per arm


def test_each_arm_gets_the_overlay_its_own_tree_produces(env):
    fake = FakeClaude()
    sc = [scenario("S-12", loadsSkills=[SKILL])]
    ctx = context(env, fake, sc)
    (ctx.base_tree / "config" / "skill-overlays" / f"{SKILL}.md").write_text("OLD-OVERLAY\n")
    (ctx.head_tree / "config" / "skill-overlays" / f"{SKILL}.md").write_text(
        "NEW-OVERLAY `references/a.md`\n"
    )
    re_.run_scenario(ctx)
    prompts = [c["stdin"] for c in fake.calls if c["stdin"].endswith("prompt S-12")]
    old = [p for p in prompts if "OLD-OVERLAY" in p]
    new = [p for p in prompts if "NEW-OVERLAY" in p]
    assert len(old) == 1 and len(new) == 1
    assert f"Skill overlay for {SKILL}:" in new[0]
    assert "references/a.md`" in new[0] and "/skills/senior-qa/references/a.md" in new[0]


def test_an_arm_whose_tree_has_no_hook_only_hears_that_the_skill_loaded(env):
    sc = [scenario("S-12", loadsSkills=[SKILL])]
    ctx = context(env, FakeClaude(), sc)
    text = re_.overlay_prefix(ctx, make_tree(env["tmp"] / "nohook", hook=False), [SKILL])
    assert text == f"[You loaded {SKILL}.]\n\n"


def test_a_skill_without_an_overlay_only_hears_that_the_skill_loaded(env):
    ctx = context(env, FakeClaude(), [])
    text = re_.overlay_prefix(ctx, make_tree(env["tmp"] / "noov", overlay=None), [SKILL])
    assert text == f"[You loaded {SKILL}.]\n\n"
    assert leftovers(env["root"]) == []


# Grading and the pass rule


def test_a_scenario_that_passes_on_both_arms_passes(env):
    result = re_.run_scenario(context(env, FakeClaude(), [scenario("S-01")]))
    assert result["pass"] is True
    assert result["cases"] == [
        {
            "id": "S-01",
            "old": "pass",
            "new": "pass",
            "graderReason": result["cases"][0]["graderReason"],
        }
    ]
    assert len(result["cases"][0]["graderReason"]) == 200
    assert result["rerunsUsed"] == 0


def test_old_pass_new_fail_is_a_regression(env):
    fake = FakeClaude(verdicts={("new", "S-01"): [False]})
    result = re_.run_scenario(context(env, fake, [scenario("S-01")]))
    assert result["pass"] is False
    assert result["regressions"] == ["S-01"]
    assert result["cases"][0]["new"] == "fail"


def test_old_fail_new_pass_is_not_a_regression(env):
    fake = FakeClaude(verdicts={("old", "S-01"): [False]})
    result = re_.run_scenario(context(env, fake, [scenario("S-01")]))
    assert result["pass"] is True and result["regressions"] == []


def test_a_new_rule_scenario_must_pass_on_the_new_arm(env):
    fake = FakeClaude(verdicts={("new", "S-19"): [False], ("old", "S-19"): [False]})
    result = re_.run_scenario(context(env, fake, [scenario("S-19", tags=["new-rule-only"])]))
    assert result["pass"] is False
    assert result["newRuleFailures"] == ["S-19"]
    assert result["regressions"] == []


def test_the_superseded_tag_no_longer_excuses_a_regression(env):
    fake = FakeClaude(verdicts={("new", "S-01"): [False]})
    sc = [scenario("S-01", tags=["superseded:D-4"])]
    assert re_.run_scenario(context(env, fake, sc))["regressions"] == ["S-01"]


def test_a_case_that_fails_once_and_then_passes_uses_one_rerun(env):
    fake = FakeClaude(verdicts={("new", "S-01"): [False, True]})
    result = re_.run_scenario(context(env, fake, [scenario("S-01")]))
    assert result["pass"] is True and result["rerunsUsed"] == 1


def test_arm_specific_checks_apply_only_to_the_named_arm():
    sc = scenario("S-03", mustIncludeByArm={"new": ["extra"]})
    assert re_.criteria(sc, "new")["mustInclude"] == ["does the thing", "extra"]
    assert re_.criteria(sc, "old")["mustInclude"] == ["does the thing"]


@pytest.mark.parametrize(
    "reply",
    [
        "no json here",
        '{"mustInclude": [true]}',
        '{"mustInclude": [true, true], "mustNotInclude": [false]}',
        '{"mustInclude": "yes", "mustNotInclude": []}',
    ],
)
def test_an_unusable_grader_reply_fails_the_attempt(reply):
    sc = scenario("S-01")
    graded = re_.score_grade(reply, re_.criteria(sc, "new"))
    assert graded["pass"] is False


def test_an_unparsable_grader_reply_says_so():
    graded = re_.score_grade("nonsense", re_.criteria(scenario("S-01"), "new"))
    assert graded["reason"] == "grader reply unparsable"


def test_a_forbidden_behaviour_fails_the_attempt():
    crit = re_.criteria(scenario("S-01"), "new")
    reply = '{"mustInclude": [true], "mustNotInclude": [true], "reason": "did the bad thing"}'
    assert re_.score_grade(reply, crit) == {"pass": False, "reason": "did the bad thing"}


def test_the_v2_scenarios_no_longer_carry_the_superseded_tag():
    scenarios = json.loads((REPO_ROOT / "eval" / "scenarios.json").read_text())
    assert len(scenarios) == 19
    assert not any("superseded:D-4" in s.get("tags", []) for s in scenarios)


def test_raw_answers_go_to_the_out_directory_and_not_into_the_result(env):
    result = re_.run_scenario(context(env, FakeClaude(), [scenario("S-01")]))
    raw = json.loads((env["tmp"] / "out" / "results.json").read_text())
    assert any("answer new S-01" in a["answer"] for r in raw for a in r["attempts"])
    assert "answer new" not in json.dumps(result)


# Running claude


def test_a_failed_or_garbled_claude_run_is_an_empty_answer_marked_as_an_error(env):
    def runner(cmd, stdin, env_, cwd, timeout):
        return 1, "not json"

    ctx = context(env, runner, [])
    cfg = re_.make_config_dir(ctx, "x", "# x\n")
    try:
        assert re_.claude(ctx, cfg, "hi").startswith("<run error:")
    finally:
        re_.remove_config_dir(cfg)


def test_a_timeout_is_an_answer_marked_as_an_error(env):
    import subprocess

    def runner(cmd, stdin, env_, cwd, timeout):
        raise subprocess.TimeoutExpired(cmd, timeout)

    ctx = context(env, runner, [])
    cfg = re_.make_config_dir(ctx, "x", "# x\n")
    try:
        assert re_.claude(ctx, cfg, "hi").startswith("<run error:")
    finally:
        re_.remove_config_dir(cfg)
    assert leftovers(env["root"]) == []


def test_the_model_flag_reaches_the_arms_but_not_the_grader(env):
    fake = FakeClaude()
    ctx = context(env, fake, [scenario("S-01")])
    ctx.model = "sonnet"
    re_.run_scenario(ctx)
    arm_calls = [c for c in fake.calls if c["stdin"].endswith("prompt S-01")]
    grader_calls = [c for c in fake.calls if c["stdin"].startswith("You are grading")]
    assert all(c["cmd"][-2:] == ["--model", "sonnet"] for c in arm_calls)
    assert all("--model" not in c["cmd"] for c in grader_calls)


def test_the_default_runner_returns_the_exit_code_and_output(tmp_path):
    code, out = re_.default_runner(["cat"], "hello", {"PATH": "/usr/bin:/bin"}, tmp_path, 5)
    assert (code, out) == (0, "hello")


# The rubric


def test_the_rubric_path_comes_from_installed_plugins(env):
    path = re_.find_rubric(env["live"])
    assert path.read_text() == "Score it out of 100.\n"


def test_a_missing_rubric_plugin_is_refused(env):
    (env["live"] / "plugins" / "installed_plugins.json").write_text('{"plugins": {}}')
    with pytest.raises(re_.Refusal, match="claude-md-management"):
        re_.find_rubric(env["live"])


def test_an_unreadable_plugin_list_is_refused(env):
    (env["live"] / "plugins" / "installed_plugins.json").write_text("{")
    with pytest.raises(re_.Refusal, match="installed_plugins.json"):
        re_.find_rubric(env["live"])


def test_the_rubric_passes_on_a_rerun_above_80(env):
    fake = FakeClaude(rubric_scores=(78, 82))
    result = re_.run_rubric(context(env, fake, []))
    assert result == {"pass": True, "scores": [78, 82], "rerunsUsed": 1, "cases": []}


def test_the_rubric_fails_after_two_low_scores(env):
    fake = FakeClaude(rubric_scores=(70, 75))
    result = re_.run_rubric(context(env, fake, []))
    assert result["pass"] is False and result["scores"] == [70, 75]
    assert leftovers(env["root"]) == []


def test_the_rubric_scores_the_head_claude_md(env):
    fake = FakeClaude(rubric_scores=(90,))
    re_.run_rubric(context(env, fake, [], head_md="# The head file\n"))
    assert "# The head file" in fake.calls[0]["stdin"]
    assert "Score it out of 100." in fake.calls[0]["stdin"]


@pytest.mark.parametrize("reply", ["nope", '{"total": "high"}', "[1]"])
def test_an_unparsable_rubric_reply_scores_nothing(reply):
    assert re_.parse_rubric(reply) is None


def test_the_scenario_suite_needs_a_base_tree(env):
    with pytest.raises(re_.Refusal, match="CLAUDE.global.md"):
        re_.run_scenario(context(env, FakeClaude(), [scenario("S-01")], base_tree=False))


def test_a_config_directory_that_fails_halfway_is_removed(env):
    (env["live"] / "settings.json").write_text("{")
    ctx = context(env, FakeClaude(), [])
    with pytest.raises(ValueError):
        re_.make_config_dir(ctx, "x", "# x\n")
    assert leftovers(env["root"]) == []


def test_live_settings_are_optional(env):
    (env["live"] / "settings.json").unlink()
    ctx = context(env, FakeClaude(), [])
    cfg = re_.make_config_dir(ctx, "x", "# x\n")
    try:
        assert json.loads((cfg / "settings.json").read_text()) == {}
    finally:
        re_.remove_config_dir(cfg)


def test_a_tree_without_an_overlay_directory_only_hears_that_the_skill_loaded(env):
    import shutil

    ctx = context(env, FakeClaude(), [])
    tree = make_tree(env["tmp"] / "bare")
    shutil.rmtree(tree / "config" / "skill-overlays")
    assert re_.overlay_prefix(ctx, tree, [SKILL]) == f"[You loaded {SKILL}.]\n\n"


def test_a_grader_reply_with_broken_json_is_unparsable():
    graded = re_.score_grade("{not: json}", re_.criteria(scenario("S-01"), "new"))
    assert graded == {"pass": False, "reason": "grader reply unparsable"}


def test_a_broken_rubric_reply_scores_nothing():
    assert re_.parse_rubric("{not json}") is None


def test_the_rubric_lookup_skips_other_plugins_and_installs_without_the_file(env, tmp_path):
    good = tmp_path / "plugin-cache" / "cmm"
    installed = {
        "plugins": {
            "other@x": [{"installPath": str(tmp_path)}],
            "claude-md-management@y": [
                {"installPath": str(tmp_path / "empty")},
                {"installPath": str(good)},
            ],
        }
    }
    (env["live"] / "plugins" / "installed_plugins.json").write_text(json.dumps(installed))
    assert re_.find_rubric(env["live"]) == good / re_.RUBRIC_FILE


def test_a_new_rule_scenario_that_passes_on_the_new_arm_passes(env):
    fake = FakeClaude(verdicts={("old", "S-19"): [False]})
    result = re_.run_scenario(context(env, fake, [scenario("S-19", tags=["new-rule-only"])]))
    assert result["pass"] is True and result["newRuleFailures"] == []


def test_a_rubric_plugin_install_without_the_criteria_file_is_refused(env, tmp_path):
    installed = {
        "plugins": {
            "claude-md-management@y": [{"installPath": str(tmp_path / "empty")}],
            "zzz@x": [],
        }
    }
    (env["live"] / "plugins" / "installed_plugins.json").write_text(json.dumps(installed))
    with pytest.raises(re_.Refusal, match="not installed"):
        re_.find_rubric(env["live"])


def test_the_calling_sessions_claude_variables_do_not_reach_eval_runs(env, monkeypatch):
    monkeypatch.setenv("CLAUDE_EFFORT", "medium")
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "outer")
    monkeypatch.setenv("CLAUDECODE", "1")
    monkeypatch.setenv("KEEP_ME", "yes")
    seen = []

    def runner(cmd, stdin, env_, cwd, timeout):
        seen.append(env_)
        return 0, '{"result": "ok"}'

    ctx = context(env, runner, [])
    cfg = re_.make_config_dir(ctx, "x", "# x\n")
    try:
        re_.run_claude(ctx, cfg, ["-p"], "hi", extra_env={"GH_TOKEN": ""})
    finally:
        re_.remove_config_dir(cfg)
    claude_vars = {k for k in seen[0] if k.startswith("CLAUDE")}
    assert claude_vars == {"CLAUDE_CONFIG_DIR"}
    assert seen[0]["KEEP_ME"] == "yes" and seen[0]["GH_TOKEN"] == ""
