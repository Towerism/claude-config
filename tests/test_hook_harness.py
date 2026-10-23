"""The real-harness hook checks, run against a scripted `claude`.

What the model does is faked; what the harness builds (the hook home, the
settings it passes, the stub gh, the environment) and how it scores the
streams are real.
"""

import json
import os
import stat
import subprocess
import tempfile
from pathlib import Path

import pytest

from eval import hook_harness as hh
from eval import run_eval as re_
from tests.checker_helpers import REPO_ROOT
from tests.criteria_probe import (
    assert_reads_are_selected,
    criteria_pointers,
    probe_reads,
    uncovered,
)
from tests.eval_fakes import leftovers, make_live_home, make_tree


@pytest.fixture(autouse=True)
def fresh_tempdir(monkeypatch):
    monkeypatch.setattr(tempfile, "tempdir", None)


@pytest.fixture
def ctx(tmp_path):
    root = tmp_path / "tmproot"
    root.mkdir()
    return re_.Context(
        live=make_live_home(tmp_path),
        root=root,
        runner=None,
        model=None,
        workers=1,
        out=tmp_path / "out",
        head_tree=make_tree(tmp_path / "head"),
        base_tree=None,
    )


def stream(answer, tool_inputs=()):
    """A stream-json transcript: one assistant message per tool call, then the result."""
    lines = [json.dumps({"type": "system", "subtype": "init"})]
    for name, inp in tool_inputs:
        lines.append(json.dumps({"type": "assistant", "message": {"content": [
            {"type": "tool_use", "name": name, "input": inp}]}}))  # fmt: skip
    lines.append(json.dumps({"type": "result", "result": answer}))
    return "\n".join(lines) + "\n"


def settings_arg(cmd):
    return Path(cmd[cmd.index("--settings") + 1]) if "--settings" in cmd else None


class OverlayFake:
    """Quotes the canary when the hook settings are passed, unless told otherwise per run."""

    def __init__(self, plan=None):
        self.plan = {k: list(v) for k, v in (plan or {}).items()}
        self.calls = []

    def __call__(self, cmd, stdin, env, cwd, timeout):
        settings = settings_arg(cmd)
        seen = None
        if settings:
            hooks = json.loads(settings.read_text())
            command = hooks["hooks"]["PostToolUse"][0]["hooks"][0]["command"]
            home = Path(command.split()[0].split("=", 1)[1])
            seen = {
                "settings": hooks,
                "overlay": (home / "skill-overlays" / f"{hh.SKILL}.md").read_text(),
                "hook": (home / "hooks" / "skill_overlay.py").is_file(),
            }
        self.calls.append({"cmd": cmd, "stdin": stdin, "seen": seen})
        kind = "control" if not settings else ("subagent" if "subagent" in stdin else "main")
        steps = self.plan.get(kind)
        step = (steps.pop(0) if len(steps) > 1 else steps[0]) if steps else "ok"
        if step == "read":
            return 0, stream(hh.CANARY, [("Read", {"file_path": "/x/skill-overlays/a.md"})])
        if step == "silent":
            return 0, stream("no overlay here")
        return 0, stream(f"I received: {hh.CANARY}" if settings else "nothing received")


def fixed_timer(ms):
    return lambda hook, home, runs: [ms] * runs


# The overlay check


def test_the_overlay_check_passes_when_only_the_hooked_runs_see_the_canary(ctx):
    fake = OverlayFake()
    ctx.runner = fake
    result = hh.run_overlay(ctx, timer=fixed_timer(30.0))
    assert result == {
        "pass": True,
        "rerunsUsed": 0,
        "p95Ms": 30.0,
        "cases": [
            {"id": "main", "result": "pass"},
            {"id": "subagent", "result": "pass"},
            {"id": "control", "result": "pass"},
            {"id": "p95", "result": "pass"},
        ],
    }
    assert leftovers(ctx.root) == []


def test_the_overlay_settings_render_the_fragment_entries_against_the_canary_home(ctx):
    fake = OverlayFake()
    ctx.runner = fake
    hh.run_overlay(ctx, timer=fixed_timer(30.0))
    seen = fake.calls[0]["seen"]
    assert set(seen["settings"]["hooks"]) == {"PostToolUse", "UserPromptExpansion"}
    command = seen["settings"]["hooks"]["UserPromptExpansion"][0]["hooks"][0]["command"]
    home = command.split()[0].split("=", 1)[1]
    assert command == f"CLAUDE_HOME={home} python3 -I {home}/hooks/skill_overlay.py"
    assert hh.CANARY in seen["overlay"]
    assert seen["hook"] is True


def test_the_canary_skill_is_the_repo_copy_of_senior_qa():
    assert hh.SKILL == "senior-qa"


def test_the_canary_home_links_the_trees_skill_and_not_the_live_one(ctx):
    live_skill = ctx.live / "skills" / hh.SKILL
    live_skill.mkdir(parents=True)
    (live_skill / "SKILL.md").write_text("live copy\n")
    home = hh.build_overlay_home(ctx)
    try:
        linked = home / "skills" / hh.SKILL
        assert linked.is_symlink()
        tree_skill = ctx.head_tree / "config" / "vendored-skills" / hh.SKILL
        assert linked.resolve() == tree_skill.resolve()
    finally:
        import shutil

        shutil.rmtree(home)


def test_every_overlay_run_has_a_copy_of_the_trees_canary_skill(ctx):
    copies = []

    def runner(cmd, stdin, env, cwd, timeout):
        skill = Path(env["CLAUDE_CONFIG_DIR"]) / "skills" / hh.SKILL
        copies.append((skill.is_symlink(), (skill / "SKILL.md").read_text()))
        return OverlayFake()(cmd, stdin, env, cwd, timeout)

    ctx.runner = runner
    hh.run_overlay(ctx, timer=fixed_timer(30.0))
    tree_md = (ctx.head_tree / "config" / "vendored-skills" / hh.SKILL / "SKILL.md").read_text()
    assert copies and set(copies) == {(False, tree_md)}


def test_a_tree_without_the_canary_skill_stops_the_overlay_check(ctx):
    import shutil

    shutil.rmtree(ctx.head_tree / "config" / "vendored-skills" / hh.SKILL)
    ctx.runner = OverlayFake()
    with pytest.raises(re_.Refusal, match="config/vendored-skills/senior-qa"):
        hh.run_overlay(ctx, timer=fixed_timer(30.0))
    assert leftovers(ctx.root) == []


def test_the_control_run_gets_no_hook_settings_and_every_run_streams_json(ctx):
    fake = OverlayFake()
    ctx.runner = fake
    hh.run_overlay(ctx, timer=fixed_timer(30.0))
    assert [settings_arg(c["cmd"]) is None for c in fake.calls] == [False, False, True]
    assert all(["-p", "--output-format", "stream-json", "--verbose"] == c["cmd"][1:5]
               for c in fake.calls)  # fmt: skip


def test_reading_the_overlay_file_fails_the_check_even_with_the_canary(ctx):
    ctx.runner = OverlayFake({"main": ["read"]})
    result = hh.run_overlay(ctx, timer=fixed_timer(30.0))
    assert result["pass"] is False
    assert result["cases"][0] == {"id": "main", "result": "fail"}
    assert result["rerunsUsed"] == 1


def test_a_control_run_that_sees_the_canary_fails_the_check(ctx):
    def runner(cmd, stdin, env, cwd, timeout):
        return 0, stream(hh.CANARY)

    ctx.runner = runner
    result = hh.run_overlay(ctx, timer=fixed_timer(30.0))
    assert result["cases"][2] == {"id": "control", "result": "fail"}
    assert result["pass"] is False


def test_a_subagent_run_that_fails_once_and_then_passes_uses_one_rerun(ctx):
    ctx.runner = OverlayFake({"subagent": ["silent", "ok"]})
    result = hh.run_overlay(ctx, timer=fixed_timer(30.0))
    assert result["pass"] is True and result["rerunsUsed"] == 1


def test_a_slow_hook_fails_the_check(ctx):
    ctx.runner = OverlayFake()
    result = hh.run_overlay(ctx, timer=fixed_timer(500.0))
    assert result["pass"] is False
    assert result["cases"][3] == {"id": "p95", "result": "fail"}


def test_raw_streams_are_kept_in_the_out_directory(ctx):
    ctx.runner = OverlayFake()
    hh.run_overlay(ctx, timer=fixed_timer(30.0))
    assert sorted(p.name for p in ctx.out.iterdir()) == [
        "control-1.stream", "main-1.stream", "subagent-1.stream", "timings.json"]  # fmt: skip


def test_the_p95_of_twenty_timings_is_the_nineteenth_smallest():
    assert hh.p95([float(n) for n in range(20, 0, -1)]) == 19.0


def test_the_hook_timer_runs_the_real_hook(ctx):
    home = hh.build_overlay_home(ctx)
    try:
        timings = hh.time_hook(home / "hooks" / "skill_overlay.py", home, 3)
    finally:
        import shutil

        shutil.rmtree(home)
    assert len(timings) == 3 and all(t > 0 for t in timings)


def test_the_real_hook_in_the_canary_home_injects_the_canary(ctx):
    home = hh.build_overlay_home(ctx)
    try:
        payload = {"hook_event_name": "PostToolUse", "tool_input": {"skill": hh.SKILL}}
        import subprocess
        import sys

        out = subprocess.run(
            [sys.executable, "-I", str(home / "hooks" / "skill_overlay.py")],
            input=json.dumps(payload), capture_output=True, text=True,
            env={"PATH": os.environ["PATH"], "CLAUDE_HOME": str(home)}, check=True,
        )  # fmt: skip
    finally:
        import shutil

        shutil.rmtree(home)
    assert hh.CANARY in json.loads(out.stdout)["hookSpecificOutput"]["additionalContext"]


def test_stream_lines_that_are_not_json_are_ignored():
    text = "garbage\n" + stream(hh.CANARY, [("Bash", {"command": "cat skill-overlays/x"})])
    assert hh.stream_findings(text) == {"canary": True, "overlayReads": ["Bash"]}


def test_a_stream_without_a_result_has_no_canary():
    assert hh.stream_findings('{"type": "assistant", "message": {"content": "text"}}\n') == {
        "canary": False,
        "overlayReads": [],
    }


# The merge check


def merge_reply(*denied):
    return json.dumps({"result": "done", "permission_denials": [
        {"tool_name": "Bash", "tool_input": {"command": c}} for c in denied]})  # fmt: skip


class MergeFake:
    def __init__(self, hook=("gh pr merge --help",), control=()):
        self.hook, self.control = list(hook), list(control)
        self.calls = []

    def __call__(self, cmd, stdin, env, cwd, timeout):
        settings = settings_arg(cmd)
        self.calls.append({"cmd": cmd, "env": env, "stdin": stdin,
                           "settings": json.loads(settings.read_text()) if settings else None,
                           "gh_dir": sorted(os.listdir(env["GH_CONFIG_DIR"]))})  # fmt: skip
        stub = Path(env["PATH"].split(os.pathsep)[0]) / "gh"
        self.calls[-1]["stub"] = stat.S_IMODE(stub.stat().st_mode) & 0o100
        return 0, merge_reply(*(self.hook if settings else self.control))


def test_the_merge_check_passes_when_only_the_hook_denies_the_merge(ctx):
    fake = MergeFake()
    ctx.runner = fake
    result = hh.run_merge(ctx)
    assert result == {
        "pass": True,
        "rerunsUsed": 0,
        "cases": [{"id": "hook", "result": "pass"}, {"id": "control", "result": "pass"}],
    }
    assert leftovers(ctx.root) == []


def test_every_merge_run_uses_the_stub_gh_and_an_empty_gh_config(ctx):
    fake = MergeFake()
    ctx.runner = fake
    hh.run_merge(ctx)
    for call in fake.calls:
        assert call["stub"] == 0o100
        assert call["gh_dir"] == []
        assert call["env"]["GH_TOKEN"] == "" and call["env"]["GITHUB_TOKEN"] == ""
    assert "gh pr merge --help" in fake.calls[0]["stdin"]


def test_the_merge_settings_hold_only_the_rendered_pre_tool_use_entry(ctx):
    fake = MergeFake()
    ctx.runner = fake
    hh.run_merge(ctx)
    hooks = fake.calls[0]["settings"]["hooks"]
    assert list(hooks) == ["PreToolUse"]
    command = hooks["PreToolUse"][0]["hooks"][0]["command"]
    home = command.split()[0].split("=", 1)[1]
    assert command.endswith(f"python3 -I {home}/hooks/merge_confirm.py")
    cmd = fake.calls[0]["cmd"]
    assert cmd[cmd.index("--allowedTools") + 1 : cmd.index("--allowedTools") + 3] == [
        "Bash(gh pr view *)", "Bash(gh pr merge *)"]  # fmt: skip
    assert cmd[cmd.index("--permission-prompts") + 1] == "none"


@pytest.mark.parametrize(
    ("hook", "control", "failing"),
    [
        (("gh pr merge --help", "gh pr view --help"), (), "hook"),
        ((), (), "hook"),
        (("gh pr merge --help",), ("gh pr merge --help",), "control"),
    ],
)
def test_the_merge_check_fails_when_a_run_denies_the_wrong_commands(ctx, hook, control, failing):
    ctx.runner = MergeFake(hook, control)
    result = hh.run_merge(ctx)
    assert result["pass"] is False
    assert {"id": failing, "result": "fail"} in result["cases"]
    assert result["rerunsUsed"] == 1


def test_an_unparsable_merge_reply_fails_the_run():
    assert hh.denied_commands("not json") is None
    assert hh.denied_commands('{"permission_denials": "x"}') is None
    assert hh.denied_commands('{"permission_denials": [{"tool_name": "Read"}]}') == []


def test_rendering_keeps_other_hook_fields_and_skips_missing_entries(tmp_path):
    fragment = json.loads((REPO_ROOT / "config" / "settings.fragment.json").read_text())
    settings = hh.render_entries(fragment, ["PreToolUse", "Nope"], tmp_path)
    assert list(settings["hooks"]) == ["PreToolUse"]
    assert settings["hooks"]["PreToolUse"][0]["matcher"] == "Bash"
    assert fragment["hooks"]["PreToolUse"][0]["hooks"][0]["command"].startswith("python3 -I {")


@pytest.mark.parametrize("check", [hh.run_overlay, hh.run_merge])
def test_missing_credentials_stop_a_hook_check_and_leave_nothing_behind(ctx, check):
    (ctx.live / ".credentials.json").unlink()
    ctx.runner = MergeFake()
    with pytest.raises(re_.Refusal):
        check(ctx)
    assert leftovers(ctx.root) == []


def test_a_merge_run_whose_reply_does_not_parse_fails(ctx):
    ctx.runner = lambda cmd, stdin, env, cwd, timeout: (1, "crashed")
    result = hh.run_merge(ctx)
    assert result["cases"] == [
        {"id": "hook", "result": "fail"},
        {"id": "control", "result": "fail"},
    ]


def test_tool_calls_that_do_not_touch_overlays_and_text_blocks_are_not_reads():
    line = json.dumps({"type": "assistant", "message": {"content": [
        {"type": "text", "text": "skill-overlays"},
        {"type": "tool_use", "name": "Skill", "input": {"skill": hh.SKILL}}]}})  # fmt: skip
    assert hh.stream_findings(line) == {"canary": False, "overlayReads": []}


def run_stub_gh(tmp_path, *args):
    gh = tmp_path / "bin" / "gh"
    gh.parent.mkdir(exist_ok=True)
    gh.write_text(hh.STUB_GH)
    gh.chmod(0o700)
    done = subprocess.run([str(gh), *args], capture_output=True, text=True, check=False)
    return done, (tmp_path / "bin" / "gh-calls.log").read_text().splitlines()


def test_the_stub_gh_prints_a_usage_line_for_help(tmp_path):
    done, calls = run_stub_gh(tmp_path, "pr", "merge", "--help")
    assert done.returncode == 0
    assert done.stdout.startswith("usage: gh pr merge")
    assert calls == ["pr merge --help"]


def test_the_stub_gh_prints_nothing_for_other_commands(tmp_path):
    done, calls = run_stub_gh(tmp_path, "pr", "view", "5")
    assert (done.returncode, done.stdout) == (0, "")
    assert calls == ["pr view 5"]


# The smoke tier (#77)


def test_a_smoke_overlay_check_skips_the_subagent_and_times_five_runs(ctx):
    timed = []
    ctx.runner, ctx.tier = OverlayFake(), "smoke"
    result = hh.run_overlay(ctx, timer=lambda hook, home, runs: timed.append(runs) or [30.0] * runs)
    assert [c["id"] for c in result["cases"]] == ["main", "control", "p95"]
    assert timed == [5] and result["pass"] is True


def test_a_smoke_overlay_check_does_not_rerun_a_failure(ctx):
    ctx.runner, ctx.tier = OverlayFake({"main": ["silent", "ok"]}), "smoke"
    result = hh.run_overlay(ctx, timer=fixed_timer(30.0))
    assert result["pass"] is False and result["rerunsUsed"] == 0


def test_a_smoke_merge_check_does_not_rerun_a_failure(ctx):
    fake = MergeFake(hook=())
    ctx.runner, ctx.tier = fake, "smoke"
    result = hh.run_merge(ctx)
    assert result["pass"] is False and result["rerunsUsed"] == 0
    assert len(fake.calls) == 2


def test_the_hook_checks_read_only_criteria_keys_their_selectors_cover(ctx, monkeypatch):
    reads = probe_reads(monkeypatch)
    ctx.runner = OverlayFake()
    hh.run_overlay(ctx, timer=fixed_timer(30.0))
    assert_reads_are_selected("hook-overlay", reads)
    reads.clear()
    ctx.runner = MergeFake()
    hh.run_merge(ctx)
    # A passing merge check never needs a retry, so it may read no key at all.
    assert uncovered(criteria_pointers("hook-merge"), reads) == []
