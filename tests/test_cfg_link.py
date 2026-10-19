"""Classifying managed paths, backing them up and replacing them with links."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from tests.cfg_helpers import write_tree
from tools.cfg import link, state
from tools.cfg.errors import CfgError


@pytest.fixture
def release(tmp_path: Path) -> Path:
    root = tmp_path / "release"
    write_tree(
        root,
        {
            "config/CLAUDE.global.md": "# global\n",
            "config/skills/alpha/SKILL.md": "alpha\n",
            "config/skills/alpha/scripts/run.py": "print(1)\n",
        },
    )
    (root / "config" / "skills" / "alpha" / "scripts" / "latest").symlink_to("run.py")
    return root


@pytest.fixture
def home(tmp_path: Path) -> Path:
    path = tmp_path / "home" / ".claude"
    path.mkdir(parents=True)
    return path


TARGET = "/data/current/config/skills/alpha"


def copy_skill(release: Path, dest: Path) -> None:
    write_tree(
        dest,
        {"SKILL.md": "alpha\n", "scripts/run.py": "print(1)\n"},
    )
    (dest / "scripts" / "latest").symlink_to("run.py")


# ---- classify -------------------------------------------------------------------


def test_nothing_there_is_missing(home: Path, release: Path) -> None:
    assert (
        link.classify(home / "skills" / "alpha", TARGET, release / "config/skills/alpha")
        == "missing"
    )


def test_a_link_with_the_expected_text_is_linked(home: Path, release: Path) -> None:
    (home / "alpha").symlink_to(TARGET)
    assert link.classify(home / "alpha", TARGET, release / "config/skills/alpha") == "linked"


@pytest.mark.parametrize("text", ["/data/releases/abc/config/skills/alpha", "/nowhere/at/all"])
def test_any_other_link_is_foreign_even_when_dangling(home: Path, release: Path, text: str) -> None:
    (home / "alpha").symlink_to(text)
    assert link.classify(home / "alpha", TARGET, release / "config/skills/alpha") == "foreign-link"


def test_a_link_pointing_at_a_matching_copy_is_not_followed(home: Path, release: Path) -> None:
    (home / "alpha").symlink_to(release / "config" / "skills" / "alpha")
    assert link.classify(home / "alpha", TARGET, release / "config/skills/alpha") == "foreign-link"


def test_a_directory_matching_except_for_caches_is_a_match(home: Path, release: Path) -> None:
    copy_skill(release, home / "alpha")
    write_tree(
        home / "alpha",
        {"__pycache__/x.cpython-314.pyc": "c", ".pytest_cache/v": "c", "scripts/stray.pyc": "c"},
    )
    assert link.classify(home / "alpha", TARGET, release / "config/skills/alpha") == "match"


@pytest.mark.parametrize(
    "change",
    [
        lambda d: (d / "SKILL.md").write_text("edited\n"),
        lambda d: (d / "extra.md").write_text("new\n"),
        lambda d: (d / "scripts" / "run.py").unlink(),
        lambda d: ((d / "scripts" / "latest").unlink(), (d / "scripts" / "latest").symlink_to("x")),
    ],
    ids=["edited", "added", "removed", "link-text"],
)
def test_a_directory_with_any_difference_differs(home: Path, release: Path, change: object) -> None:
    copy_skill(release, home / "alpha")
    change(home / "alpha")  # type: ignore[operator]
    assert link.classify(home / "alpha", TARGET, release / "config/skills/alpha") == "differs"


def test_a_file_matches_or_differs_by_content(home: Path, release: Path) -> None:
    source = release / "config" / "CLAUDE.global.md"
    (home / "CLAUDE.md").write_text("# global\n")
    assert link.classify(home / "CLAUDE.md", "/t", source) == "match"
    (home / "CLAUDE.md").write_text("# edited\n")
    assert link.classify(home / "CLAUDE.md", "/t", source) == "differs"


def test_a_file_where_a_directory_belongs_differs(home: Path, release: Path) -> None:
    (home / "alpha").write_text("alpha\n")
    assert link.classify(home / "alpha", TARGET, release / "config/skills/alpha") == "differs"
    (home / "CLAUDE.md").mkdir()
    assert link.classify(home / "CLAUDE.md", "/t", release / "config/CLAUDE.global.md") == "differs"


def test_a_missing_source_makes_a_plain_path_differ(home: Path, tmp_path: Path) -> None:
    (home / "x").write_text("x")
    assert link.classify(home / "x", "/t", tmp_path / "gone") == "differs"


# ---- backup ---------------------------------------------------------------------


def test_backup_moves_paths_and_lists_files_and_links(home: Path, release: Path) -> None:
    (home / "CLAUDE.md").write_text("# mine\n")
    copy_skill(release, home / "skills" / "alpha")
    (home / "hooks").mkdir()
    (home / "hooks" / "guard.py").symlink_to("/old/guard.py")
    backup_dir = home / "backups" / "claude-config" / "ts"
    paths = [home / "CLAUDE.md", home / "skills" / "alpha", home / "hooks" / "guard.py"]

    entries = link.backup(paths, home, backup_dir)

    sha = state.sha256_file
    expected = [
        state.ManifestEntry("CLAUDE.md", sha256=sha(backup_dir / "CLAUDE.md")),
        state.ManifestEntry(
            "skills/alpha/SKILL.md", sha256=sha(backup_dir / "skills/alpha/SKILL.md")
        ),
        state.ManifestEntry("skills/alpha/scripts/latest", link="run.py"),
        state.ManifestEntry(
            "skills/alpha/scripts/run.py", sha256=sha(backup_dir / "skills/alpha/scripts/run.py")
        ),
        state.ManifestEntry("hooks/guard.py", link="/old/guard.py"),
    ]
    assert entries == expected
    assert state.read_manifest(backup_dir / "manifest.sha256") == expected
    assert not any(p.exists() or p.is_symlink() for p in paths)
    assert (backup_dir / "CLAUDE.md").read_text() == "# mine\n"
    assert os.readlink(backup_dir / "hooks" / "guard.py") == "/old/guard.py"


def test_backup_refuses_a_path_outside_the_claude_home(home: Path, tmp_path: Path) -> None:
    (tmp_path / "outside").write_text("x")
    with pytest.raises(CfgError, match="outside"):
        link.backup([tmp_path / "outside"], home, home / "backups" / "b")
    assert (tmp_path / "outside").exists()


def test_backup_refuses_another_filesystem_before_moving_anything(
    home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (home / "CLAUDE.md").write_text("x")
    monkeypatch.setattr(link, "_same_filesystem", lambda a, b: False)
    with pytest.raises(CfgError, match="filesystem"):
        link.backup([home / "CLAUDE.md"], home, home / "backups" / "b")
    assert (home / "CLAUDE.md").exists()
    assert not (home / "backups" / "b" / "manifest.sha256").exists()


def test_backup_refuses_a_backup_directory_that_already_has_a_manifest(home: Path) -> None:
    (home / "CLAUDE.md").write_text("x")
    backup_dir = home / "backups" / "b"
    backup_dir.mkdir(parents=True)
    (backup_dir / "manifest.sha256").write_text("")
    with pytest.raises(CfgError, match="already"):
        link.backup([home / "CLAUDE.md"], home, backup_dir)
    assert (home / "CLAUDE.md").exists()


def test_same_filesystem_compares_devices(home: Path) -> None:
    assert link._same_filesystem(home, home)


# ---- make_link --------------------------------------------------------------------


def test_make_link_creates_parents_and_the_link(home: Path) -> None:
    link.make_link(home / "skills" / "alpha", TARGET)
    assert os.readlink(home / "skills" / "alpha") == TARGET
    assert sorted(p.name for p in (home / "skills").iterdir()) == ["alpha"]


def test_make_link_refuses_a_plain_path_still_in_place(home: Path) -> None:
    (home / "CLAUDE.md").write_text("mine")
    with pytest.raises(CfgError, match="back"):
        link.make_link(home / "CLAUDE.md", "/t")
    assert (home / "CLAUDE.md").read_text() == "mine"


def test_make_link_replaces_an_existing_link_only_when_asked(home: Path) -> None:
    (home / "alpha").symlink_to("/old")
    with pytest.raises(CfgError, match="link"):
        link.make_link(home / "alpha", TARGET)
    assert os.readlink(home / "alpha") == "/old"
    link.make_link(home / "alpha", TARGET, replace_link=True)
    assert os.readlink(home / "alpha") == TARGET


def test_make_link_clears_a_stale_temporary_link(home: Path) -> None:
    (home / f".alpha.cfg-link-{os.getpid()}").symlink_to("/stale")
    link.make_link(home / "alpha", TARGET)
    assert sorted(p.name for p in home.iterdir()) == ["alpha"]


def test_a_directory_link_inside_a_skill_is_compared_by_its_text(home: Path, release: Path) -> None:
    (release / "config" / "skills" / "alpha" / "shared").symlink_to("scripts")
    copy_skill(release, home / "alpha")
    (home / "alpha" / "shared").symlink_to("scripts")
    assert link.classify(home / "alpha", TARGET, release / "config/skills/alpha") == "match"
    (home / "alpha" / "shared").unlink()
    (home / "alpha" / "shared").symlink_to("elsewhere")
    assert link.classify(home / "alpha", TARGET, release / "config/skills/alpha") == "differs"


def test_special_files_are_left_out_of_comparison_and_manifest(home: Path, release: Path) -> None:
    copy_skill(release, home / "alpha")
    os.mkfifo(home / "alpha" / "pipe")
    assert link.classify(home / "alpha", TARGET, release / "config/skills/alpha") == "match"
    entries = link.backup([home / "alpha"], home, home / "backups" / "b")
    assert [e.path for e in entries] == [
        "alpha/SKILL.md",
        "alpha/scripts/latest",
        "alpha/scripts/run.py",
    ]
