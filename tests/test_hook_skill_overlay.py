"""The skill-overlay hook: what it injects, and what it refuses to do.

The hook runs after every Skill call and every slash command the owner types,
with the owner's privileges, so it must never fail, never echo the tool input
and never read outside the overlays, the plugin index and skill directories
(ADR-0007's hook review bar).
"""

import builtins
import json
import pathlib

import pytest

from tests.hook_helpers import load_hook, make_home, payload, run_main, run_script

CANARY = "CANARY-7f3a"


@pytest.fixture
def hook():
    return load_hook("skill_overlay")


@pytest.fixture
def home(tmp_path, monkeypatch):
    home = make_home(
        tmp_path,
        overlays={
            "foo": "Use `references/a.md` and `scripts/gone.py`.\n",
            "bundle:qa": "Plugin policy, see `references/b.md`.\n",
            "orphan": "Its skill directory doesn't exist.\n",
        },
        skills={"foo": ["references/a.md"]},
        plugins={"bundle@market": {"qa": ["references/b.md"]}},
    )
    monkeypatch.setenv("CLAUDE_HOME", str(home))
    return home


def skill_call(skill):
    return payload(hook_event_name="PostToolUse", tool_name="Skill", tool_input={"skill": skill})


def context_of(stdout):
    return json.loads(stdout)["hookSpecificOutput"]["additionalContext"]


# What it injects


def test_a_skill_call_gets_its_overlay_with_paths_made_absolute(hook, home, monkeypatch, capsys):
    out, err = run_main(hook, skill_call("foo"), monkeypatch, capsys)
    assert json.loads(out)["hookSpecificOutput"]["hookEventName"] == "PostToolUse"
    context = context_of(out)
    assert context.startswith("Skill overlay for foo:\n")
    assert f"`{home}/skills/foo/references/a.md`" in context
    assert context.endswith("missing: scripts/gone.py")
    assert err == ""


def test_a_typed_slash_command_gets_the_same_overlay(hook, home, monkeypatch, capsys):
    stdin = payload(hook_event_name="UserPromptExpansion", command_name="foo")
    out, _ = run_main(hook, stdin, monkeypatch, capsys)
    assert context_of(out).startswith("Skill overlay for foo:\n")


def test_a_prompt_starting_with_a_slash_command_gets_the_overlay(hook, home, monkeypatch, capsys):
    stdin = payload(hook_event_name="UserPromptSubmit", prompt="  /foo do the thing")
    out, _ = run_main(hook, stdin, monkeypatch, capsys)
    assert context_of(out).startswith("Skill overlay for foo:\n")


def test_a_plugin_skill_resolves_through_the_plugin_index(hook, home, monkeypatch, capsys):
    out, _ = run_main(hook, skill_call("bundle:qa"), monkeypatch, capsys)
    assert "plugin-cache/bundle-market/skills/qa/references/b.md`" in context_of(out)
    assert "missing:" not in context_of(out)


def test_a_bare_name_matches_the_one_plugin_overlay_ending_in_it(hook, home, monkeypatch, capsys):
    out, _ = run_main(hook, skill_call("qa"), monkeypatch, capsys)
    assert context_of(out).startswith("Skill overlay for bundle:qa:\n")


def test_the_config_directory_defaults_to_dot_claude_in_home(hook, tmp_path, monkeypatch, capsys):
    home = make_home(tmp_path, overlays={"foo": "Hi.\n"}, skills={"foo": []})
    monkeypatch.delenv("CLAUDE_HOME", raising=False)
    monkeypatch.setattr(pathlib.Path, "home", classmethod(lambda cls: home.parent))
    home.rename(home.parent / ".claude")
    out, _ = run_main(hook, skill_call("foo"), monkeypatch, capsys)
    assert context_of(out) == "Skill overlay for foo:\nHi.\n"


# When there is nothing to inject


@pytest.mark.parametrize(
    "stdin",
    [
        skill_call("nothing"),
        skill_call("other:foo"),
        payload(hook_event_name="UserPromptSubmit", prompt="no slash command here"),
        payload(hook_event_name="UserPromptSubmit"),
        payload(hook_event_name="PostToolUse", tool_name="Skill"),
        payload(hook_event_name="SessionStart"),
        payload(),
    ],
    ids=[
        "no overlay",
        "bundled name with no exact overlay",
        "prompt without a slash command",
        "prompt missing",
        "tool input missing",
        "another event",
        "no event",
    ],
)
def test_nothing_is_printed_when_no_overlay_applies(hook, home, monkeypatch, capsys, stdin):
    assert run_main(hook, stdin, monkeypatch, capsys) == ("", "")


def test_two_plugin_overlays_ending_in_the_same_name_inject_nothing(
    hook, home, monkeypatch, capsys
):
    (home / "skill-overlays" / "other:qa.md").write_text("Ambiguous.\n", encoding="utf-8")
    assert run_main(hook, skill_call("qa"), monkeypatch, capsys) == ("", "")


def test_a_missing_overlay_directory_injects_nothing(hook, tmp_path, monkeypatch, capsys):
    home = make_home(tmp_path, skills={"foo": []})
    monkeypatch.setenv("CLAUDE_HOME", str(home))
    assert run_main(hook, skill_call("foo"), monkeypatch, capsys) == ("", "")


# When the overlay can't be loaded


def failure_text(skill, reason):
    return (
        f"Skill overlay for {skill} could not be loaded ({reason}); "
        f"read ~/.claude/skill-overlays/{skill}.md manually."
    )


def test_an_overlay_without_its_skill_directory_says_so(hook, home, monkeypatch, capsys):
    out, err = run_main(hook, skill_call("orphan"), monkeypatch, capsys)
    assert context_of(out) == failure_text("orphan", "user skill directory not found for orphan")
    assert "Traceback" in err


def test_an_unreadable_plugin_index_says_so(hook, home, monkeypatch, capsys):
    (home / "plugins" / "installed_plugins.json").write_text("{not json", encoding="utf-8")
    out, _ = run_main(hook, skill_call("bundle:qa"), monkeypatch, capsys)
    assert context_of(out).startswith(
        "Skill overlay for bundle:qa could not be loaded (cannot read installed_plugins.json: "
    )


@pytest.mark.parametrize(
    "plugins",
    [
        {"plugins": {"other@market": [{"installPath": "/nonexistent"}]}},
        {"plugins": {"bundle@market": [{"installPath": "/nonexistent"}]}},
        {"plugins": {"bundle@market": [{}]}},
        {"version": 2},
    ],
    ids=["another bundle only", "install path missing", "no install path", "no plugins key"],
)
def test_a_plugin_skill_with_no_usable_install_says_so(hook, home, monkeypatch, capsys, plugins):
    (home / "plugins" / "installed_plugins.json").write_text(json.dumps(plugins), encoding="utf-8")
    out, _ = run_main(hook, skill_call("bundle:qa"), monkeypatch, capsys)
    assert context_of(out) == failure_text("bundle:qa", "no active install of bundle:qa")


def test_an_error_with_no_message_is_named_by_its_type(hook, home, monkeypatch, capsys):
    def fail(*args, **kwargs):
        raise PermissionError()

    monkeypatch.setattr(pathlib.Path, "read_text", fail)
    out, _ = run_main(hook, skill_call("foo"), monkeypatch, capsys)
    assert context_of(out) == failure_text("foo", "PermissionError")


# Hostile or malformed input


@pytest.mark.parametrize(
    "stdin",
    [
        b"",
        b"not json",
        b"\xff\xfe\x00",
        b"[1, 2]",
        b'"a string"',
        b"null",
        payload(hook_event_name="PostToolUse", tool_input=[1, 2]),
        payload(hook_event_name="PostToolUse", tool_input="foo"),
        payload(hook_event_name="PostToolUse", tool_input={"skill": 5}),
        payload(hook_event_name="UserPromptExpansion", command_name=7),
        payload(hook_event_name="UserPromptSubmit", prompt=5),
        payload(hook_event_name="UserPromptSubmit", prompt={"text": "/foo"}),
        payload(hook_event_name=["PostToolUse"], tool_input={"skill": "foo"}),
    ],
    ids=[
        "empty",
        "not json",
        "not utf-8",
        "json array",
        "json string",
        "json null",
        "tool input a list",
        "tool input a string",
        "skill a number",
        "command name a number",
        "prompt a number",
        "prompt an object",
        "event name a list",
    ],
)
def test_malformed_input_prints_nothing_and_never_raises(hook, home, monkeypatch, capsys, stdin):
    assert run_main(hook, stdin, monkeypatch, capsys) == ("", "")


@pytest.mark.parametrize("skill", ["../../etc/passwd", "a/b", "A", "x:y:z", "", "foo "])
def test_skill_names_that_could_build_a_bad_path_are_refused(
    hook, home, monkeypatch, capsys, skill
):
    assert run_main(hook, skill_call(skill), monkeypatch, capsys) == ("", "")


def test_only_overlays_and_the_plugin_index_are_ever_read(hook, home, monkeypatch, capsys):
    read = []
    real_read_text, real_open = pathlib.Path.read_text, builtins.open

    def record_read_text(self, *args, **kwargs):
        read.append(self)
        return real_read_text(self, *args, **kwargs)

    def record_open(file, *args, **kwargs):
        read.append(pathlib.Path(file))
        return real_open(file, *args, **kwargs)

    monkeypatch.setattr(pathlib.Path, "read_text", record_read_text)
    monkeypatch.setattr(builtins, "open", record_open)
    for skill in ["foo", "bundle:qa", "qa", "orphan", "nothing", "../../etc/passwd"]:
        run_main(hook, skill_call(skill), monkeypatch, capsys)
    allowed = {home / "plugins" / "installed_plugins.json"}
    assert read
    assert all(p.parent == home / "skill-overlays" or p in allowed for p in read), read


def test_the_tool_input_is_never_echoed(hook, home, monkeypatch, capsys):
    stdin = payload(
        hook_event_name="PostToolUse",
        tool_name=CANARY,
        tool_input={"skill": "foo", "args": CANARY},
        session_id=CANARY,
        cwd=f"/tmp/{CANARY}",
    )
    out, err = run_main(hook, stdin, monkeypatch, capsys)
    assert out
    assert CANARY not in out + err


# As Claude Code runs it


def test_the_script_exits_0_with_nothing_on_stderr_for_malformed_input(home):
    done = run_script("skill_overlay", payload(hook_event_name="PostToolUse", tool_input=[1]), home)
    assert (done.returncode, done.stdout, done.stderr) == (0, b"", b"")


def test_non_ascii_text_works_whatever_the_locale(tmp_path):
    home = make_home(
        tmp_path, overlays={"foo": "Café: `references/ü.md`\n"}, skills={"foo": ["references/ü.md"]}
    )
    stdin = payload(hook_event_name="UserPromptSubmit", prompt="/foo résumé")
    outputs = {lc: run_script("skill_overlay", stdin, home, LC_ALL=lc) for lc in ("C", "C.UTF-8")}
    assert {d.returncode for d in outputs.values()} == {0}
    assert outputs["C"].stdout == outputs["C.UTF-8"].stdout
    assert "Café" in context_of(outputs["C"].stdout)
