"""cfg deploy and cfg rollback against temp repos and temp Claude homes."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from tests.cfg_helpers import (
    CHECKER_OK,
    PROFILE,
    commit_change,
    git,
    inventory,
    make_deploy_repo,
    make_home,
    make_live_install,
    push,
    write_tree,
)
from tools.cfg import deploy, drift, release, state
from tools.cfg.errors import CfgError, LockBusy

pytestmark = pytest.mark.usefixtures("cfg_isolation")

HOST = "hosts/test.json"
REPO_ROOT = Path(__file__).resolve().parent.parent


def clock() -> datetime:
    return datetime(2026, 10, 19, 12, 0, 0, tzinfo=UTC)


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    return make_deploy_repo(tmp_path / "clone")


@pytest.fixture
def home(tmp_path: Path) -> Path:
    return make_home(tmp_path / "home")


@pytest.fixture
def data(tmp_path: Path) -> Path:
    return tmp_path / "data"


@pytest.fixture
def installed(repo: Path, home: Path) -> Path:
    make_live_install(home, repo)
    return home


def run(repo: Path, home: Path, data: Path, **kwargs: Any) -> deploy.DeployResult:
    kwargs.setdefault("yes", True)
    return deploy.deploy(repo, home, data, HOST, clock=clock, **kwargs)


def settings(home: Path) -> dict[str, Any]:
    loaded: dict[str, Any] = json.loads((home / "settings.json").read_text())
    return loaded


def snapshot_bytes(root: Path) -> dict[str, bytes | str]:
    out: dict[str, bytes | str] = {}
    for path in sorted(root.rglob("*")):
        rel = str(path.relative_to(root))
        if rel.startswith("backups"):
            continue
        if path.is_symlink():
            out[rel] = "link:" + os.readlink(path)
        elif path.is_file():
            out[rel] = path.read_bytes()
        else:
            out[rel] = "dir"
    return out


# ---- the first deploy -------------------------------------------------------------


def test_a_first_deploy_links_everything_and_backs_up_the_originals(
    repo: Path, installed: Path, data: Path
) -> None:
    home = installed
    before = snapshot_bytes(home)
    result = run(repo, home, data)

    assert result.result == "deployed"
    managed = ["CLAUDE.md", "skill-overlays", "hooks/overlay.py", "hooks/guard.py"]
    managed += ["skills/alpha", "skills/beta"]
    for live in managed:
        assert (home / live).is_symlink()
        assert os.readlink(home / live).startswith(f"{data}/current/")
    assert sorted(result.matched) == sorted(managed)
    backup = Path(result.backup or "")
    assert backup.parent == home / "backups" / "claude-config"
    assert (backup / "CLAUDE.md").read_bytes() == before["CLAUDE.md"]
    entries = state.read_manifest(backup / "manifest.sha256")
    assert {e.path for e in entries} >= {"CLAUDE.md", "skills/alpha/scripts/run.py"}
    assert (home / "skills" / "other" / "SKILL.md").read_text() == "mine\n"
    assert (home / "hooks" / "extra.py").read_text() == "pass\n"
    assert settings(home)["theme"] == "dark"
    st = state.load(data)
    assert st is not None and st.release == result.release and st.history == (result.release,)
    assert drift.status(repo, data, home).result == "clean"


def test_settings_already_matching_are_not_rewritten(
    repo: Path, installed: Path, data: Path
) -> None:
    before = (installed / "settings.json").stat().st_mtime_ns
    result = run(repo, installed, data)
    assert result.settings_changes == []
    assert (installed / "settings.json").stat().st_mtime_ns == before
    assert not (Path(result.backup or "") / "settings.json").exists()


def test_a_first_deploy_into_an_empty_home_creates_links_and_settings(
    repo: Path, home: Path, data: Path
) -> None:
    result = run(repo, home, data)
    assert result.result == "deployed"
    assert (home / "CLAUDE.md").is_symlink()
    assert settings(home)["enabledPlugins"] == {"tools@market": True}
    assert result.settings_changes


@pytest.mark.parametrize(
    "change",
    ["claude-md", "skill-extra-file", "plugin-off"],
)
def test_the_first_deploy_stops_on_each_kind_of_conflict_and_changes_nothing(
    repo: Path, installed: Path, data: Path, change: str
) -> None:
    home = installed
    if change == "claude-md":
        (home / "CLAUDE.md").write_text("# edited live\n")
    elif change == "skill-extra-file":
        (home / "skills" / "alpha" / "notes.md").write_text("mine\n")
    else:
        s = settings(home)
        s["enabledPlugins"]["tools@market"] = False
        (home / "settings.json").write_text(json.dumps(s, indent=2) + "\n")
    before = inventory(home, data, repo / ".git", skip=("cfg.lock",))

    result = run(repo, home, data)

    assert result.result == "stopped"
    assert len(result.conflicts) == 1
    assert result.conflicts[0].diff
    after = inventory(home, data, repo / ".git", skip=("cfg.lock",))
    assert [r for r in after if r[0] != str(data)] == [r for r in before if r[0] != str(data)]


def test_discard_live_waives_exactly_the_named_conflict(
    repo: Path, installed: Path, data: Path
) -> None:
    home = installed
    (home / "CLAUDE.md").write_text("# edited live\n")
    (home / "skills" / "beta" / "extra.md").write_text("x\n")

    first = run(repo, home, data, discard=("CLAUDE.md",))
    assert first.result == "stopped"
    assert [c.path for c in first.conflicts] == ["skills/beta"]

    both = run(repo, home, data, discard=("CLAUDE.md", "skills/beta"))
    assert both.result == "deployed"
    assert sorted(both.discarded) == ["CLAUDE.md", "skills/beta"]
    backup = Path(both.backup or "")
    assert (backup / "CLAUDE.md").read_text() == "# edited live\n"
    assert (backup / "skills" / "beta" / "extra.md").read_text() == "x\n"


def test_discarding_a_path_with_no_conflict_is_a_usage_error(
    repo: Path, installed: Path, data: Path
) -> None:
    with pytest.raises(CfgError, match="no conflict there"):
        run(repo, installed, data, discard=("hooks/guard.py",))


def test_discarding_settings_lets_the_repo_values_win(
    repo: Path, installed: Path, data: Path
) -> None:
    s = settings(installed)
    s["enabledPlugins"]["tools@market"] = False
    (installed / "settings.json").write_text(json.dumps(s, indent=2) + "\n")
    result = run(repo, installed, data, discard=("settings.json",))
    assert result.result == "deployed"
    assert settings(installed)["enabledPlugins"]["tools@market"] is True
    saved = json.loads((Path(result.backup or "") / "settings.json").read_text())
    assert saved["enabledPlugins"]["tools@market"] is False


def test_without_yes_or_a_yes_answer_nothing_changes(
    repo: Path, installed: Path, data: Path
) -> None:
    before = snapshot_bytes(installed)
    asked: list[list[str]] = []

    def no(lines: list[str]) -> bool:
        asked.append(lines)
        return False

    assert run(repo, installed, data, yes=False).result == "needs-confirmation"
    assert run(repo, installed, data, yes=False, confirm=no).result == "needs-confirmation"
    assert any("back up and link CLAUDE.md (match)" in line for line in asked[0])
    assert snapshot_bytes(installed) == before
    assert run(repo, installed, data, yes=False, confirm=lambda lines: True).result == "deployed"


def test_a_failing_checker_stops_before_any_live_change(
    repo: Path, installed: Path, data: Path
) -> None:
    commit_change(repo, "tools/check_config.py", "import sys\nprint('bad');sys.exit(1)\n")
    push(repo)
    before = snapshot_bytes(installed)
    with pytest.raises(CfgError, match="config checker failed"):
        run(repo, installed, data)
    assert snapshot_bytes(installed) == before


@pytest.mark.parametrize(
    "hook",
    [
        "import sys\nsys.stdin.read()\nprint('oops', file=sys.stderr)\n",
        "import sys\nsys.stdin.read()\nprint('not json')\n",
        "import sys\nsys.stdin.read()\nprint('[1]')\n",
        "import sys\nsys.exit(3)\n",
    ],
    ids=["stderr", "not-json", "not-object", "exit-code"],
)
def test_a_broken_hook_stops_before_any_live_change(
    repo: Path, installed: Path, data: Path, hook: str
) -> None:
    commit_change(repo, "config/hooks/guard.py", hook)
    push(repo)
    shutil.copyfile(repo / "config" / "hooks" / "guard.py", installed / "hooks" / "guard.py")
    before = snapshot_bytes(installed)
    with pytest.raises(CfgError, match="smoke test failed before"):
        run(repo, installed, data)
    assert snapshot_bytes(installed) == before


def test_a_hook_that_hangs_fails_the_smoke_test(repo: Path, installed: Path, data: Path) -> None:
    commit_change(repo, "config/hooks/guard.py", "import time\ntime.sleep(5)\n")
    push(repo)
    shutil.copyfile(repo / "config" / "hooks" / "guard.py", installed / "hooks" / "guard.py")
    with pytest.raises(CfgError, match="took longer"):
        run(repo, installed, data, smoke_timeout=0.2)


# ---- the repo's own hooks in the smoke test ---------------------------------------


def real_hooks_repo(root: Path, home: Path) -> Path:
    """A repo whose hooks are this repo's real merge_confirm.py and skill_overlay.py."""
    repo = make_deploy_repo(root)
    for name in ("merge_confirm.py", "skill_overlay.py"):
        shutil.copyfile(REPO_ROOT / "config" / "hooks" / name, repo / "config" / "hooks" / name)
    profile = {**PROFILE, "hooks": ["merge_confirm.py", "skill_overlay.py"]}
    hook = "python3 -I {claude_home}/hooks/"
    fragment = {
        "hooks": {
            "PreToolUse": [
                {
                    "matcher": "Bash",
                    "hooks": [{"type": "command", "command": hook + "merge_confirm.py"}],
                }
            ],
            "PostToolUse": [
                {
                    "matcher": "Skill",
                    "hooks": [{"type": "command", "command": hook + "skill_overlay.py"}],
                }
            ],
            "UserPromptExpansion": [
                {"hooks": [{"type": "command", "command": hook + "skill_overlay.py"}]}
            ],
            "Stop": [{"hooks": [{"type": "command", "command": hook + "merge_confirm.py"}]}],
        }
    }
    write_tree(
        repo,
        {
            "hosts/test.json": json.dumps(profile) + "\n",
            "config/settings.fragment.json": json.dumps(fragment) + "\n",
            "config/skill-overlays/alpha.md": "Use `scripts/run.py`.\n",
        },
    )
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "real hooks")
    push(repo)
    return repo


def test_the_repos_own_hooks_pass_the_smoke_test(tmp_path: Path, home: Path, data: Path) -> None:
    repo = real_hooks_repo(tmp_path / "real", home)
    result = deploy.deploy(repo, home, data, HOST, yes=True, clock=clock)
    assert result.result == "deployed"


def test_a_merge_hook_that_no_longer_asks_fails_the_smoke_test(
    tmp_path: Path, home: Path, data: Path
) -> None:
    repo = real_hooks_repo(tmp_path / "real", home)
    commit_change(repo, "config/hooks/merge_confirm.py", "import sys\nsys.stdin.read()\n")
    push(repo)
    with pytest.raises(CfgError, match="didn't ask"):
        deploy.deploy(repo, home, data, HOST, yes=True, clock=clock)


def test_an_overlay_hook_that_loads_nothing_fails_the_smoke_test(
    tmp_path: Path, home: Path, data: Path
) -> None:
    repo = real_hooks_repo(tmp_path / "real", home)
    git(repo, "rm", "-q", "-r", "config/skills/alpha")
    write_tree(
        repo,
        {
            "hosts/test.json": json.dumps(
                {**PROFILE, "skills": ["beta"], "hooks": ["merge_confirm.py", "skill_overlay.py"]}
            )
            + "\n"
        },
    )
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "drop alpha")
    push(repo)
    with pytest.raises(CfgError, match="skill_overlay.py: wrote to stderr"):
        deploy.deploy(repo, home, data, HOST, yes=True, clock=clock)


def test_overlay_output_saying_it_could_not_load_is_a_failure() -> None:
    out = {"hookSpecificOutput": {"additionalContext": "x could not be loaded (y)"}}
    assert deploy._expect_context(out) == "couldn't load the overlay"


def test_an_overlay_hook_that_prints_nothing_fails_the_smoke_test(
    tmp_path: Path, home: Path, data: Path
) -> None:
    repo = real_hooks_repo(tmp_path / "real", home)
    commit_change(repo, "config/hooks/skill_overlay.py", "import sys\nsys.stdin.read()\n")
    push(repo)
    with pytest.raises(CfgError, match="printed no overlay"):
        deploy.deploy(repo, home, data, HOST, yes=True, clock=clock)


def test_a_merge_hook_that_talks_about_harmless_commands_fails(
    tmp_path: Path, home: Path, data: Path
) -> None:
    repo = real_hooks_repo(tmp_path / "real", home)
    noisy = (
        "import json, sys\nsys.stdin.read()\n"
        "print(json.dumps({'hookSpecificOutput': {'permissionDecision': 'ask'}}))\n"
    )
    commit_change(repo, "config/hooks/merge_confirm.py", noisy)
    push(repo)
    with pytest.raises(CfgError, match="harmless command"):
        deploy.deploy(repo, home, data, HOST, yes=True, clock=clock)


def test_a_hook_command_that_cannot_start_fails_the_smoke_test(
    repo: Path, home: Path, data: Path
) -> None:
    fragment = {
        "hooks": {"Stop": [{"hooks": [{"type": "command", "command": "/no/such/program"}]}]}
    }
    write_tree(repo, {"config/settings.fragment.json": json.dumps(fragment) + "\n"})
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "bad command")
    push(repo)
    with pytest.raises(CfgError, match="couldn't run"):
        run(repo, home, data)


def test_smoke_cases_cover_each_known_handler() -> None:
    assert len(deploy.smoke_cases("PreToolUse", "Bash", "merge_confirm.py", "x")) == 2
    assert deploy.smoke_cases("PostToolUse", "Skill", "skill_overlay.py", None) == [({}, None)]
    assert deploy.smoke_cases("Stop", "", "other.py", "x") == [({}, None)]


# ---- ordinary deploys -------------------------------------------------------------


def test_an_ordinary_deploy_switches_current_and_keeps_the_links(
    repo: Path, installed: Path, data: Path
) -> None:
    first = run(repo, installed, data)
    links = {p: os.readlink(installed / p) for p in ("CLAUDE.md", "skills/alpha")}
    commit_change(repo, "config/skill-overlays/alpha.md", "overlay v2\n")
    fragment = json.loads((repo / "config" / "settings.fragment.json").read_text())
    fragment["enabledPlugins"]["new@market"] = True
    commit_change(repo, "config/settings.fragment.json", json.dumps(fragment, indent=2) + "\n")
    push(repo)

    second = run(repo, installed, data)

    assert second.result == "deployed"
    assert release.current_release(data) == second.release != first.release
    assert {p: os.readlink(installed / p) for p in links} == links
    assert (installed / "skill-overlays" / "alpha.md").read_text() == "overlay v2\n"
    assert settings(installed)["enabledPlugins"]["new@market"] is True
    backup = Path(second.backup or "")
    assert sorted(p.name for p in backup.iterdir()) == [
        "manifest.sha256",
        "restore.json",
        "restore.py",
        "restore.sh",
        "settings.json",
    ]
    st = state.load(data)
    assert st is not None and st.history == (first.release, second.release)


def test_deploying_the_same_commit_again_does_nothing(
    repo: Path, installed: Path, data: Path
) -> None:
    run(repo, installed, data)
    again = run(repo, installed, data, yes=False)
    assert again.result == "already-deployed"
    assert again.backup is None


def test_old_clean_releases_are_pruned_to_five(repo: Path, installed: Path, data: Path) -> None:
    names = [run(repo, installed, data).release]
    for n in range(6):
        commit_change(repo, "config/skill-overlays/alpha.md", f"v{n}\n")
        push(repo)
        names.append(run(repo, installed, data).release)
    left = sorted(p.name for p in release.releases_dir(data).iterdir())
    assert left == sorted(names[-5:])
    st = state.load(data)
    assert st is not None and list(st.history) == names[-5:]


def test_pruning_keeps_releases_with_edits_and_reports_one_it_cannot_remove(
    repo: Path, installed: Path, data: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    first = run(repo, installed, data).release
    commit_change(repo, "config/skill-overlays/alpha.md", "v1\n")
    push(repo)
    second = run(repo, installed, data).release
    history = [first, second, "gone-release"]
    kept, warnings = deploy.prune(repo, data, history, second)
    assert kept == history and warnings == []

    def refuse(clone: Path, data_dir: Path, name: str) -> None:
        raise CfgError("busy")

    (release.releases_dir(data) / first / "config" / "CLAUDE.global.md").write_text("edit\n")
    monkeypatch.setattr(deploy, "KEEP_RELEASES", 1)
    kept, warnings = deploy.prune(repo, data, history, second)
    assert kept == history
    (release.releases_dir(data) / first / "config" / "CLAUDE.global.md").write_text("# global\n")
    monkeypatch.setattr(release, "remove", refuse)
    kept, warnings = deploy.prune(repo, data, history, second)
    assert kept == history and "couldn't prune" in warnings[0]


def test_drift_in_the_release_stops_until_discarded(
    repo: Path, installed: Path, data: Path
) -> None:
    first = run(repo, installed, data)
    (installed / "skills" / "alpha" / "SKILL.md").write_text("edited through the link\n")

    stopped = run(repo, installed, data)
    assert stopped.result == "stopped"
    assert stopped.conflicts[0].kind == "drift"
    assert stopped.conflicts[0].path == "skills/alpha"

    done = run(repo, installed, data, discard=("skills/alpha",))
    assert done.release == f"{first.release}-2"
    edited = release.releases_dir(data) / first.release / "config" / "skills" / "alpha"
    assert (edited / "SKILL.md").read_text() == "edited through the link\n"
    assert (installed / "skills" / "alpha" / "SKILL.md").read_text() == "---\nname: alpha\n---\n"


def test_drift_that_has_merged_is_resolved(repo: Path, installed: Path, data: Path) -> None:
    run(repo, installed, data)
    (installed / "skills" / "alpha" / "SKILL.md").write_text("captured\n")
    commit_change(repo, "config/skills/alpha/SKILL.md", "captured\n")
    push(repo)
    result = run(repo, installed, data)
    assert result.result == "deployed"


def test_drift_outside_the_managed_paths_is_only_a_warning(
    repo: Path, installed: Path, data: Path
) -> None:
    first = run(repo, installed, data)
    (release.releases_dir(data) / first.release / "tools" / "stray.txt").write_text("x\n")
    commit_change(repo, "config/skill-overlays/alpha.md", "v2\n")
    push(repo)
    result = run(repo, installed, data)
    assert result.result == "deployed"
    assert any("tools/stray.txt" in w for w in result.warnings)


def test_a_path_that_left_the_profile_loses_only_cfgs_link(
    repo: Path, installed: Path, data: Path
) -> None:
    run(repo, installed, data)
    profile = {**PROFILE, "skills": ["alpha"]}
    commit_change(repo, HOST, json.dumps(profile, indent=2) + "\n")
    push(repo)
    result = run(repo, installed, data)
    assert result.removed == ["skills/beta"]
    assert not os.path.lexists(installed / "skills" / "beta")


def test_a_path_that_left_the_profile_but_is_not_cfgs_link_is_left_alone(
    repo: Path, installed: Path, data: Path
) -> None:
    run(repo, installed, data)
    (installed / "skills" / "beta").unlink()
    (installed / "skills" / "beta").mkdir()
    commit_change(repo, HOST, json.dumps({**PROFILE, "skills": ["alpha"]}, indent=2) + "\n")
    push(repo)
    result = run(repo, installed, data)
    assert result.removed == []
    assert (installed / "skills" / "beta").is_dir()
    assert any("left the profile" in w for w in result.warnings)


def test_a_foreign_link_is_backed_up_with_its_text_and_relinked(
    repo: Path, installed: Path, data: Path
) -> None:
    run(repo, installed, data)
    (installed / "CLAUDE.md").unlink()
    os.symlink("/somewhere/old", installed / "CLAUDE.md")
    result = run(repo, installed, data)
    assert os.readlink(installed / "CLAUDE.md").startswith(str(data))
    assert os.readlink(Path(result.backup or "") / "CLAUDE.md") == "/somewhere/old"


def test_a_ref_off_origin_main_deploys_with_a_warning(
    repo: Path, installed: Path, data: Path
) -> None:
    run(repo, installed, data)
    side = commit_change(repo, "config/skill-overlays/alpha.md", "side\n")
    result = run(repo, installed, data, ref=side)
    assert result.result == "deployed"
    assert any("isn't on origin/main" in w for w in result.warnings)


def test_a_clone_without_origin_main_needs_a_ref(repo: Path, installed: Path, data: Path) -> None:
    git(repo, "update-ref", "-d", "refs/remotes/origin/main")
    with pytest.raises(CfgError, match="--ref"):
        run(repo, installed, data)
    result = run(repo, installed, data, ref="HEAD")
    assert any("origin/main is missing" in w for w in result.warnings)


def test_an_unknown_ref_is_refused(repo: Path, installed: Path, data: Path) -> None:
    with pytest.raises(CfgError):
        run(repo, installed, data, ref="no-such-ref")


def test_a_state_for_another_home_is_refused(
    repo: Path, installed: Path, data: Path, tmp_path: Path
) -> None:
    run(repo, installed, data)
    with pytest.raises(CfgError, match="deployed to"):
        run(repo, tmp_path / "other-home", data)


def test_a_second_cfg_during_a_deploy_is_refused(repo: Path, installed: Path, data: Path) -> None:
    seen: list[str] = []

    def second_cfg(step: str) -> None:
        if step == "swap":
            with pytest.raises(LockBusy, match="another cfg is running"):
                run(repo, installed, data)
            seen.append(step)

    result = run(repo, installed, data, step=second_cfg)
    assert result.result == "deployed" and seen == ["swap"]


# ---- failures after the first change ----------------------------------------------


@pytest.mark.parametrize("at", ["backup-files", "link", "state"])
def test_a_failure_after_the_first_change_names_restore_sh_which_undoes_it(
    repo: Path, installed: Path, data: Path, at: str
) -> None:
    before = snapshot_bytes(installed)

    def fail(step: str) -> None:
        if step == at:
            raise RuntimeError("injected")

    with pytest.raises(deploy.DeployFailed, match="restore.sh") as caught:
        run(repo, installed, data, step=fail)
    backup = next((installed / "backups" / "claude-config").iterdir())
    assert str(backup) in str(caught.value)
    done = subprocess.run([str(backup / "restore.sh")], capture_output=True, text=True, check=False)
    assert done.returncode == 0, done.stdout + done.stderr
    assert snapshot_bytes(installed) == before


def test_a_later_failure_also_names_rollback(repo: Path, installed: Path, data: Path) -> None:
    run(repo, installed, data)
    fragment = json.loads((repo / "config" / "settings.fragment.json").read_text())
    fragment["enabledPlugins"]["new@market"] = True
    commit_change(repo, "config/settings.fragment.json", json.dumps(fragment, indent=2) + "\n")
    push(repo)

    def fail(step: str) -> None:
        if step == "swap":
            raise RuntimeError("injected")

    with pytest.raises(deploy.DeployFailed, match="cfg rollback"):
        run(repo, installed, data, step=fail)


def test_a_failure_with_no_backup_points_at_rollback(
    repo: Path, installed: Path, data: Path
) -> None:
    run(repo, installed, data)
    commit_change(repo, "config/skill-overlays/alpha.md", "v2\n")
    push(repo)

    def fail(step: str) -> None:
        if step == "swap":
            raise RuntimeError("injected")

    with pytest.raises(deploy.DeployFailed, match="run cfg rollback"):
        run(repo, installed, data, step=fail)


def test_settings_changed_during_the_write_fail_the_deploy(
    repo: Path, home: Path, data: Path
) -> None:
    def harness_saves() -> None:
        (home / "settings.json").write_text('{"harness": true}\n')

    with pytest.raises(deploy.DeployFailed, match="another program"):
        run(repo, home, data, before_replace=harness_saves)
    assert settings(home) == {"harness": True}


def test_a_hook_broken_after_the_switch_fails_with_exit_3(
    repo: Path, installed: Path, data: Path
) -> None:
    def break_hook(step: str) -> None:
        if step == "state":
            (installed / "hooks" / "extra-break").write_text("")
            target = Path(os.readlink(installed / "hooks" / "guard.py"))
            target.write_text("import sys\nsys.exit(4)\n")

    with pytest.raises(deploy.DeployFailed, match="after the switch"):
        run(repo, installed, data, step=break_hook)


# ---- restore against real deploys -------------------------------------------------


def test_restore_sh_puts_a_first_deploy_back_byte_for_byte_without_the_repo(
    repo: Path, installed: Path, data: Path, tmp_path: Path
) -> None:
    before = snapshot_bytes(installed)
    result = run(repo, installed, data)
    s = settings(installed)
    s["theme"] = "light"
    (installed / "settings.json").write_text(json.dumps(s, indent=2) + "\n")
    os.rename(repo, tmp_path / "moved-away")

    done = subprocess.run(
        [str(Path(result.backup or "") / "restore.sh")], capture_output=True, text=True, check=False
    )

    assert done.returncode == 0, done.stdout + done.stderr
    after = snapshot_bytes(installed)
    assert json.loads(after.pop("settings.json")) == {
        **json.loads(before.pop("settings.json")),
        "theme": "light",
    }
    assert after == before
    assert state.load(data) is None


def test_restore_of_an_ordinary_deploy_reverts_only_its_settings(
    repo: Path, installed: Path, data: Path
) -> None:
    run(repo, installed, data)
    fragment = json.loads((repo / "config" / "settings.fragment.json").read_text())
    fragment["enabledPlugins"]["new@market"] = True
    commit_change(repo, "config/settings.fragment.json", json.dumps(fragment, indent=2) + "\n")
    push(repo)
    second = run(repo, installed, data)
    done = subprocess.run(
        [str(Path(second.backup or "") / "restore.sh")], capture_output=True, text=True, check=False
    )
    assert done.returncode == 0, done.stdout + done.stderr
    assert "new@market" not in settings(installed)["enabledPlugins"]
    assert (installed / "CLAUDE.md").is_symlink()


# ---- rollback ---------------------------------------------------------------------


def test_rollback_switches_back_and_fixes_cfgs_links_but_not_settings(
    repo: Path, installed: Path, data: Path
) -> None:
    first = run(repo, installed, data)
    profile = {**PROFILE, "skills": ["alpha", "beta", "gamma"]}
    fragment = json.loads((repo / "config" / "settings.fragment.json").read_text())
    fragment["enabledPlugins"]["new@market"] = True
    write_tree(
        repo,
        {
            HOST: json.dumps(profile, indent=2) + "\n",
            "config/skills/gamma/SKILL.md": "---\nname: gamma\n---\n",
            "config/settings.fragment.json": json.dumps(fragment, indent=2) + "\n",
        },
    )
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "gamma")
    push(repo)
    run(repo, installed, data)
    settings_before = (installed / "settings.json").read_bytes()

    result = deploy.rollback(repo, installed, data)

    assert result.to == first.release
    assert release.current_release(data) == first.release
    assert result.removed == ["skills/gamma"]
    assert not os.path.lexists(installed / "skills" / "gamma")
    assert (installed / "settings.json").read_bytes() == settings_before
    assert (
        result.settings_differ and result.command == f"cfg deploy --host {HOST} --ref {first.sha}"
    )
    st = state.load(data)
    assert st is not None and st.release == first.release


def test_rollback_relinks_a_missing_path_and_leaves_others_alone(
    repo: Path, installed: Path, data: Path
) -> None:
    run(repo, installed, data)
    commit_change(repo, HOST, json.dumps({**PROFILE, "skills": ["alpha"]}, indent=2) + "\n")
    push(repo)
    run(repo, installed, data)
    (installed / "CLAUDE.md").unlink()
    (installed / "CLAUDE.md").write_text("plain\n")
    result = deploy.rollback(repo, installed, data)
    assert result.relinked == ["skills/beta"]
    assert result.left_alone == ["CLAUDE.md"]
    assert not result.settings_differ


def test_rollback_leaves_alone_a_path_that_left_and_is_not_cfgs_link(
    repo: Path, installed: Path, data: Path
) -> None:
    run(repo, installed, data)
    commit_change(repo, HOST, json.dumps({**PROFILE, "skills": ["alpha", "beta", "gamma"]}) + "\n")
    write_tree(repo, {"config/skills/gamma/SKILL.md": "g\n"})
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "gamma")
    push(repo)
    run(repo, installed, data)
    (installed / "skills" / "gamma").unlink()
    (installed / "skills" / "gamma").mkdir()
    result = deploy.rollback(repo, installed, data)
    assert "skills/gamma" in result.left_alone


def test_rollback_reports_settings_left_over_from_the_newer_release(
    repo: Path, installed: Path, data: Path
) -> None:
    run(repo, installed, data)
    fragment = json.loads((repo / "config" / "settings.fragment.json").read_text())
    fragment["enabledPlugins"]["new@market"] = True
    commit_change(repo, "config/settings.fragment.json", json.dumps(fragment, indent=2) + "\n")
    push(repo)
    run(repo, installed, data)
    s = settings(installed)
    s["enabledPlugins"].pop("tools@market")
    (installed / "settings.json").write_text(json.dumps(s, indent=2) + "\n")
    st = state.load(data)
    assert st is not None
    state.save(data, state.State(**{**vars(st), "settings": {"enabledPlugins/new@market": True}}))
    assert deploy.rollback(repo, installed, data).settings_differ


def test_rollback_says_edits_stay_in_the_release_it_leaves(
    repo: Path, installed: Path, data: Path
) -> None:
    run(repo, installed, data)
    commit_change(repo, "config/skill-overlays/alpha.md", "v2\n")
    push(repo)
    run(repo, installed, data)
    (installed / "skill-overlays" / "alpha.md").write_text("edited\n")
    result = deploy.rollback(repo, installed, data)
    assert any("stay there on disk" in w for w in result.warnings)


def test_rollback_needs_an_earlier_release(repo: Path, installed: Path, data: Path) -> None:
    with pytest.raises(CfgError, match="nothing is deployed"):
        deploy.rollback(repo, installed, data)
    run(repo, installed, data)
    with pytest.raises(CfgError, match="no earlier release"):
        deploy.rollback(repo, installed, data)


def test_rollback_skips_a_release_that_is_gone_and_refuses_another_home(
    repo: Path, installed: Path, data: Path, tmp_path: Path
) -> None:
    first = run(repo, installed, data)
    commit_change(repo, "config/skill-overlays/alpha.md", "v2\n")
    push(repo)
    run(repo, installed, data)
    st = state.load(data)
    assert st is not None
    state.save(data, state.State(**{**vars(st), "history": (first.release, "gone", st.release)}))
    assert deploy.rollback(repo, installed, data).to == first.release
    with pytest.raises(CfgError, match="deployed to"):
        deploy.rollback(repo, tmp_path / "elsewhere", data)


def test_rollback_from_a_release_missing_from_the_history(
    repo: Path, installed: Path, data: Path
) -> None:
    first = run(repo, installed, data)
    commit_change(repo, "config/skill-overlays/alpha.md", "v2\n")
    push(repo)
    run(repo, installed, data)
    st = state.load(data)
    assert st is not None
    state.save(data, state.State(**{**vars(st), "history": (first.release,)}))
    assert deploy.rollback(repo, installed, data).to == first.release


def test_the_backup_restore_py_is_this_repos_restore(repo: Path, home: Path, data: Path) -> None:
    result = run(repo, home, data)
    backup = Path(result.backup or "")
    assert (backup / "restore.py").read_bytes() == deploy.RESTORE_PY.read_bytes()
    assert os.access(backup / "restore.sh", os.X_OK)
    assert (repo / "tools" / "check_config.py").read_text() == CHECKER_OK


def test_utc_now_is_aware_utc() -> None:
    assert deploy.utc_now().tzinfo is UTC


def test_a_state_whose_release_is_gone_still_deploys(
    repo: Path, installed: Path, data: Path
) -> None:
    first = run(repo, installed, data)
    git(repo, "worktree", "remove", "--force", str(release.releases_dir(data) / first.release))
    commit_change(repo, "config/skill-overlays/alpha.md", "v2\n")
    push(repo)
    assert run(repo, installed, data).result == "deployed"


def test_a_setting_the_repo_changes_is_updated_without_a_conflict(
    repo: Path, installed: Path, data: Path
) -> None:
    run(repo, installed, data)
    fragment = json.loads((repo / "config" / "settings.fragment.json").read_text())
    fragment["enabledPlugins"]["tools@market"] = False
    commit_change(repo, "config/settings.fragment.json", json.dumps(fragment, indent=2) + "\n")
    push(repo)
    result = run(repo, installed, data)
    assert result.result == "deployed"
    assert settings(installed)["enabledPlugins"]["tools@market"] is False


def test_a_setting_that_left_the_fragment_is_removed_and_restore_puts_it_back(
    repo: Path, installed: Path, data: Path
) -> None:
    run(repo, installed, data)
    fragment = json.loads((repo / "config" / "settings.fragment.json").read_text())
    del fragment["enabledPlugins"]
    commit_change(repo, "config/settings.fragment.json", json.dumps(fragment, indent=2) + "\n")
    push(repo)
    result = run(repo, installed, data)
    assert result.settings_changes == ["remove enabledPlugins/tools@market"]
    assert "tools@market" not in settings(installed).get("enabledPlugins", {})
    done = subprocess.run([str(Path(result.backup or "") / "restore.sh")], check=False)
    assert done.returncode == 0
    assert settings(installed)["enabledPlugins"]["tools@market"] is True


def test_a_path_that_left_the_profile_and_is_already_gone_is_quiet(
    repo: Path, installed: Path, data: Path
) -> None:
    run(repo, installed, data)
    (installed / "skills" / "beta").unlink()
    commit_change(repo, HOST, json.dumps({**PROFILE, "skills": ["alpha"]}, indent=2) + "\n")
    push(repo)
    result = run(repo, installed, data)
    assert result.removed == [] and result.warnings == []


def test_a_home_without_plugins_still_runs_the_smoke_test(
    repo: Path, home: Path, data: Path
) -> None:
    shutil.rmtree(home / "plugins")
    assert run(repo, home, data).result == "deployed"


def test_a_release_git_cannot_read_is_kept_with_a_warning_not_an_error(
    repo: Path, installed: Path, data: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    first = run(repo, installed, data).release
    commit_change(repo, "config/skill-overlays/alpha.md", "v1\n")
    push(repo)
    second = run(repo, installed, data).release

    def unreadable(path: Path) -> list[str]:
        raise CfgError("git status failed")

    monkeypatch.setattr(deploy, "KEEP_RELEASES", 1)
    monkeypatch.setattr(release, "dirty_paths", unreadable)
    kept, warnings = deploy.prune(repo, data, [first, second], second)
    assert kept == [first, second]
    assert "couldn't prune" in warnings[0]


def test_a_rollback_that_fails_after_the_switch_says_so(
    repo: Path, installed: Path, data: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    run(repo, installed, data)
    commit_change(repo, HOST, json.dumps({**PROFILE, "skills": ["alpha"]}, indent=2) + "\n")
    push(repo)
    run(repo, installed, data)

    def broken(live: Path, target: str, replace_link: bool = False) -> None:
        raise OSError("disk full")

    monkeypatch.setattr(deploy.link, "make_link", broken)
    with pytest.raises(deploy.DeployFailed, match="after switching current"):
        deploy.rollback(repo, installed, data)
