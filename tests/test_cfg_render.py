"""Rendering a host profile into the deploy set and the settings fragment."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from tests.cfg_helpers import FRAGMENT, PROFILE, make_tree, write_tree
from tools.cfg import render
from tools.cfg.errors import CfgError
from tools.cfg.state import ManagedPath

REPO_ROOT = Path(__file__).resolve().parent.parent
HOME = Path("/home/u/.claude")
DATA = Path("/home/u/.local/share/claude-config")


def tree_snapshot(root: Path) -> list[tuple[str, float]]:
    return sorted((str(p), p.stat().st_mtime) for p in root.rglob("*"))


def test_the_personal_profile_renders_every_component() -> None:
    ds = render.render(REPO_ROOT, "hosts/personal.json", HOME, DATA)
    profile = json.loads((REPO_ROOT / "hosts" / "personal.json").read_text())
    expected = [
        ManagedPath(
            "CLAUDE.md",
            "config/CLAUDE.global.md",
            "file",
            f"{DATA}/current/config/CLAUDE.global.md",
        ),
        ManagedPath(
            "skill-overlays",
            "config/skill-overlays",
            "dir",
            f"{DATA}/current/config/skill-overlays",
        ),
    ]
    expected += [
        ManagedPath(f"hooks/{h}", f"config/hooks/{h}", "file", f"{DATA}/current/config/hooks/{h}")
        for h in profile["hooks"]
    ]
    trees = {s: "skills" if (REPO_ROOT / "config" / "skills" / s).is_dir() else "vendored-skills"
             for s in profile["skills"]}  # fmt: skip
    expected += [
        ManagedPath(
            f"skills/{s}",
            f"config/{trees[s]}/{s}",
            "dir",
            f"{DATA}/current/config/{trees[s]}/{s}",
        )
        for s in profile["skills"]
    ]
    assert list(ds.paths) == expected
    assert sorted(trees.values()).count("skills") == 7
    assert ds.host == "hosts/personal.json"


def test_a_second_profile_renders_its_own_components(tmp_path: Path) -> None:
    profile = {**PROFILE, "hooks": ["guard.py"], "skills": ["beta"], "defaultLifecycle": "beta"}
    tree = make_tree(tmp_path / "tree", profile=profile)
    home, data = tmp_path / "home" / ".claude", tmp_path / "data"
    home.mkdir(parents=True)
    data.mkdir()
    before = tree_snapshot(home) + tree_snapshot(data)
    ds = render.render(tree, "hosts/test.json", home, data)
    assert [(p.live, p.kind) for p in ds.paths] == [
        ("CLAUDE.md", "file"),
        ("skill-overlays", "dir"),
        ("hooks/guard.py", "file"),
        ("skills/beta", "dir"),
    ]
    assert ds.paths[-1].target == f"{data}/current/config/skills/beta"
    assert tree_snapshot(home) + tree_snapshot(data) == before


def test_rendered_hook_commands_use_the_claude_home() -> None:
    ds = render.render(REPO_ROOT, "hosts/personal.json", HOME, DATA)
    commands = [
        h["command"] for groups in ds.fragment["hooks"].values() for g in groups for h in g["hooks"]
    ]
    assert commands == [
        "python3 -I /home/u/.claude/hooks/skill_overlay.py",
        "python3 -I /home/u/.claude/hooks/skill_overlay.py",
        "python3 -I /home/u/.claude/hooks/merge_confirm.py",
        "python3 -I /home/u/.claude/hooks/lifecycle_default.py",
        "python3 -I /home/u/.claude/hooks/lifecycle_default.py",
    ]
    assert "{claude_home}" not in json.dumps(ds.fragment)


@pytest.mark.parametrize(
    ("profile", "message"),
    [
        ({**PROFILE, "skills": ["alpha", "gone"]}, "gone"),
        ({**PROFILE, "hooks": ["overlay.py", "gone.py"]}, "gone.py"),
        ({**PROFILE, "overlays": ["alpha"]}, "overlays"),
        ({**PROFILE, "skills": ["alpha", "../x"]}, r"\.\./x"),
        ({**PROFILE, "skills": ["a/b"]}, "a/b"),
        ({**PROFILE, "hooks": ["."]}, "'.'"),
        ({**PROFILE, "hooks": ["a\\b"]}, r"a\\\\b"),
        ({**PROFILE, "claudeMd": "hosts/test.json"}, "config/"),
        ({**PROFILE, "claudeMd": "config/missing.md"}, "missing.md"),
    ],
    ids=[
        "missing-skill",
        "missing-hook",
        "overlay-list",
        "dotdot",
        "slash",
        "dot",
        "backslash",
        "claude-md-outside",
        "claude-md-missing",
    ],
)
def test_an_unusable_profile_is_refused_naming_the_problem(
    tmp_path: Path, profile: dict[str, Any], message: str
) -> None:
    tree = make_tree(tmp_path, profile=profile)
    with pytest.raises(CfgError, match=message):
        render.render(tree, "hosts/test.json", HOME, DATA)


def test_a_profile_without_overlays_dir_is_refused(tmp_path: Path) -> None:
    tree = make_tree(tmp_path)
    (tree / "config" / "skill-overlays" / "alpha.md").unlink()
    (tree / "config" / "skill-overlays").rmdir()
    with pytest.raises(CfgError, match="skill-overlays"):
        render.render(tree, "hosts/test.json", HOME, DATA)


def test_an_unreadable_profile_is_refused(tmp_path: Path) -> None:
    tree = make_tree(tmp_path)
    with pytest.raises(CfgError, match="nope.json"):
        render.render(tree, "hosts/nope.json", HOME, DATA)


@pytest.mark.parametrize(
    ("fragment", "message"),
    [
        ({**FRAGMENT, "theme": "dark"}, "theme"),
        ({**FRAGMENT, "enabledPlugins": {"{user}@m": True}}, "{user}"),
        ([], "object"),
    ],
    ids=["unknown-key", "other-placeholder", "not-object"],
)
def test_an_unusable_fragment_is_refused(tmp_path: Path, fragment: Any, message: str) -> None:
    tree = make_tree(tmp_path)
    write_tree(tree, {"config/settings.fragment.json": json.dumps(fragment)})
    with pytest.raises(CfgError, match=message):
        render.render(tree, "hosts/test.json", HOME, DATA)


def test_an_unparseable_fragment_is_refused(tmp_path: Path) -> None:
    tree = make_tree(tmp_path)
    write_tree(tree, {"config/settings.fragment.json": "{not json"})
    with pytest.raises(CfgError, match="settings.fragment.json"):
        render.render(tree, "hosts/test.json", HOME, DATA)


@pytest.mark.parametrize("home", ["/home/u/my claude", "/home/u/$x", "/home/u/a;b"])
def test_a_claude_home_needing_shell_quotes_is_refused(tmp_path: Path, home: str) -> None:
    tree = make_tree(tmp_path)
    with pytest.raises(CfgError, match="shell"):
        render.render(tree, "hosts/test.json", Path(home), DATA)


def test_placeholders_are_replaced_in_nested_lists_and_keys_are_kept(tmp_path: Path) -> None:
    fragment = {**FRAGMENT, "extraKnownMarketplaces": {"m": {"paths": ["{claude_home}/x", 3]}}}
    tree = make_tree(tmp_path, fragment=fragment)
    ds = render.render(tree, "hosts/test.json", HOME, DATA)
    assert ds.fragment["extraKnownMarketplaces"] == {"m": {"paths": ["/home/u/.claude/x", 3]}}


def test_a_vendored_skill_deploys_from_its_own_tree(tmp_path: Path) -> None:
    tree = make_tree(tmp_path, profile={**PROFILE, "skills": ["alpha", "gamma"]})
    write_tree(tree, {"config/vendored-skills/gamma/SKILL.md": "---\nname: gamma\n---\n"})
    ds = render.render(tree, "hosts/test.json", HOME, DATA)
    assert ds.paths[-2:] == (
        ManagedPath(
            "skills/alpha", "config/skills/alpha", "dir", f"{DATA}/current/config/skills/alpha"
        ),
        ManagedPath(
            "skills/gamma",
            "config/vendored-skills/gamma",
            "dir",
            f"{DATA}/current/config/vendored-skills/gamma",
        ),
    )


def test_a_skill_in_both_trees_is_refused_naming_both_paths(tmp_path: Path) -> None:
    tree = make_tree(tmp_path)
    write_tree(tree, {"config/vendored-skills/alpha/SKILL.md": "---\nname: alpha\n---\n"})
    with pytest.raises(CfgError, match="config/skills/alpha and config/vendored-skills/alpha"):
        render.render(tree, "hosts/test.json", HOME, DATA)
