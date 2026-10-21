"""The real-harness check for the default-lifecycle hook, run against a scripted `claude`.

What the model does is faked. The fake runs the hook commands the harness
rendered, through a shell as Claude Code would, so the deployment-shaped
layout, the settings and the hook itself are all real.
"""

import json
import os
import subprocess
import tempfile
from pathlib import Path

import pytest

from eval import hook_harness as hh
from eval import run_eval as re_
from eval import session_harness as sh
from tests.checker_helpers import REPO_ROOT
from tests.eval_fakes import leftovers, make_live_home, make_tree


@pytest.fixture(autouse=True)
def fresh_tempdir(monkeypatch):
    monkeypatch.setattr(tempfile, "tempdir", None)


@pytest.fixture
def ctx(tmp_path):
    root = tmp_path / "tmproot"
    root.mkdir()
    head = make_tree(tmp_path / "head")
    hook = REPO_ROOT / "config" / "hooks" / "lifecycle_default.py"
    (head / "config" / "hooks" / "lifecycle_default.py").write_bytes(hook.read_bytes())
    return re_.Context(
        live=make_live_home(tmp_path),
        root=root,
        runner=None,
        model=None,
        workers=1,
        out=tmp_path / "out",
        head_tree=head,
        base_tree=None,
    )


def settings_arg(cmd):
    return Path(cmd[cmd.index("--settings") + 1]) if "--settings" in cmd else None


def hook_context(command, event):
    """What the rendered hook command prints for `event`, run through a shell."""
    done = subprocess.run(
        command, shell=True, input=json.dumps({"hook_event_name": event}),  # noqa: S602
        capture_output=True, text=True, env={"PATH": os.environ["PATH"]}, check=True,
    )  # fmt: skip
    assert done.stderr == ""
    return json.loads(done.stdout)["hookSpecificOutput"]["additionalContext"]


def stream(answer, agent_input=None, agent_reply=None):
    """A stream-json transcript, with one Agent call and its result when `agent_input` is given."""
    lines = [json.dumps({"type": "system", "subtype": "init"})]
    if agent_input is not None:
        call = {"type": "tool_use", "id": "toolu_1", "name": "Agent", "input": agent_input}
        lines.append(json.dumps({"type": "assistant", "message": {"content": [call]}}))
        inner = {"content": [{"type": "text", "text": "sub"}]}
        lines.append(json.dumps(
            {"type": "assistant", "parent_tool_use_id": "toolu_1", "message": inner}))  # fmt: skip
        result = {"type": "tool_result", "tool_use_id": "toolu_1",
                  "content": [{"type": "text", "text": agent_reply}]}  # fmt: skip
        lines.append(json.dumps({"type": "user", "message": {"content": [result]}}))
    lines.append(json.dumps({"type": "result", "result": answer}))
    return "\n".join(lines) + "\n"


class SessionFake:
    """Plays the model: it repeats what the rendered hooks print, unless a plan says otherwise.

    Plans per check ("main", "subagent", "control") are lists of steps, one per
    attempt: "ok", "silent", "leak-input" (the canary is in the Agent call's
    input) or "main-only" (the canary is in the final answer but not in the
    subagent's reply).
    """

    def __init__(self, plan=None):
        self.plan = {k: list(v) for k, v in (plan or {}).items()}
        self.calls = []

    def __call__(self, cmd, stdin, env, cwd, timeout):
        settings = settings_arg(cmd)
        hooks = json.loads(settings.read_text())["hooks"] if settings else None
        stub = Path(env["PATH"].split(os.pathsep)[0]) / "gh"
        self.calls.append({
            "cmd": cmd, "stdin": stdin, "env": env, "hooks": hooks,
            "gh_dir": sorted(os.listdir(env["GH_CONFIG_DIR"])),
            "stub": stub.read_text(), "stub_x": os.access(stub, os.X_OK),
        })  # fmt: skip
        kind = "control" if not hooks else ("subagent" if "subagent" in stdin else "main")
        steps = self.plan.get(kind)
        step = (steps.pop(0) if len(steps) > 1 else steps[0]) if steps else "ok"
        if not hooks:
            return 0, stream("No hook context.")
        session = hook_context(hooks["SessionStart"][0]["hooks"][0]["command"], "SessionStart")
        if kind == "main":
            return 0, stream("nothing" if step == "silent" else f"I was told: {session}")
        sub = hook_context(hooks["SubagentStart"][0]["hooks"][0]["command"], "SubagentStart")
        prompt = "Quote any context a hook gave you when you started."
        if step == "leak-input":
            prompt += f" {sub}"
        reply = "nothing" if step in ("silent", "main-only") else sub
        answer = f"The subagent said: {reply}" + (f" {session}" if step == "main-only" else "")
        return 0, stream(answer, {"description": "quote", "prompt": prompt}, reply)


def fixed_timer(ms):
    return lambda hook, xdg, runs: [ms] * runs


def test_the_check_passes_when_both_hooked_runs_quote_the_canary_and_the_control_doesnt(ctx):
    fake = SessionFake()
    ctx.runner = fake
    result = sh.run_session(ctx, timer=fixed_timer(40.0))
    assert result == {
        "pass": True,
        "rerunsUsed": 0,
        "p95Ms": 40.0,
        "cases": [
            {"id": "main", "result": "pass"},
            {"id": "subagent", "result": "pass"},
            {"id": "control", "result": "pass"},
            {"id": "p95", "result": "pass"},
        ],
    }
    assert leftovers(ctx.root) == []


def test_the_hook_states_the_canary_and_no_prompt_names_it(ctx, monkeypatch):
    monkeypatch.setattr(sh, "new_canary", lambda: "canary-lifecycle-fixed")
    fake = SessionFake()
    ctx.runner = fake
    sh.run_session(ctx, timer=fixed_timer(40.0))
    main = (ctx.out / "main-1.stream").read_text()
    assert "Default lifecycle skill on this machine: `canary-lifecycle-fixed`" in main
    assert all(sh.CANARY_PREFIX not in c["stdin"] for c in fake.calls)


def test_two_runs_use_different_canaries():
    assert sh.new_canary() != sh.new_canary()
    assert sh.new_canary().startswith(sh.CANARY_PREFIX)


def test_the_layout_reaches_the_release_through_the_home_link_and_current(ctx):
    layout = sh.build_layout(ctx, "canary-lifecycle-abc123")
    try:
        link = layout.home / "hooks" / "lifecycle_default.py"
        data = layout.root / "data"
        hooks = Path("config") / "hooks" / "lifecycle_default.py"
        assert os.readlink(link) == str(data / "current" / hooks)
        assert os.readlink(data / "current") == "releases/r"
        assert json.loads((data / "state.json").read_text()) == {"host": "hosts/eval.json"}
        profile = json.loads((data / "releases" / "r" / "hosts" / "eval.json").read_text())
        assert profile == {"defaultLifecycle": "canary-lifecycle-abc123"}
        assert list(layout.xdg.iterdir()) == []
        copied = data / "releases" / "r" / hooks
        assert copied.read_bytes() == (ctx.head_tree / hooks).read_bytes()
    finally:
        sh.remove_layout(layout)
    assert leftovers(ctx.root) == []


def test_the_settings_hold_only_the_two_rendered_entries_with_the_empty_config_dir(ctx):
    fake = SessionFake()
    ctx.runner = fake
    sh.run_session(ctx, timer=fixed_timer(40.0))
    hooks = fake.calls[0]["hooks"]
    assert set(hooks) == {"SessionStart", "SubagentStart"}
    for event in hooks:
        [entry] = hooks[event]
        assert "matcher" not in entry
        command = entry["hooks"][0]["command"]
        xdg, home = (part.split("=", 1)[1] for part in command.split()[:2])
        run = f"python3 -I {home}/hooks/lifecycle_default.py"
        assert command == f"XDG_CONFIG_HOME={xdg} CLAUDE_HOME={home} {run}"


def test_each_run_has_its_own_config_dir_the_stub_gh_and_no_github_login(ctx):
    fake = SessionFake()
    ctx.runner = fake
    sh.run_session(ctx, timer=fixed_timer(40.0))
    assert len(fake.calls) == 3
    for call in fake.calls:
        env = call["env"]
        assert call["gh_dir"] == []
        assert env["GH_TOKEN"] == "" and env["GITHUB_TOKEN"] == ""
        assert call["stub"] == sh.hook_harness.STUB_GH and call["stub_x"]
        assert Path(env["CLAUDE_CONFIG_DIR"]).name.startswith("arm-session.")


def test_the_main_and_control_runs_have_no_tools_and_only_hooked_runs_get_settings(ctx):
    fake = SessionFake()
    ctx.runner = fake
    sh.run_session(ctx, timer=fixed_timer(40.0))
    main, sub, control = fake.calls
    for call in (main, control):
        cmd = call["cmd"]
        assert cmd[cmd.index("--tools") + 1] == ""
    assert "--tools" not in sub["cmd"]
    assert [settings_arg(c["cmd"]) is None for c in fake.calls] == [False, False, True]
    assert all(c["cmd"][1:5] == ["-p", "--output-format", "stream-json", "--verbose"]
               for c in fake.calls)  # fmt: skip


@pytest.mark.parametrize("step", ["main-only", "leak-input", "silent"])
def test_the_subagent_check_counts_only_the_subagents_own_reply(ctx, step):
    ctx.runner = SessionFake({"subagent": [step]})
    result = sh.run_session(ctx, timer=fixed_timer(40.0))
    assert result["cases"][1] == {"id": "subagent", "result": "fail"}
    assert result["pass"] is False
    assert result["rerunsUsed"] == 1


def test_a_main_run_that_fails_once_then_passes_uses_one_rerun(ctx):
    ctx.runner = SessionFake({"main": ["silent", "ok"]})
    result = sh.run_session(ctx, timer=fixed_timer(40.0))
    assert result["pass"] is True and result["rerunsUsed"] == 1


def test_a_control_run_that_sees_the_canary_fails(ctx, monkeypatch):
    monkeypatch.setattr(sh, "new_canary", lambda: "canary-lifecycle-fixed")
    fake = SessionFake()

    def runner(cmd, stdin, env, cwd, timeout):
        code, out = fake(cmd, stdin, env, cwd, timeout)
        if settings_arg(cmd) is None:
            return code, stream("canary-lifecycle-fixed")
        return code, out

    ctx.runner = runner
    result = sh.run_session(ctx, timer=fixed_timer(40.0))
    assert result["cases"][2] == {"id": "control", "result": "fail"}


def test_a_slow_hook_fails_the_check(ctx):
    ctx.runner = SessionFake()
    result = sh.run_session(ctx, timer=fixed_timer(500.0))
    assert result["cases"][3] == {"id": "p95", "result": "fail"}
    assert result["pass"] is False


def test_raw_streams_and_timings_are_kept_in_the_out_directory(ctx):
    ctx.runner = SessionFake()
    sh.run_session(ctx, timer=fixed_timer(40.0))
    assert sorted(p.name for p in ctx.out.iterdir()) == [
        "control-1.stream", "main-1.stream", "subagent-1.stream", "timings.json"]  # fmt: skip


def test_the_timer_runs_the_real_hook_through_the_layout(ctx):
    layout = sh.build_layout(ctx, "canary-lifecycle-abc123")
    try:
        timings = sh.time_hook(layout.home / "hooks" / "lifecycle_default.py", layout.xdg, 3)
    finally:
        sh.remove_layout(layout)
    assert len(timings) == 3 and all(t > 0 for t in timings)


def test_missing_credentials_stop_the_check_and_leave_nothing_behind(ctx):
    (ctx.live / ".credentials.json").unlink()
    ctx.runner = SessionFake()
    with pytest.raises(re_.Refusal):
        sh.run_session(ctx, timer=fixed_timer(40.0))
    assert leftovers(ctx.root) == []


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("garbage\n[1]\n" + stream("x", {"prompt": "p"}, "CANARY here"), True),
        (stream("x", {"prompt": "p CANARY"}, "CANARY here"), False),
        (stream("CANARY", {"prompt": "p"}, "nothing"), False),
        (stream("CANARY"), False),
        # A result given as a plain string, and one for another tool call, also count.
        ('{"type": "assistant", "message": {"content": [{"type": "tool_use", "id": "a", '
         '"name": "Task", "input": {}}]}}\n'
         '{"type": "user", "message": {"content": [{"type": "tool_result", "tool_use_id": "b", '
         '"content": "CANARY"}, {"type": "tool_result", "tool_use_id": "a", '
         '"content": "CANARY too"}]}}\n', True),
        ('{"type": "user", "message": {"content": "text"}}\n', False),
        # A fork inherits the parent's SessionStart context, so its reply proves nothing.
        (stream("x", {"prompt": "p", "subagent_type": "fork"}, "CANARY here"), False),
        (stream("x", {"prompt": "p", "subagent_type": "general-purpose"}, "CANARY here"), True),
        ('{"type": "assistant", "message": {"content": [{"type": "text", "text": "CANARY"}]}}\n',
         False),
    ],
)  # fmt: skip
def test_only_an_agent_calls_own_result_counts_for_the_subagent(text, expected):
    assert sh.subagent_quoted(text, "CANARY") is expected


def test_the_final_answer_is_read_from_the_result_event():
    assert sh.final_answer(stream("the answer")) == "the answer"
    assert sh.final_answer("garbage\n") == ""


def test_the_command_line_takes_no_arguments_and_runs_without_a_record(monkeypatch, capsys):
    from tools.cfg import eval as eval_cmd

    calls = []
    monkeypatch.setattr(eval_cmd, "main", lambda argv: calls.append(argv) or 0)
    assert sh.main([]) == 0
    assert calls == [["--suite", "hook-session-start", "--no-record"]]
    assert sh.main(["x"]) == 2
    assert "usage: session_harness.py" in capsys.readouterr().err


def test_a_smoke_session_check_skips_the_subagent_and_times_five_runs(ctx):
    timed = []
    ctx.runner, ctx.tier = SessionFake(), "smoke"
    result = sh.run_session(ctx, timer=lambda hook, xdg, runs: timed.append(runs) or [40.0] * runs)
    assert [c["id"] for c in result["cases"]] == ["main", "control", "p95"]
    assert timed == [hh.SMOKE_TIMING_RUNS] and result["pass"] is True
