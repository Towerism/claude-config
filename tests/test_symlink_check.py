"""The symlink mechanism check, run against a scripted `claude`.

What the model does is faked. The layout, the links, the inventories, the
Edit and Write outcomes and every verdict are real: they run on a real temp
file system with real symlinks, as they do on the owner's machine.
"""

import hashlib
import json
import os
import re
import tempfile
from pathlib import Path

import pytest

from eval import run_eval as re_
from eval import symlink_check as sc
from tests.checker_helpers import REPO_ROOT
from tests.eval_fakes import leftovers, make_live_home, make_tree


@pytest.fixture(autouse=True)
def fresh_tempdir(monkeypatch):
    monkeypatch.setattr(tempfile, "tempdir", None)


@pytest.fixture
def ctx(tmp_path):
    root = tmp_path / "tmproot"
    root.mkdir()
    live = make_live_home(tmp_path)
    (live / "plugins" / "known_marketplaces.json").write_text("{}")
    return re_.Context(
        live=live,
        root=root,
        runner=None,
        model=None,
        workers=1,
        out=tmp_path / "out",
        head_tree=make_tree(tmp_path / "head"),
        base_tree=None,
    )


# ---- stream fixtures -------------------------------------------------------


def line(obj):
    return json.dumps(obj)


def init(skills):
    return line({"type": "system", "subtype": "init", "skills": skills})


def tool_use(tid, name, inp, parent=None):
    return line(
        {
            "type": "assistant",
            "parent_tool_use_id": parent,
            "message": {"content": [{"type": "tool_use", "id": tid, "name": name, "input": inp}]},
        }
    )


def tool_result(tid, text, parent=None, error=False):
    return line(
        {
            "type": "user",
            "parent_tool_use_id": parent,
            "message": {
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": tid,
                        "content": [{"type": "text", "text": text}],
                        "is_error": error,
                    }
                ]
            },
        }
    )


def user_text(text, parent=None):
    return line(
        {
            "type": "user",
            "parent_tool_use_id": parent,
            "message": {"content": [{"type": "text", "text": text}]},
        }
    )


def result(answer, denials=()):
    return line({"type": "result", "result": answer, "permission_denials": list(denials)})


def joined(*lines):
    return "\n".join(lines) + "\n"


CAN = {
    "claude_md": "CANARY-claude_md-aaaa1111",
    "skill": "CANARY-skill-bbbb2222",
    "overlay": "CANARY-overlay-cccc3333",
    "session": "CANARY-session-dddd4444",
}


def main_stream(canaries=CAN, skills=("probe-skill",), loaded=True, quote=None, base="/b"):
    quoted = canaries.values() if quote is None else quote
    lines = [init(list(skills))]
    if loaded:
        lines += [
            tool_use("t1", "Skill", {"skill": "probe-skill"}),
            tool_result("t1", "Launching skill: probe-skill"),
            user_text(f"Base directory for this skill: {base}\n\n# probe"),
        ]
    lines.append(result("\n".join(quoted)))
    return joined(*lines)


def sub_stream(
    canaries=CAN,
    sub_quote=None,
    main_quote=(),
    prompt="load probe-skill",
    parent="a1",
    main_skill=False,
    agent=True,
    base="/sb",
):
    quoted = canaries.values() if sub_quote is None else sub_quote
    lines = [init(["probe-skill"])]
    if main_skill:
        lines.append(tool_use("m1", "Skill", {"skill": "probe-skill"}))
    if agent:
        lines += [
            tool_use("a1", "Agent", {"prompt": prompt, "subagent_type": "general-purpose"}),
            tool_use("s1", "Skill", {"skill": "probe-skill"}, parent=parent),
            tool_result("s1", "Launching skill: probe-skill", parent=parent),
            user_text(f"Base directory for this skill: {base}", parent=parent),
            tool_result("a1", "\n".join(quoted)),
        ]
    lines.append(result("\n".join(main_quote)))
    return joined(*lines)


# ---- a fake claude ---------------------------------------------------------


def config_canaries(cfg):
    """The canaries a real session would get from the config directory it was given."""
    text = (cfg / "CLAUDE.md").read_text() + (
        cfg / "skills" / "probe-skill" / "SKILL.md"
    ).read_text()
    text += (cfg / "skill-overlays" / "probe-skill.md").read_text()
    text += (cfg / "hooks" / "session_probe.py").read_text()
    return dict(
        zip(
            ["claude_md", "skill", "overlay", "session"],
            re.findall(r"CANARY-[a-z_]+-[0-9a-f]+", text),
            strict=True,
        )
    )


class FakeClaude:
    """Answers each run as a working Claude Code would, unless a plan says otherwise.

    plan maps a run ("M", "S") to a list of behaviours, one per attempt, each
    "ok" or "miss"; "miss" leaves the overlay canary out of the answer.
    """

    def __init__(self, plan=None, edit="through", plugin=None, raise_on=None):
        self.plan = {k: list(v) for k, v in (plan or {}).items()}
        self.edit = edit
        self.plugin = plugin or (lambda cmd, env: (0, "ok"))
        self.raise_on = raise_on
        self.calls = []

    def __call__(self, cmd, stdin, env, cwd, timeout):
        self.calls.append({"cmd": cmd, "env": dict(env), "cwd": cwd, "stdin": stdin})
        if self.raise_on and self.raise_on(cmd):
            raise RuntimeError("boom")
        if cmd[1] == "plugin":
            return self.plugin(cmd, env)
        tools = cmd[cmd.index("--tools") + 1]
        cfg = Path(env["CLAUDE_CONFIG_DIR"])
        if tools == "Read,Edit,Write":
            return 0, self._edit(cwd)
        run = "M" if tools == "Skill" else "S"
        behaviour = self.plan.get(run, []).pop(0) if self.plan.get(run) else "ok"
        can = config_canaries(cfg)
        if behaviour == "miss":
            can = {k: v for k, v in can.items() if k != "overlay"}
        return 0, main_stream(can) if run == "M" else sub_stream(can)

    def _edit(self, cwd):
        uses = [
            tool_use(f"e{i}", n, {"file_path": str(cwd / p)})
            for i, (n, p) in enumerate(sc.EDIT_CASES.values())
        ]
        results = [tool_result(f"e{i}", "ok") for i in range(len(uses))]
        for _, rel in sc.EDIT_CASES.values():
            path = cwd / rel
            if self.edit == "through":
                path.write_text("edited line\n")
            elif self.edit == "replaced":
                path.unlink()
                path.write_text("edited line\n")
        if self.edit == "none":
            uses, results = [], []
        if self.edit == "error":
            results = [tool_result(f"e{i}", "denied", error=True) for i in range(len(uses))]
        return joined(init([]), *uses, *results, result("done"))


# ---- layout ----------------------------------------------------------------


def built(ctx, plain=False):
    cfg = re_.make_config_dir(ctx, "t", "")
    data = sc.build_layout(ctx.root, cfg, ctx.head_tree, sc.new_canaries(), "f" * 40, plain=plain)
    return cfg, data


def test_the_layout_links_every_managed_path_through_current_as_adr_0001_says(ctx):
    cfg, data = built(ctx)
    assert (data / "current").readlink() == Path("releases") / ("f" * 40)
    kinds = {
        "CLAUDE.md": "file",
        "skill-overlays": "dir",
        "hooks/skill_overlay.py": "file",
        "hooks/session_probe.py": "file",
        "skills/probe-skill": "dir",
    }
    for rel, kind in kinds.items():
        p = cfg / rel
        assert p.is_symlink(), rel
        assert (data / "current") in Path(os.readlink(p)).parents or str(os.readlink(p)).startswith(
            str(data / "current")
        ), rel
        assert p.is_dir() if kind == "dir" else p.is_file(), rel
    hook = (cfg / "hooks" / "skill_overlay.py").resolve()
    original = REPO_ROOT / "config" / "hooks" / "skill_overlay.py"
    assert (
        hashlib.sha256(hook.read_bytes()).digest() == hashlib.sha256(original.read_bytes()).digest()
    )


def test_the_settings_add_the_overlay_and_session_hooks_and_keep_the_live_keys(ctx):
    cfg, _ = built(ctx)
    settings = json.loads((cfg / "settings.json").read_text())
    assert settings["enabledPlugins"] == {"x@y": True}
    post = settings["hooks"]["PostToolUse"][0]
    assert post["matcher"] == "Skill"
    assert post["hooks"][0]["command"] == f"python3 -I {cfg}/hooks/skill_overlay.py"
    start = settings["hooks"]["SessionStart"][0]["hooks"][0]["command"]
    assert start == f"python3 -I {cfg}/hooks/session_probe.py"
    assert set(settings["hooks"]) == {"PostToolUse", "SessionStart"}


def test_the_session_probe_prints_its_canary_as_session_start_context(ctx):
    cfg, _ = built(ctx)
    code, out = re_.default_runner(
        ["python3", "-I", str(cfg / "hooks" / "session_probe.py")],
        "{}",
        dict(os.environ),
        ctx.root,
        30,
    )
    assert code == 0
    payload = json.loads(out)["hookSpecificOutput"]
    assert payload["hookEventName"] == "SessionStart"
    assert payload["additionalContext"] == config_canaries(cfg)["session"]


def test_the_plain_control_holds_the_same_files_as_copies(ctx):
    cfg, _ = built(ctx, plain=True)
    for rel in ["CLAUDE.md", "skill-overlays", "hooks/skill_overlay.py", "skills/probe-skill"]:
        assert not (cfg / rel).is_symlink(), rel
        assert (cfg / rel).exists(), rel
    assert set(config_canaries(cfg)) == {"claude_md", "skill", "overlay", "session"}


def test_each_canary_appears_in_exactly_one_file_under_the_temp_root(ctx):
    canaries = sc.new_canaries()
    cfg = re_.make_config_dir(ctx, "t", "")
    sc.build_layout(ctx.root, cfg, ctx.head_tree, canaries, "f" * 40)
    for value in canaries.values():
        holders = [
            p
            for p in ctx.root.rglob("*")
            if p.is_file() and not p.is_symlink() and value in p.read_text(errors="ignore")
        ]
        assert len(holders) == 1, (value, holders)


def test_canaries_are_fresh_for_every_layout():
    assert sc.new_canaries() != sc.new_canaries()
    assert all(re.fullmatch(r"CANARY-[a-z_]+-[0-9a-f]{8}", v) for v in sc.new_canaries().values())


# ---- verdicts --------------------------------------------------------------


def test_the_main_run_passes_when_the_stream_shows_every_mechanism():
    found = sc.judge_main(sc.parse_stream(main_stream()), CAN)
    assert set(found.values()) == {"pass"}
    assert set(found) == set(sc.MAIN_RESULTS)


@pytest.mark.parametrize(
    "stream, failing",
    [
        (main_stream(skills=["other"]), "skills-main"),
        (main_stream(loaded=False), "skill-load-main"),
        (main_stream(quote=[CAN["skill"], CAN["overlay"], CAN["session"]]), "claude-md-main"),
        (
            main_stream(
                quote=[CAN["claude_md"], CAN["skill"], CAN["session"], "CANARY-overlay-ffffffff"]
            ),
            "hook-overlay-main",
        ),
        (main_stream(quote=[CAN["claude_md"], CAN["skill"], CAN["overlay"]]), "session-start-main"),
    ],
)
def test_each_main_result_fails_on_its_own_condition(stream, failing):
    found = sc.judge_main(sc.parse_stream(stream), CAN)
    assert found[failing] == "fail"
    assert [k for k, v in found.items() if v != "pass"] == [failing]


def test_a_skill_load_whose_result_is_an_error_fails():
    stream = joined(
        init(["probe-skill"]),
        tool_use("t1", "Skill", {"skill": "probe-skill"}),
        tool_result("t1", "nope", error=True),
        result("\n".join(CAN.values())),
    )
    assert sc.judge_main(sc.parse_stream(stream), CAN)["skill-load-main"] == "fail"


def test_a_stream_without_an_init_event_marks_discovery_failed():
    stream = joined(result("\n".join(CAN.values())))
    assert sc.judge_main(sc.parse_stream(stream), CAN)["skills-main"] == "failed"


def test_garbage_lines_in_a_stream_are_skipped():
    parsed = sc.parse_stream("not json\n[1]\n" + main_stream())
    assert parsed.init_skills == ["probe-skill"]


def test_the_subagent_results_come_from_the_subagent_message():
    found = sc.judge_sub(sc.parse_stream(sub_stream()), CAN)
    assert set(found) == set(sc.SUB_RESULTS)
    assert set(found.values()) == {"pass"}


def test_canaries_only_in_the_main_answer_never_pass_the_subagent_results():
    stream = sub_stream(
        sub_quote=[CAN["skill"], CAN["overlay"]], main_quote=[CAN["claude_md"], CAN["session"]]
    )
    found = sc.judge_sub(sc.parse_stream(stream), CAN)
    assert found["claude-md-sub"] == "fail"
    assert found["session-start-sub"] == "fail"
    assert found["skill-load-sub"] == found["hook-overlay-sub"] == "pass"


@pytest.mark.parametrize(
    "stream",
    [
        sub_stream(prompt=f"report {CAN['claude_md']}"),
        sub_stream(main_skill=True),
    ],
)
def test_a_leaked_canary_or_a_main_skill_load_makes_the_subagent_attempt_inconclusive(stream):
    assert set(sc.judge_sub(sc.parse_stream(stream), CAN).values()) == {"inconclusive"}


def test_no_subagent_means_not_attempted():
    found = sc.judge_sub(sc.parse_stream(sub_stream(agent=False)), CAN)
    assert set(found.values()) == {"not-attempted"}


def test_a_skill_call_that_isnt_the_subagents_fails_the_subagent_load():
    found = sc.judge_sub(sc.parse_stream(sub_stream(parent="elsewhere")), CAN)
    assert found["skill-load-sub"] == "fail"


def test_the_base_directory_is_read_for_the_main_session_and_the_subagent():
    assert sc.base_directory(sc.parse_stream(main_stream(base="/x/skills/probe-skill")), None) == (
        "/x/skills/probe-skill"
    )
    assert sc.base_directory(sc.parse_stream(sub_stream(base="/y")), "a1") == "/y"
    assert sc.base_directory(sc.parse_stream(main_stream(loaded=False)), None) is None


# ---- Edit and Write --------------------------------------------------------


def edit_setup(ctx):
    cfg, data = built(ctx)
    workdir, before = sc.build_edit_dir(data)
    return workdir, before


def test_the_edit_directory_holds_only_the_three_paths(ctx):
    workdir, _ = edit_setup(ctx)
    assert sorted(p.name for p in workdir.iterdir()) == [
        "edit-link.md",
        "skill-link",
        "write-link.md",
    ]
    assert (workdir / "edit-link.md").is_symlink()
    assert (workdir / "skill-link").is_symlink() and (workdir / "skill-link").is_dir()


def test_the_edit_run_pre_approves_only_the_three_files(ctx):
    workdir, _ = edit_setup(ctx)
    args = sc.edit_args(workdir)
    assert args[args.index("--tools") + 1] == "Read,Edit,Write"
    allowed = args[args.index("--allowedTools") + 1 :]
    allowed = allowed[: next((i for i, a in enumerate(allowed) if a.startswith("--")), None)]
    assert "Read" in allowed
    rules = [a for a in allowed if a != "Read"]
    assert rules and all(re.fullmatch(r"(Edit|Write)\(.+\)", r) for r in rules)
    named = " ".join(rules)
    for _, rel in sc.EDIT_CASES.values():
        assert rel in named


@pytest.mark.parametrize(
    "behaviour, outcome",
    [
        ("through", "through"),
        ("replaced", "replaced"),
        ("none", "not-attempted"),
        ("error", "failed"),
    ],
)
def test_edit_and_write_outcomes_come_from_the_file_system(ctx, behaviour, outcome):
    workdir, before = edit_setup(ctx)
    fake = FakeClaude(edit=behaviour)
    _, out = fake(
        ["claude", "--tools", "Read,Edit,Write"], "", {"CLAUDE_CONFIG_DIR": "x"}, workdir, 1
    )
    found = sc.judge_edit(workdir, sc.parse_stream(out), before)
    expected = {k: outcome for k in sc.EDIT_CASES}
    if behaviour == "replaced":
        expected["edit-in-dir-link"] = "through"  # the directory link itself stays a link
    assert found == expected


def test_a_successful_tool_call_that_changes_nothing_is_failed(ctx):
    workdir, before = edit_setup(ctx)
    uses = [
        tool_use(f"e{i}", n, {"file_path": str(workdir / p)})
        for i, (n, p) in enumerate(sc.EDIT_CASES.values())
    ]
    out = joined(*uses, *[tool_result(f"e{i}", "ok") for i in range(3)], result("done"))
    assert set(sc.judge_edit(workdir, sc.parse_stream(out), before).values()) == {"failed"}


# ---- inventories -----------------------------------------------------------


def test_an_unchanged_inventory_has_no_differences(ctx):
    cfg, _ = built(ctx)
    assert sc.diff_inventory(sc.inventory(cfg), sc.inventory(cfg)) == []


def test_the_inventory_diff_names_every_kind_of_change(ctx):
    cfg, data = built(ctx)
    before = sc.inventory(cfg)
    (cfg / "CLAUDE.md").unlink()
    (cfg / "CLAUDE.md").write_text("plain")
    (cfg / "skill-overlays").unlink()
    (cfg / "skill-overlays").symlink_to(data)
    (cfg / "hooks" / "session_probe.py").unlink()
    (cfg / "skills" / "synced").mkdir()
    changes = sc.diff_inventory(before, sc.inventory(cfg))
    joined_changes = "\n".join(changes)
    for path in ["CLAUDE.md", "skill-overlays", "hooks/session_probe.py", "skills/synced"]:
        assert path in joined_changes, path
    assert len(changes) == 4


def test_inventory_mode_prints_json_and_writes_nothing(ctx, tmp_path, capsys):
    cfg, _ = built(ctx)
    snapshot = {p: p.lstat().st_mtime_ns for p in cfg.rglob("*")}
    assert sc.main(["--inventory", str(cfg)]) == 0
    printed = json.loads(capsys.readouterr().out)
    assert printed == sc.inventory(cfg)
    assert {p: p.lstat().st_mtime_ns for p in cfg.rglob("*")} == snapshot


def test_inventory_mode_refuses_a_missing_directory(tmp_path, capsys):
    assert sc.main(["--inventory", str(tmp_path / "nope")]) == 2


# ---- the plugin check ------------------------------------------------------


def plugin_fake(edit_link=False, settings_key=False, live=None, fail_cmd=None):
    def answer(cmd, env):
        cfg = Path(env["CLAUDE_CONFIG_DIR"])
        if fail_cmd and fail_cmd in cmd:
            return 1, "refused"
        if cmd[2:4] == ["install", "probe-plugin@probe-market"]:
            settings = json.loads((cfg / "settings.json").read_text())
            settings.setdefault("enabledPlugins", {})["probe-plugin@probe-market"] = True
            if settings_key:
                settings["theme"] = "changed"
            (cfg / "settings.json").write_text(json.dumps(settings))
            if edit_link:
                (cfg / "CLAUDE.md").unlink()
                (cfg / "CLAUDE.md").write_text("x")
            if live:
                (live / "settings.json").write_text("{}")
        return 0, "ok"

    return answer


def test_the_plugin_check_runs_its_commands_in_order_in_isolation(ctx):
    fake = FakeClaude(plugin=plugin_fake())
    ctx.runner = fake
    found = sc.run_plugin_check(ctx, "f" * 40)
    assert found["outcome"] == "pass"
    steps = [c["cmd"][2:] for c in fake.calls]
    assert [s[:2] for s in steps] == [
        ["marketplace", "add"],
        ["install", "probe-plugin@probe-market"],
        ["uninstall", "probe-plugin@probe-market"],
        ["marketplace", "remove"],
    ]
    for call in fake.calls:
        cfg = Path(call["env"]["CLAUDE_CONFIG_DIR"])
        home = Path(call["env"]["HOME"])
        assert home.is_relative_to(ctx.root) and home != Path.home()
        assert not (cfg / "plugins").is_symlink()
    assert len(found["commands"]) == 4


def test_the_plugin_config_starts_with_an_empty_plugins_directory_of_its_own(ctx):
    seen = {}

    def answer(cmd, env):
        cfg = Path(env["CLAUDE_CONFIG_DIR"])
        seen.setdefault(
            "plugins",
            (cfg / "plugins").is_symlink(),
        )
        seen.setdefault("contents", sorted(p.name for p in (cfg / "plugins").iterdir()))
        return 0, "ok"

    ctx.runner = FakeClaude(plugin=answer)
    sc.run_plugin_check(ctx, "f" * 40)
    assert seen == {"plugins": False, "contents": []}


@pytest.mark.parametrize("kwargs", [{"edit_link": True}, {"settings_key": True}])
def test_a_plugin_command_that_touches_a_link_or_another_key_fails(ctx, kwargs):
    ctx.runner = FakeClaude(plugin=plugin_fake(**kwargs))
    assert sc.run_plugin_check(ctx, "f" * 40)["outcome"] == "fail"


def test_a_refused_plugin_command_is_not_attempted(ctx):
    ctx.runner = FakeClaude(plugin=plugin_fake(fail_cmd="add"))
    found = sc.run_plugin_check(ctx, "f" * 40)
    assert found["outcome"] == "not-attempted"
    assert "refused" in found["commands"][0]["output"]


def test_a_change_to_the_live_config_stops_the_check(ctx):
    ctx.runner = FakeClaude(plugin=plugin_fake(live=ctx.live))
    with pytest.raises(sc.LiveConfigChanged, match="settings.json"):
        sc.run_plugin_check(ctx, "f" * 40)


# ---- the whole run ---------------------------------------------------------


def run(ctx, fake):
    ctx.runner = fake
    return sc.run_check(ctx, "f" * 40)


def outcomes(report):
    return {r["id"]: r["outcome"] for r in report["results"]}


def test_a_clean_run_uses_three_model_runs_and_passes(ctx):
    report = run(ctx, FakeClaude())
    found = outcomes(report)
    assert report["modelRuns"] == 3
    assert all(found[i] == "pass" for i in sc.REQUIRED)
    assert found["edit-file-link"] == "through"
    assert found["plugin-cli"] == "pass"
    assert found["sync-headless"] == "not-observed"
    assert sc.exit_code(report) == 0


def test_a_miss_then_a_pass_is_a_pass_with_both_runs_reported(ctx):
    report = run(ctx, FakeClaude(plan={"M": ["miss", "ok"]}))
    overlay = next(r for r in report["results"] if r["id"] == "hook-overlay-main")
    assert overlay["outcome"] == "pass"
    assert [r["attempt"] for r in overlay["runs"]] == [1, 2]
    assert report["modelRuns"] == 4
    assert sc.exit_code(report) == 0


def test_two_link_misses_and_a_plain_pass_is_a_failure_through_the_link(ctx):
    report = run(ctx, FakeClaude(plan={"M": ["miss", "miss", "ok"]}))
    overlay = next(r for r in report["results"] if r["id"] == "hook-overlay-main")
    assert overlay["outcome"] == "fails-through-link"
    assert [r["layout"] for r in overlay["runs"]] == ["links", "links", "plain"]
    assert sc.exit_code(report) == 1


def test_a_plain_miss_too_is_inconclusive(ctx):
    report = run(ctx, FakeClaude(plan={"S": ["miss", "miss", "miss"]}))
    assert outcomes(report)["hook-overlay-sub"] == "inconclusive"
    assert report["modelRuns"] == 5
    assert sc.exit_code(report) == 2


def test_a_failing_edit_run_is_never_rerun(ctx):
    report = run(ctx, FakeClaude(edit="error"))
    assert report["modelRuns"] == 3
    assert outcomes(report)["write-file-link"] == "failed"
    assert sc.exit_code(report) == 2


def test_a_replaced_link_is_recorded_without_changing_the_exit_code(ctx):
    report = run(ctx, FakeClaude(edit="replaced"))
    assert outcomes(report)["edit-file-link"] == "replaced"
    assert sc.exit_code(report) == 0


def test_every_command_runs_isolated_and_outside_home(ctx):
    fake = FakeClaude()
    run(ctx, fake)
    assert fake.calls
    for call in fake.calls:
        env, path = call["env"], call["env"]["PATH"].split(os.pathsep)
        assert (Path(path[0]) / "gh").is_file() or not Path(path[0]).exists()
        assert env["GH_CONFIG_DIR"] and env["GIT_TERMINAL_PROMPT"] == "0"
        assert env["GIT_ALLOW_PROTOCOL"] == "file"
        assert {k for k in env if k.startswith("CLAUDE")} <= {"CLAUDE_CONFIG_DIR", "CLAUDE_HOME"}
        assert Path(call["cwd"]).is_relative_to(ctx.root)


def test_the_stub_gh_is_first_on_path_for_model_runs(ctx):
    seen = []

    class Recorder(FakeClaude):
        def __call__(self, cmd, stdin, env, cwd, timeout):
            first = Path(env["PATH"].split(os.pathsep)[0])
            seen.append((first / "gh").is_file())
            return super().__call__(cmd, stdin, env, cwd, timeout)

    run(ctx, Recorder())
    assert seen and all(seen)


def test_an_error_mid_run_leaves_nothing_behind(ctx, monkeypatch):
    monkeypatch.delenv("CLAUDE_SESSION_ID", raising=False)
    fake = FakeClaude(raise_on=lambda cmd: "Agent,Skill" in cmd)
    with pytest.raises(RuntimeError):
        run(ctx, fake)
    assert leftovers(ctx.root) == []
    assert not list(ctx.root.rglob(".credentials.json"))


def test_session_variables_are_not_passed_on(ctx, monkeypatch):
    monkeypatch.setenv("CLAUDE_CODE_SESSION", "parent")
    fake = FakeClaude()
    run(ctx, fake)
    assert all("CLAUDE_CODE_SESSION" not in c["env"] for c in fake.calls)


def test_the_base_directories_are_reported(ctx):
    report = run(ctx, FakeClaude())
    assert report["baseDirectory"] == {"main": "/b", "sub": "/sb"}


def test_a_new_entry_under_skills_counts_as_sync_observed_when_links_survive(ctx):
    class Syncing(FakeClaude):
        def __call__(self, cmd, stdin, env, cwd, timeout):
            if "CLAUDE_CONFIG_DIR" in env and cmd[1] != "plugin":
                (Path(env["CLAUDE_CONFIG_DIR"]) / "skills" / "synced").mkdir(exist_ok=True)
            return super().__call__(cmd, stdin, env, cwd, timeout)

    assert outcomes(run(ctx, Syncing()))["sync-headless"] == "pass"


def test_a_link_changed_during_a_model_run_fails_sync(ctx):
    class Clobber(FakeClaude):
        def __call__(self, cmd, stdin, env, cwd, timeout):
            if cmd[1] != "plugin" and "Skill" == cmd[cmd.index("--tools") + 1]:
                link = Path(env["CLAUDE_CONFIG_DIR"]) / "skill-overlays"
                answer = super().__call__(cmd, stdin, env, cwd, timeout)
                target = link.resolve()
                link.unlink()
                link.symlink_to(target)  # same place, but no longer through current
                return answer
            return super().__call__(cmd, stdin, env, cwd, timeout)

    assert outcomes(run(ctx, Clobber()))["sync-headless"] == "fail"


def test_the_model_runs_use_the_tools_the_spec_names(ctx):
    fake = FakeClaude()
    run(ctx, fake)
    tools = [c["cmd"][c["cmd"].index("--tools") + 1] for c in fake.calls if c["cmd"][1] != "plugin"]
    assert tools == ["Skill", "Agent,Skill", "Read,Edit,Write"]
    for c in fake.calls:
        if c["cmd"][1] != "plugin":
            assert "stream-json" in c["cmd"] and "--verbose" in c["cmd"]


def test_the_subagent_prompt_tells_the_main_session_not_to_leak_canaries(ctx):
    fake = FakeClaude()
    run(ctx, fake)
    sub = next(c for c in fake.calls if "Agent,Skill" in c["cmd"])
    assert "CANARY-" in sub["stdin"] and "Agent tool" in sub["stdin"]
    assert not re.search(r"CANARY-[a-z_]+-[0-9a-f]{8}", sub["stdin"])


def test_a_timed_out_run_counts_as_failed_and_is_rerun(ctx):
    class Slow(FakeClaude):
        first = True

        def __call__(self, cmd, stdin, env, cwd, timeout):
            if self.first and cmd[1] != "plugin" and "Skill" == cmd[cmd.index("--tools") + 1]:
                self.first = False
                self.calls.append({"cmd": cmd, "env": env, "cwd": cwd, "stdin": stdin})
                raise __import__("subprocess").TimeoutExpired(cmd, timeout)
            return super().__call__(cmd, stdin, env, cwd, timeout)

    report = run(ctx, Slow())
    assert outcomes(report)["skills-main"] == "pass"
    assert report["modelRuns"] == 4


# ---- writing results -------------------------------------------------------


def test_results_are_written_scrubbed_and_small(ctx, tmp_path):
    report = run(ctx, FakeClaude())
    report["baseDirectory"]["main"] = str(Path.home() / ".claude" / "skills" / "probe-skill")
    report["note"] = f"made under {ctx.root}"
    out = tmp_path / "evidence"
    sc.write_results(report, out, ctx.root)
    for name in ["symlink-check.json", "symlink-check.md"]:
        text = (out / name).read_text()
        assert "/home/" not in text and str(ctx.root) not in text, name
        assert len(text.encode()) < 200_000
    data = json.loads((out / "symlink-check.json").read_text())
    assert {
        "claudeVersion",
        "commit",
        "date",
        "modelRuns",
        "results",
        "baseDirectory",
        "inventories",
        "pluginCommands",
    } <= set(data)
    assert data["baseDirectory"]["main"] == "~/.claude/skills/probe-skill"
    md = (out / "symlink-check.md").read_text()
    for rid in sc.REQUIRED:
        assert rid in md


def test_scrub_reaches_nested_values(tmp_path):
    root = tmp_path / "r"
    got = sc.scrub({"a": [f"{root}/x", {"b": f"{Path.home()}/y"}], "n": 3}, root)
    assert got == {"a": ["<tmp>/x", {"b": "~/y"}], "n": 3}


def test_long_evidence_is_cut(tmp_path):
    assert len(sc.scrub({"output": "x" * 10_000}, tmp_path)["output"]) <= sc.EXCERPT + 20


# ---- the command line ------------------------------------------------------


def test_a_dirty_tree_is_refused_before_anything_is_built(monkeypatch, capsys):
    monkeypatch.setattr(sc, "is_dirty", lambda repo: True)
    built_any = []
    monkeypatch.setattr(sc, "run_check", lambda *a, **k: built_any.append(1))
    assert sc.main([]) == 2
    assert built_any == []
    assert "changes" in capsys.readouterr().err


def test_a_missing_claude_is_refused(monkeypatch, capsys):
    monkeypatch.setattr(sc, "is_dirty", lambda repo: False)
    monkeypatch.setattr(sc.shutil, "which", lambda name: None)
    assert sc.main([]) == 2
    assert "claude" in capsys.readouterr().err


def test_main_runs_the_check_and_writes_results(monkeypatch, tmp_path, ctx):
    monkeypatch.setattr(sc, "is_dirty", lambda repo: False)
    monkeypatch.setattr(sc.shutil, "which", lambda name: "/usr/bin/claude")
    monkeypatch.setattr(sc.run_eval, "live_home", lambda: ctx.live)
    monkeypatch.setattr(sc.run_eval, "temp_root", lambda repo: ctx.root)
    monkeypatch.setattr(sc, "claude_version", lambda: "2.1.299 (Claude Code)")
    written = {}
    monkeypatch.setattr(sc, "write_results", lambda report, out, root: written.update(report))
    assert (
        sc.main(
            [], runner=FakeClaude(), repo=REPO_ROOT, evidence=tmp_path / "ev", out=tmp_path / "raw"
        )
        == 0
    )
    assert written["claudeVersion"] == "2.1.299 (Claude Code)"
    assert re.fullmatch(r"[0-9a-f]{40}", written["commit"])
    assert leftovers(ctx.root) == []


def test_main_reports_a_live_config_change_with_exit_2(monkeypatch, tmp_path, ctx, capsys):
    monkeypatch.setattr(sc, "is_dirty", lambda repo: False)
    monkeypatch.setattr(sc.shutil, "which", lambda name: "/usr/bin/claude")
    monkeypatch.setattr(sc.run_eval, "live_home", lambda: ctx.live)
    monkeypatch.setattr(sc.run_eval, "temp_root", lambda repo: ctx.root)

    def boom(*a, **k):
        raise sc.LiveConfigChanged("settings.json changed")

    monkeypatch.setattr(sc, "run_check", boom)
    assert (
        sc.main(
            [], runner=FakeClaude(), repo=REPO_ROOT, evidence=tmp_path / "ev", out=tmp_path / "raw"
        )
        == 2
    )
    assert "settings.json changed" in capsys.readouterr().err


def test_main_refuses_without_credentials(monkeypatch, tmp_path, ctx, capsys):
    monkeypatch.setattr(sc, "is_dirty", lambda repo: False)
    monkeypatch.setattr(sc.shutil, "which", lambda name: "/usr/bin/claude")
    (ctx.live / ".credentials.json").unlink()
    monkeypatch.setattr(sc.run_eval, "live_home", lambda: ctx.live)
    assert sc.main([], runner=FakeClaude(), repo=REPO_ROOT) == 2
    assert "credentials" in capsys.readouterr().err


@pytest.mark.parametrize(
    "found, code",
    [
        ({"skills-main": "pass"}, 0),
        ({"skills-main": "fails-through-link"}, 1),
        ({"skills-main": "inconclusive"}, 2),
        ({"skills-main": "pass", "edit-file-link": "not-attempted"}, 2),
        ({"skills-main": "fails-through-link", "plugin-cli": "failed"}, 2),
        ({"skills-main": "pass", "plugin-cli": "fail", "edit-file-link": "replaced"}, 0),
    ],
)
def test_the_exit_code_follows_the_results(found, code):
    report = {
        "results": [{"id": k, "outcome": v, "required": k in sc.REQUIRED} for k, v in found.items()]
    }
    assert sc.exit_code(report) == code


def test_tool_results_with_plain_or_missing_content_and_odd_blocks_still_parse():
    stream = joined(
        line(
            {
                "type": "user",
                "message": {
                    "content": [
                        {"type": "tool_result", "tool_use_id": "a", "content": "plain text"},
                        {"type": "tool_result", "tool_use_id": "b", "content": None},
                        "not a block",
                    ]
                },
            }
        ),
        line({"type": "assistant", "message": {"content": [{"type": "text", "text": "ignored"}]}}),
    )
    parsed = sc.parse_stream(stream)
    assert parsed.results == {"a": (False, "plain text"), "b": (False, "")}
    assert parsed.texts == [(None, "plain text"), (None, "")]


def test_an_empty_home_inventories_its_fixed_paths_as_missing(tmp_path):
    assert sc.inventory(tmp_path) == [
        {"path": "CLAUDE.md", "type": "missing"},
        {"path": "skill-overlays", "type": "missing"},
    ]


def test_a_settings_file_turned_into_a_link_fails_the_plugin_check(ctx):
    def answer(cmd, env):
        settings = Path(env["CLAUDE_CONFIG_DIR"]) / "settings.json"
        if cmd[2] == "install":
            real = settings.with_name("real-settings.json")
            settings.rename(real)
            settings.symlink_to(real)
        return 0, "ok"

    ctx.runner = FakeClaude(plugin=answer)
    found = sc.run_plugin_check(ctx, "f" * 40)
    assert found["outcome"] == "fail"
    assert "regular file" in found["note"]


def test_an_error_in_the_edit_run_leaves_nothing_behind(ctx):
    with pytest.raises(RuntimeError):
        run(ctx, FakeClaude(raise_on=lambda cmd: "Read,Edit,Write" in cmd))
    assert leftovers(ctx.root) == []
