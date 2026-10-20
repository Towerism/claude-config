"""The work machine's profile deploys what the personal one does, with Jira as its default.

ADR-0011 gives both machines the same skills, hooks, plugins and overlays; they
differ only in `defaultLifecycle`. A component added to one profile and not the
other would land on one machine only, so these tests compare the two files.
"""

import json
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from tests.checker_helpers import REPO_ROOT, run_script
from tools.cfg import render

HOSTS = REPO_ROOT / "hosts"
CHECKER = REPO_ROOT / "tools" / "check_config.py"
HOOK = REPO_ROOT / "config" / "hooks" / "lifecycle_default.py"
# Keys that may differ between the two machines' profiles.
PER_MACHINE = {"name", "defaultLifecycle"}


def load(name: str) -> dict[str, Any]:
    return json.loads((HOSTS / f"{name}.json").read_text())


def shared_differences(a: dict[str, Any], b: dict[str, Any]) -> set[str]:
    """Keys outside PER_MACHINE whose values differ, or that only one profile has."""
    return {k for k in a.keys() | b.keys() if k not in PER_MACHINE and a.get(k) != b.get(k)}


def test_the_work_profile_differs_from_the_personal_one_only_in_name_and_default():
    personal, work = load("personal"), load("work")
    assert shared_differences(personal, work) == set()
    assert work["name"] == "work"
    assert work["defaultLifecycle"] == "jira-ticket-lifecycle"
    assert personal["defaultLifecycle"] == "github-issue-lifecycle"


@pytest.mark.parametrize(
    ("change", "key"),
    [
        (lambda p: p["skills"].append("only-on-work"), "skills"),
        (lambda p: p["hooks"].pop(), "hooks"),
        (lambda p: p["plugins"].reverse(), "plugins"),
        (lambda p: p.update(overlays=["one-overlay.md"]), "overlays"),
        (lambda p: p.pop("claudeMd"), "claudeMd"),
        (lambda p: p.update(extra=True), "extra"),
    ],
    ids=["skills", "hooks", "plugins", "overlays", "claudeMd", "extra"],
)
def test_the_comparison_catches_a_component_on_one_machine_only(change, key):
    work = load("work")
    change(work)
    assert shared_differences(load("personal"), work) == {key}


def deployment(tmp_path: Path, host: str) -> Path:
    """A data directory laid out as `cfg deploy` leaves it, holding the repo's real profile.

    Returns the path of the hook inside the `current` release.
    """
    data = tmp_path / "data"
    release = data / "releases" / "r"
    (release / "config" / "hooks").mkdir(parents=True)
    (release / "config" / "hooks" / HOOK.name).write_bytes(HOOK.read_bytes())
    (release / "hosts").mkdir()
    (release / host).write_bytes((REPO_ROOT / host).read_bytes())
    (data / "current").symlink_to("releases/r")
    (data / "state.json").write_text(json.dumps({"host": host}))
    return data / "current" / "config" / "hooks" / HOOK.name


@pytest.mark.parametrize("event", ["SessionStart", "SubagentStart"])
def test_a_work_machine_session_is_told_jira_is_its_default(tmp_path, event):
    hook = deployment(tmp_path, "hosts/work.json")
    xdg = tmp_path / "xdg"
    xdg.mkdir()
    done = subprocess.run(
        [sys.executable, "-I", str(hook)],
        input=json.dumps({"hook_event_name": event}),
        capture_output=True,
        text=True,
        env={"PATH": "/usr/bin:/bin", "XDG_CONFIG_HOME": str(xdg)},
        timeout=30,
        check=True,
    )
    out = json.loads(done.stdout)["hookSpecificOutput"]
    assert out["hookEventName"] == event
    assert out["additionalContext"].startswith(
        "Default lifecycle skill on this machine: `jira-ticket-lifecycle` "
        "(set in `hosts/work.json`)."
    ), out["additionalContext"]


def test_the_work_profile_deploys_the_same_paths_and_hook_commands(tmp_path):
    home, data = tmp_path / "home" / ".claude", tmp_path / "data"
    personal = render.render(REPO_ROOT, "hosts/personal.json", home, data)
    work = render.render(REPO_ROOT, "hosts/work.json", home, data)
    assert work.paths == personal.paths
    assert work.fragment == personal.fragment
    assert work.host == "hosts/work.json"


def test_the_checker_accepts_the_work_profile_offline(tmp_path):
    home = tmp_path / ".claude"
    (home / "plugins").mkdir(parents=True)
    args = [
        "--host", str(HOSTS / "work.json"),
        "--plugin-index", str(REPO_ROOT / "plugins.lock.json"),
        "--claude-home", str(home),
        "--path-rule", "fail",
        "--json",
    ]  # fmt: skip
    r = run_script(CHECKER, "", home, args)
    assert r.returncode == 0, r.stdout + r.stderr
