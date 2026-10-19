"""Release worktrees, the current link, and what changed inside a release."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from tests.cfg_helpers import commit_change, git, make_repo, write_tree
from tools.cfg import release
from tools.cfg.errors import CfgError

pytestmark = pytest.mark.usefixtures("cfg_isolation")


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    return make_repo(tmp_path / "clone")


@pytest.fixture
def data(tmp_path: Path) -> Path:
    return tmp_path / "data"


def test_create_makes_a_detached_worktree_and_reuses_a_clean_one(repo: Path, data: Path) -> None:
    first = git(repo, "rev-parse", "HEAD")
    second = commit_change(repo, "config/CLAUDE.global.md", "# v2\n")

    a = release.create(repo, first[:12], data)
    a_again = release.create(repo, first, data)
    b = release.create(repo, "HEAD", data)

    assert (a, a_again, b) == (first, first, second)
    assert release.release_sha(data / "releases" / a) == first
    assert release.release_sha(data / "releases" / b) == second
    assert (data / "releases" / a / "config" / "CLAUDE.global.md").read_text() == "# global\n"


def test_a_dirty_release_or_fresh_request_gets_the_next_free_name(repo: Path, data: Path) -> None:
    sha = git(repo, "rev-parse", "HEAD")
    release.create(repo, sha, data)
    (data / "releases" / sha / "config" / "CLAUDE.global.md").write_text("edited\n")

    assert release.create(repo, sha, data) == f"{sha}-2"
    assert release.create(repo, sha, data) == f"{sha}-2"
    assert release.create(repo, sha, data, fresh=True) == f"{sha}-3"
    assert (data / "releases" / sha / "config" / "CLAUDE.global.md").read_text() == "edited\n"


def test_create_refuses_a_directory_that_is_not_its_worktree(repo: Path, data: Path) -> None:
    sha = git(repo, "rev-parse", "HEAD")
    (data / "releases" / sha).mkdir(parents=True)
    (data / "releases" / sha / "keep").write_text("mine")
    with pytest.raises(CfgError, match="isn't a release"):
        release.create(repo, sha, data)
    assert (data / "releases" / sha / "keep").read_text() == "mine"


def test_create_refuses_a_worktree_of_another_commit(repo: Path, data: Path) -> None:
    old = git(repo, "rev-parse", "HEAD")
    new = commit_change(repo, "x", "y")
    release.create(repo, old, data)
    os.rename(data / "releases" / old, data / "releases" / new)
    with pytest.raises(CfgError, match="isn't a release"):
        release.create(repo, new, data)


@pytest.mark.parametrize("ref", ["no-such-ref", "--output=x"])
def test_an_unknown_or_option_like_ref_is_refused(repo: Path, data: Path, ref: str) -> None:
    with pytest.raises(CfgError, match="isn't a commit"):
        release.resolve(repo, ref)


def test_swap_current_points_at_each_release_in_turn(repo: Path, data: Path) -> None:
    a = release.create(repo, "HEAD", data)
    b = release.create(repo, "HEAD", data, fresh=True)
    assert release.current_release(data) is None
    release.swap_current(data, a)
    assert os.readlink(data / "current") == f"releases/{a}"
    assert release.current_release(data) == a
    release.swap_current(data, b)
    assert release.current_release(data) == b
    assert sorted(p.name for p in data.iterdir()) == ["current", "releases"]


def test_swap_current_refuses_a_missing_release_or_a_plain_current(repo: Path, data: Path) -> None:
    a = release.create(repo, "HEAD", data)
    with pytest.raises(CfgError, match="no release"):
        release.swap_current(data, "nope")
    (data / "current").mkdir()
    with pytest.raises(CfgError, match="not a symlink"):
        release.swap_current(data, a)
    with pytest.raises(CfgError, match="not a symlink"):
        release.current_release(data)


def test_current_pointing_outside_releases_is_refused(data: Path) -> None:
    data.mkdir()
    (data / "current").symlink_to("/elsewhere")
    with pytest.raises(CfgError, match="releases/"):
        release.current_release(data)


def test_dirty_paths_leave_out_caches(repo: Path, data: Path) -> None:
    name = release.create(repo, "HEAD", data)
    root = data / "releases" / name
    (root / "config" / "CLAUDE.global.md").write_text("edited\n")
    write_tree(
        root,
        {
            "config/skills/alpha/new.md": "new\n",
            "config/skills/alpha/__pycache__/x.pyc": "c",
            "config/skills/alpha/.pytest_cache/v": "c",
            "config/skills/alpha/scripts/y.pyc": "c",
        },
    )
    git(root, "mv", "config/skills/beta/SKILL.md", "config/skills/beta/RENAMED.md")
    assert release.dirty_paths(root) == [
        "config/CLAUDE.global.md",
        "config/skills/alpha/new.md",
        "config/skills/beta/RENAMED.md",
        "config/skills/beta/SKILL.md",
    ]


def test_a_clean_release_has_no_dirty_paths(repo: Path, data: Path) -> None:
    name = release.create(repo, "HEAD", data)
    assert release.dirty_paths(data / "releases" / name) == []


def test_a_missing_git_is_reported(repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PATH", str(repo / "no-bin"))
    with pytest.raises(CfgError, match="git not found"):
        release.resolve(repo, "HEAD")


def test_swap_current_clears_a_stale_temporary_link(repo: Path, data: Path) -> None:
    name = release.create(repo, "HEAD", data)
    (data / f".current.cfg-{os.getpid()}").symlink_to("/stale")
    release.swap_current(data, name)
    assert sorted(p.name for p in data.iterdir()) == ["current", "releases"]


def test_is_ancestor_and_archive_report_git_failures(
    repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    head = git(repo, "rev-parse", "HEAD")
    assert release.is_ancestor(repo, head, head) is True
    with pytest.raises(CfgError, match="merge-base"):
        release.is_ancestor(repo, "0" * 40, head)
    with pytest.raises(CfgError, match="archive"):
        release.archive(repo, head, "no-such-path")
    monkeypatch.setenv("PATH", str(repo / "no-bin"))
    with pytest.raises(CfgError, match="git not found"):
        release.is_ancestor(repo, head, head)
