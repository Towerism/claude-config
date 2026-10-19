"""The default-lifecycle hook: each session and subagent learns its machine's lifecycle skill.

The hook runs at every session start and every subagent start, so it must
finish fast, never block, never fail, and print nothing it didn't build itself
(ADR-0007's hook review bar). It finds its deployment from its own path, then
reads at most three files: the machine-local override, the deploy state file
and the host profile (ADR-0011).
"""

import json
import os
import statistics
import subprocess
import sys
import time

import pytest

from tests.hook_helpers import HOOKS, load_hook, payload, run_main

TAIL = (
    "Use it to start and close out code changes unless the repo's CLAUDE.md names its "
    "tracker; the repo's choice wins."
)
HOST_LINE = (
    "Default lifecycle skill on this machine: `github-issue-lifecycle` "
    f"(set in `hosts/personal.json`). {TAIL}"
)
XDG_LABEL = "$XDG_CONFIG_HOME/claude-config/local.json"
HOME_LABEL = "~/.config/claude-config/local.json"
SECRET = "CANARY-do-not-print-91c2"


def expected(event, line):
    body = {"hookSpecificOutput": {"hookEventName": event, "additionalContext": line}}
    return json.dumps(body) + "\n"


def local_line(name, label):
    return (
        f"Default lifecycle skill on this machine: `{name}` "
        f"(set in `{label}`, which overrides the host profile). {TAIL}"
    )


def make_deployment(
    root,
    default="github-issue-lifecycle",
    host="hosts/personal.json",
    profile=None,
    state=None,
    local=None,
):
    """A cfg deployment as `cfg deploy` lays it out, plus an XDG config directory.

    profile and state replace the whole file's text when given; local, when
    given, is the local override's text (str) or bytes. Returns the Claude home
    hook link, which reaches the release through `current`, and the XDG config dir.
    """
    data = root / "data"
    release = data / "releases" / "r"
    (release / "config" / "hooks").mkdir(parents=True)
    hook_copy = release / "config" / "hooks" / "lifecycle_default.py"
    hook_copy.write_bytes((HOOKS / "lifecycle_default.py").read_bytes())
    (release / "hosts").mkdir()
    if profile is None:
        profile = json.dumps({"name": "personal", "defaultLifecycle": default})
    (release / "hosts" / "personal.json").write_text(profile, encoding="utf-8")
    (data / "current").symlink_to("releases/r")
    if state is None:
        state = json.dumps({"schema": 1, "host": host, "release": "r"})
    (data / "state.json").write_text(state, encoding="utf-8")
    home = root / "claude-home"
    (home / "hooks").mkdir(parents=True)
    link = home / "hooks" / "lifecycle_default.py"
    link.symlink_to(data / "current" / "config" / "hooks" / "lifecycle_default.py")
    config = root / "xdg-config"
    (config / "claude-config").mkdir(parents=True)
    if local is not None:
        target = config / "claude-config" / "local.json"
        if isinstance(local, bytes):
            target.write_bytes(local)
        else:
            target.write_text(local, encoding="utf-8")
    return link, config


@pytest.fixture
def hook():
    return load_hook("lifecycle_default")


def run(hook, link, config, monkeypatch, capsys, event="SessionStart", **extra):
    """Run main() in-process as if Claude Code had started the deployed hook at `link`."""
    monkeypatch.setattr(hook, "HOOK", link)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(config))
    return run_main(hook, payload(hook_event_name=event, **extra), monkeypatch, capsys)


# The host profile's default


@pytest.mark.parametrize("event", ["SessionStart", "SubagentStart"])
def test_a_session_or_subagent_learns_the_host_profile_default(
    hook, tmp_path, monkeypatch, capsys, event
):
    link, config = make_deployment(tmp_path)
    out, err = run(hook, link, config, monkeypatch, capsys, event=event)
    assert (out, err) == (expected(event, HOST_LINE), "")


def test_the_event_name_printed_is_the_hooks_own_not_the_payloads(
    hook, tmp_path, monkeypatch, capsys
):
    link, config = make_deployment(tmp_path)
    monkeypatch.setattr(hook, "HOOK", link)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(config))
    out, _ = run_main(
        hook, b'{"hook_event_name": "SessionStart", "source": "' + SECRET.encode() + b'"}',
        monkeypatch, capsys,
    )  # fmt: skip
    assert out == expected("SessionStart", HOST_LINE)
    assert SECRET not in out


def test_a_resumed_or_compacted_session_hears_the_default_again(
    hook, tmp_path, monkeypatch, capsys
):
    link, config = make_deployment(tmp_path)
    out, _ = run(hook, link, config, monkeypatch, capsys, source="compact")
    assert out == expected("SessionStart", HOST_LINE)


# The machine-local override


def test_the_local_override_wins_and_names_its_file_under_xdg(hook, tmp_path, monkeypatch, capsys):
    link, config = make_deployment(
        tmp_path, local=json.dumps({"defaultLifecycle": "jira-ticket-lifecycle", "jira": {}})
    )
    out, err = run(hook, link, config, monkeypatch, capsys)
    assert (out, err) == (
        expected("SessionStart", local_line("jira-ticket-lifecycle", XDG_LABEL)),
        "",
    )


def test_the_local_override_under_the_home_config_dir_names_it_without_the_home_path(
    hook, tmp_path, monkeypatch, capsys
):
    link, _ = make_deployment(tmp_path)
    fake_home = tmp_path / "home"
    (fake_home / ".config" / "claude-config").mkdir(parents=True)
    (fake_home / ".config" / "claude-config" / "local.json").write_text(
        '{"defaultLifecycle": "jira-ticket-lifecycle"}', encoding="utf-8"
    )
    monkeypatch.setattr(hook, "HOOK", link)
    monkeypatch.setenv("HOME", str(fake_home))
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    out, _ = run_main(hook, payload(hook_event_name="SessionStart"), monkeypatch, capsys)
    assert out == expected("SessionStart", local_line("jira-ticket-lifecycle", HOME_LABEL))
    assert str(fake_home) not in out


@pytest.mark.parametrize("xdg", ["", "relative/dir"])
def test_an_empty_or_relative_xdg_config_home_is_ignored(hook, tmp_path, monkeypatch, capsys, xdg):
    link, config = make_deployment(tmp_path, local='{"defaultLifecycle": "jira-ticket-lifecycle"}')
    monkeypatch.setattr(hook, "HOOK", link)
    monkeypatch.setenv("HOME", str(tmp_path / "empty-home"))
    monkeypatch.setenv("XDG_CONFIG_HOME", xdg)
    out, _ = run_main(hook, payload(hook_event_name="SessionStart"), monkeypatch, capsys)
    assert out == expected("SessionStart", HOST_LINE)


def test_a_local_file_without_the_key_is_silent_and_the_host_default_stands(
    hook, tmp_path, monkeypatch, capsys
):
    link, config = make_deployment(tmp_path, local=json.dumps({"jira": {"project": SECRET}}))
    out, _ = run(hook, link, config, monkeypatch, capsys)
    assert out == expected("SessionStart", HOST_LINE)


@pytest.mark.parametrize(
    ("local", "reason"),
    [
        (b"{" + SECRET.encode(), "it isn't valid JSON"),
        (b"\xff\xfe" + SECRET.encode(), "it isn't valid JSON"),
        (json.dumps([SECRET]).encode(), "it isn't a JSON object"),
        (json.dumps({"defaultLifecycle": f"../{SECRET}"}).encode(),
         "its defaultLifecycle isn't a skill name"),
        (json.dumps({"defaultLifecycle": "jira\n"}).encode(),
         "its defaultLifecycle isn't a skill name"),
        (json.dumps({"defaultLifecycle": "a" * 129}).encode(),
         "its defaultLifecycle isn't a skill name"),
        (json.dumps({"defaultLifecycle": 7}).encode(), "its defaultLifecycle isn't a skill name"),
        (json.dumps({"defaultLifecycle": "x", "pad": "y" * 65536}).encode(),
         "it's larger than 64 KiB"),
    ],
)  # fmt: skip
def test_an_unusable_local_file_is_named_with_a_fixed_reason_and_never_quoted(
    hook, tmp_path, monkeypatch, capsys, local, reason
):
    link, config = make_deployment(tmp_path, local=local)
    out, err = run(hook, link, config, monkeypatch, capsys)
    line = f"{HOST_LINE} `{XDG_LABEL}` was ignored: {reason}."
    assert (out, err) == (expected("SessionStart", line), "")
    assert SECRET not in out


def test_a_local_file_that_is_a_directory_couldnt_be_read(hook, tmp_path, monkeypatch, capsys):
    link, config = make_deployment(tmp_path)
    (config / "claude-config" / "local.json").mkdir()
    out, _ = run(hook, link, config, monkeypatch, capsys)
    line = f"{HOST_LINE} `{XDG_LABEL}` was ignored: it couldn't be read."
    assert out == expected("SessionStart", line)


def test_an_unreadable_local_file_couldnt_be_read(hook, tmp_path, monkeypatch, capsys):
    link, config = make_deployment(tmp_path, local='{"defaultLifecycle": "jira"}')
    local = config / "claude-config" / "local.json"
    local.chmod(0)
    try:
        if os.access(local, os.R_OK):  # pragma: no cover - running as root
            pytest.skip("root can read any file")
        out, _ = run(hook, link, config, monkeypatch, capsys)
    finally:
        local.chmod(0o600)
    line = f"{HOST_LINE} `{XDG_LABEL}` was ignored: it couldn't be read."
    assert out == expected("SessionStart", line)


def test_a_valid_local_default_is_printed_even_outside_a_deployment(
    hook, tmp_path, monkeypatch, capsys
):
    _, config = make_deployment(tmp_path, local='{"defaultLifecycle": "jira-ticket-lifecycle"}')
    plain = tmp_path / "plain-copy.py"
    plain.write_text("", encoding="utf-8")
    out, _ = run(hook, plain, config, monkeypatch, capsys)
    assert out == expected("SessionStart", local_line("jira-ticket-lifecycle", XDG_LABEL))


# Inputs that print nothing


@pytest.mark.parametrize(
    "stdin",
    [
        b"",
        b"not json",
        b"\xff\xfe",
        b"[]",
        b'"SessionStart"',
        payload(hook_event_name="PreToolUse"),
        payload(hook_event_name="SessionEnd"),
        payload(hook_event_name=["SessionStart"]),
        payload(tool_name="Bash"),
    ],
)
def test_other_events_and_malformed_input_print_nothing(hook, tmp_path, monkeypatch, capsys, stdin):
    link, config = make_deployment(tmp_path)
    monkeypatch.setattr(hook, "HOOK", link)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(config))
    assert run_main(hook, stdin, monkeypatch, capsys) == ("", "")


@pytest.mark.parametrize(
    "host",
    [
        "../x.json",
        "/etc/x.json",
        "hosts/X.json",
        "hosts/../../state.json",
        "hosts/personal.json\n",
        "hosts/personal.yaml",
        "personal.json",
        7,
        None,
    ],
)
def test_a_host_value_that_isnt_a_profile_path_prints_nothing(
    hook, tmp_path, monkeypatch, capsys, host
):
    link, config = make_deployment(tmp_path, host=host)
    assert run(hook, link, config, monkeypatch, capsys) == ("", "")


@pytest.mark.parametrize(
    "profile",
    [
        json.dumps({"name": "personal"}),
        json.dumps({"defaultLifecycle": f"../../{SECRET}"}),
        json.dumps({"defaultLifecycle": ""}),
        json.dumps(["github-issue-lifecycle"]),
        "{",
        json.dumps({"defaultLifecycle": "x", "pad": "y" * 65536}),
    ],
)
def test_a_profile_without_a_usable_default_prints_nothing(
    hook, tmp_path, monkeypatch, capsys, profile
):
    link, config = make_deployment(tmp_path, profile=profile)
    assert run(hook, link, config, monkeypatch, capsys) == ("", "")


def test_a_missing_profile_prints_nothing(hook, tmp_path, monkeypatch, capsys):
    link, config = make_deployment(tmp_path, host="hosts/renamed.json")
    assert run(hook, link, config, monkeypatch, capsys) == ("", "")


@pytest.mark.parametrize(
    "state",
    [
        "{",
        json.dumps(["hosts/personal.json"]),
        json.dumps({"host": "hosts/personal.json", "pad": "y" * (1024 * 1024)}),
    ],
)
def test_an_unusable_state_file_prints_nothing(hook, tmp_path, monkeypatch, capsys, state):
    link, config = make_deployment(tmp_path, state=state)
    assert run(hook, link, config, monkeypatch, capsys) == ("", "")


def test_no_state_file_prints_nothing(hook, tmp_path, monkeypatch, capsys):
    link, config = make_deployment(tmp_path)
    (tmp_path / "data" / "state.json").unlink()
    assert run(hook, link, config, monkeypatch, capsys) == ("", "")


def test_a_hook_path_too_short_to_hold_a_deployment_prints_nothing(
    hook, tmp_path, monkeypatch, capsys
):
    _, config = make_deployment(tmp_path)
    from pathlib import Path

    assert run(hook, Path("/x.py"), config, monkeypatch, capsys) == ("", "")


def test_an_unexpected_failure_prints_nothing(hook, tmp_path, monkeypatch, capsys):
    link, config = make_deployment(tmp_path)
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    monkeypatch.setattr(hook, "HOOK", link)

    def no_home():
        raise RuntimeError("no home directory")

    monkeypatch.setattr(hook.Path, "home", staticmethod(no_home))
    assert run_main(hook, payload(hook_event_name="SessionStart"), monkeypatch, capsys) == ("", "")


# As a real process


def run_deployed(link, config, stdin, timeout=10.0):
    """Run the deployed hook as Claude Code does, with an environment built from scratch."""
    env = {
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "HOME": str(config.parent / "home"),
        "LC_ALL": "C.UTF-8",
        "XDG_CONFIG_HOME": str(config),
    }
    return subprocess.run(
        [sys.executable, "-I", str(link)],
        input=stdin,
        capture_output=True,
        env=env,
        timeout=timeout,
        check=False,
    )


def test_the_deployed_hook_finds_its_release_through_both_links(tmp_path):
    link, config = make_deployment(tmp_path)
    done = run_deployed(link, config, payload(hook_event_name="SubagentStart"))
    assert (done.returncode, done.stdout, done.stderr) == (
        0,
        expected("SubagentStart", HOST_LINE).encode(),
        b"",
    )


@pytest.mark.parametrize("stdin", [b"", b"\xff", b"{", payload(hook_event_name="Stop")])
def test_the_process_exits_0_silently_on_bad_input(tmp_path, stdin):
    link, config = make_deployment(tmp_path)
    done = run_deployed(link, config, stdin)
    assert (done.returncode, done.stdout, done.stderr) == (0, b"", b"")


OPEN_RECORDER = """
import json, runpy, sys
opened, recording = [], True
def listen(event, args):
    if recording and event == "open" and isinstance(args[0], str):
        opened.append(args[0])
sys.addaudithook(listen)
try:
    runpy.run_path(sys.argv[1], run_name="__main__")
except SystemExit:
    pass
recording = False
sys.stderr.write(json.dumps(opened))
"""


def opened_files(link, config, root):
    env = {
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "HOME": str(root / "home"),
        "XDG_CONFIG_HOME": str(config),
    }
    done = subprocess.run(
        [sys.executable, "-I", "-c", OPEN_RECORDER, str(link)],
        input=payload(hook_event_name="SessionStart"),
        capture_output=True,
        env=env,
        timeout=10,
        check=False,
    )
    # Module imports are opens too; only files under the test's own directory count.
    inside = {os.path.realpath(p) for p in json.loads(done.stderr) if p.startswith(str(root))}
    return inside - {os.path.realpath(link)}, done.stdout


@pytest.mark.parametrize(
    ("local", "reads_deployment"),
    [
        (None, True),
        ("{", True),
        ('{"jira": 1}', True),
        ('{"defaultLifecycle": "jira-ticket-lifecycle"}', False),
    ],
)
def test_the_hook_opens_only_the_local_file_the_state_file_and_the_profile(
    tmp_path, local, reads_deployment
):
    link, config = make_deployment(tmp_path, local=local)
    opened, stdout = opened_files(link, config, tmp_path)
    want = {os.path.realpath(config / "claude-config" / "local.json")}
    if reads_deployment:
        want |= {
            os.path.realpath(tmp_path / "data" / "state.json"),
            os.path.realpath(tmp_path / "data" / "releases" / "r" / "hosts" / "personal.json"),
        }
    assert opened == want
    assert stdout  # the run really produced its line, so every read happened


@pytest.mark.parametrize(
    "which",
    ["local", "state", "profile"],
)
def test_a_fifo_in_place_of_any_file_never_blocks(tmp_path, which):
    link, config = make_deployment(tmp_path, local="{}")
    path = {
        "local": config / "claude-config" / "local.json",
        "state": tmp_path / "data" / "state.json",
        "profile": tmp_path / "data" / "releases" / "r" / "hosts" / "personal.json",
    }[which]
    path.unlink()
    os.mkfifo(path)
    # A blocking open would hang until the timeout; the hook must finish at once.
    done = run_deployed(link, config, payload(hook_event_name="SessionStart"), timeout=5)
    assert done.returncode == 0 and done.stderr == b""
    if which == "local":
        line = f"{HOST_LINE} `{XDG_LABEL}` was ignored: it couldn't be read."
        assert done.stdout == expected("SessionStart", line).encode()
    else:
        assert done.stdout == b""


def test_one_run_is_fast(hook, tmp_path, monkeypatch, capsys):
    link, config = make_deployment(tmp_path, local='{"defaultLifecycle": "jira-ticket-lifecycle"}')
    timings = []
    for _ in range(20):
        start = time.perf_counter()
        run(hook, link, config, monkeypatch, capsys)
        timings.append(time.perf_counter() - start)
    assert statistics.median(timings) < 0.05
