"""Merging the repo's owned keys into settings.json, and writing it safely."""

from __future__ import annotations

import copy
import json
import os
from pathlib import Path
from typing import Any

import pytest

from tools.cfg import settings_merge as sm
from tools.cfg import state
from tools.cfg.errors import CfgError, LockBusy, SettingsChanged, SettingsUnusable

OVERLAY = {"type": "command", "command": "python3 -I /h/.claude/hooks/overlay.py"}
GUARD = {"type": "command", "command": "python3 -I /h/.claude/hooks/guard.py"}
OWNER_HOOK = {"type": "command", "command": "my-own-check"}

FRAGMENT: dict[str, Any] = {
    "enabledPlugins": {"tools@market": True},
    "extraKnownMarketplaces": {"market": {"source": {"source": "github", "repo": "o/r"}}},
    "hooks": {
        "PostToolUse": [{"matcher": "Skill", "hooks": [OVERLAY]}],
        "UserPromptExpansion": [{"hooks": [OVERLAY]}],
        "PreToolUse": [{"matcher": "Bash", "hooks": [GUARD]}],
    },
}


def owner_settings() -> dict[str, Any]:
    return {
        "theme": "dark",
        "permissions": {"allow": ["Bash(ls:*)"]},
        "hooks": {"PreToolUse": [{"matcher": "Bash", "hooks": [OWNER_HOOK]}]},
        "enabledPlugins": {"other@market": False},
    }


def handlers(settings: dict[str, Any], event: str) -> list[dict[str, Any]]:
    return [h for g in settings["hooks"].get(event, []) for h in g["hooks"]]


# ---- preflight ----------------------------------------------------------------


def test_a_missing_settings_file_reads_as_empty(tmp_path: Path) -> None:
    assert sm.preflight(tmp_path / "settings.json") == ({}, None)


def test_preflight_returns_the_parsed_settings_and_their_bytes(tmp_path: Path) -> None:
    path = tmp_path / "settings.json"
    path.write_text('{"theme": "dark"}\n')
    assert sm.preflight(path) == ({"theme": "dark"}, b'{"theme": "dark"}\n')


@pytest.mark.parametrize(
    ("make", "reason"),
    [
        (lambda p: p.symlink_to(p.parent / "real.json"), "symlink"),
        (lambda p: p.mkdir(), "regular file"),
        (lambda p: p.write_text("{not json"), "parse"),
        (lambda p: p.write_text("[]"), "object"),
    ],
    ids=["symlink", "directory", "unparseable", "not-object"],
)
def test_unusable_settings_are_refused_naming_file_and_reason(
    tmp_path: Path, make: Any, reason: str
) -> None:
    (tmp_path / "real.json").write_text("{}")
    path = tmp_path / "settings.json"
    make(path)
    with pytest.raises(SettingsUnusable, match=f"settings.json.*{reason}"):
        sm.preflight(path)


# ---- owned items --------------------------------------------------------------


def test_owned_items_name_each_plugin_marketplace_and_hook_handler() -> None:
    items = sm.owned_items(FRAGMENT)
    assert list(items) == [
        "enabledPlugins/tools@market",
        "extraKnownMarketplaces/market",
        "hooks/PostToolUse/Skill/python3 -I /h/.claude/hooks/overlay.py",
        "hooks/UserPromptExpansion//python3 -I /h/.claude/hooks/overlay.py",
        "hooks/PreToolUse/Bash/python3 -I /h/.claude/hooks/guard.py",
    ]
    assert items["hooks/PreToolUse/Bash/python3 -I /h/.claude/hooks/guard.py"] == GUARD


def test_a_fragment_matcher_holding_a_slash_is_refused() -> None:
    fragment = {"hooks": {"PreToolUse": [{"matcher": "a/b", "hooks": [GUARD]}]}}
    with pytest.raises(CfgError, match="a/b"):
        sm.owned_items(fragment)


def test_live_values_read_each_owned_key_or_report_it_missing() -> None:
    live = sm.merge(owner_settings(), FRAGMENT, {}).settings
    keys = [*sm.owned_items(FRAGMENT), "enabledPlugins/gone@m", "hooks/Stop//x"]
    values = sm.live_values(live, keys)
    assert values["enabledPlugins/tools@market"] is True
    assert values["hooks/PreToolUse/Bash/python3 -I /h/.claude/hooks/guard.py"] == GUARD
    assert values["enabledPlugins/gone@m"] is sm.MISSING
    assert values["hooks/Stop//x"] is sm.MISSING


def test_an_empty_matcher_reads_the_same_as_no_matcher() -> None:
    live = {"hooks": {"UserPromptExpansion": [{"matcher": "", "hooks": [OVERLAY]}]}}
    key = "hooks/UserPromptExpansion//python3 -I /h/.claude/hooks/overlay.py"
    assert sm.live_values(live, [key])[key] == OVERLAY


# ---- merge ----------------------------------------------------------------------


def test_merge_sets_owned_items_and_keeps_everything_else() -> None:
    live = owner_settings()
    result = sm.merge(live, FRAGMENT, {})
    new = result.settings
    assert list(new)[:4] == ["theme", "permissions", "hooks", "enabledPlugins"]
    assert new["theme"] == "dark" and new["permissions"] == {"allow": ["Bash(ls:*)"]}
    assert new["enabledPlugins"] == {"other@market": False, "tools@market": True}
    assert new["extraKnownMarketplaces"] == FRAGMENT["extraKnownMarketplaces"]
    assert handlers(new, "PreToolUse") == [OWNER_HOOK, GUARD]
    assert new["hooks"]["PreToolUse"][0]["matcher"] == "Bash"
    assert handlers(new, "PostToolUse") == [OVERLAY]
    assert new["hooks"]["UserPromptExpansion"] == [{"hooks": [OVERLAY]}]
    assert live == owner_settings()
    assert result.changes


def test_merge_leaves_one_copy_of_each_owned_handler_and_replaces_edited_ones() -> None:
    live = owner_settings()
    live["hooks"]["PreToolUse"].append({"matcher": "Bash", "hooks": [GUARD, OWNER_HOOK]})
    live["hooks"]["PreToolUse"][0]["hooks"].append({**GUARD, "timeout": 5})
    live["hooks"]["UserPromptExpansion"] = [
        {"matcher": "", "hooks": [OVERLAY]},
        {"hooks": [OVERLAY]},
    ]
    new = sm.merge(live, FRAGMENT, {}).settings
    assert handlers(new, "PreToolUse") == [OWNER_HOOK, GUARD, OWNER_HOOK]
    assert new["hooks"]["UserPromptExpansion"] == [{"matcher": "", "hooks": [OVERLAY]}]


def test_merge_is_idempotent() -> None:
    once = sm.merge(owner_settings(), FRAGMENT, {}).settings
    again = sm.merge(copy.deepcopy(once), FRAGMENT, sm.owned_items(FRAGMENT))
    assert again.settings == once
    assert again.changes == []


def test_an_item_that_left_the_fragment_is_removed_only_if_unchanged() -> None:
    old_fragment = copy.deepcopy(FRAGMENT)
    old_fragment["enabledPlugins"]["old@market"] = True
    old_fragment["extraKnownMarketplaces"]["oldmarket"] = {"source": "x"}
    old_fragment["hooks"]["Stop"] = [{"hooks": [{"type": "command", "command": "bye"}]}]
    deployed = sm.merge(owner_settings(), old_fragment, {}).settings
    deployed["extraKnownMarketplaces"]["oldmarket"] = {"source": "edited"}
    result = sm.merge(deployed, FRAGMENT, sm.owned_items(old_fragment))
    assert "old@market" not in result.settings["enabledPlugins"]
    assert "Stop" not in result.settings["hooks"]
    assert result.settings["extraKnownMarketplaces"]["oldmarket"] == {"source": "edited"}
    assert result.kept == ["extraKnownMarketplaces/oldmarket"]


def test_removing_a_leftover_handler_keeps_a_group_that_still_has_others() -> None:
    old_fragment = copy.deepcopy(FRAGMENT)
    old_fragment["hooks"]["PreToolUse"][0]["hooks"].append({"type": "command", "command": "old"})
    deployed = sm.merge(owner_settings(), old_fragment, {}).settings
    result = sm.merge(deployed, FRAGMENT, sm.owned_items(old_fragment))
    assert handlers(result.settings, "PreToolUse") == [OWNER_HOOK, GUARD]


def test_a_leftover_already_gone_from_live_is_ignored() -> None:
    old_fragment = {**FRAGMENT, "enabledPlugins": {**FRAGMENT["enabledPlugins"], "old@m": True}}
    result = sm.merge(owner_settings(), FRAGMENT, sm.owned_items(old_fragment))
    assert "old@m" not in result.settings["enabledPlugins"]
    assert result.kept == []


def test_merge_into_empty_settings_creates_the_sections() -> None:
    new = sm.merge({}, FRAGMENT, {}).settings
    assert new == FRAGMENT


@pytest.mark.parametrize(
    "live",
    [
        {"enabledPlugins": []},
        {"hooks": []},
        {"hooks": {"PreToolUse": {}}},
    ],
    ids=["plugins-not-object", "hooks-not-object", "event-not-list"],
)
def test_merge_refuses_owned_sections_of_the_wrong_shape(live: dict[str, Any]) -> None:
    with pytest.raises(SettingsUnusable):
        sm.merge(live, FRAGMENT, {})


def test_merge_skips_hook_groups_and_handlers_it_cannot_read() -> None:
    live: dict[str, Any] = {"hooks": {"PreToolUse": ["junk", {"matcher": "Bash", "hooks": ["x"]}]}}
    new = sm.merge(live, FRAGMENT, {}).settings
    assert new["hooks"]["PreToolUse"] == ["junk", {"matcher": "Bash", "hooks": ["x", GUARD]}]


# ---- write ----------------------------------------------------------------------


def test_an_unchanged_result_writes_nothing(tmp_path: Path) -> None:
    path = tmp_path / "settings.json"
    path.write_text(json.dumps(FRAGMENT))
    before = path.stat().st_mtime_ns
    live, original = sm.preflight(path)
    assert sm.write(path, live, original, tmp_path / "data") is False
    assert path.stat().st_mtime_ns == before
    assert not (tmp_path / "data").exists()


def test_a_changed_result_is_written_as_indented_json_keeping_the_mode(tmp_path: Path) -> None:
    path = tmp_path / "settings.json"
    path.write_text(json.dumps(owner_settings()))
    path.chmod(0o600)
    live, original = sm.preflight(path)
    new = sm.merge(live, FRAGMENT, {}).settings
    assert sm.write(path, new, original, tmp_path / "data") is True
    assert path.read_text() == json.dumps(new, indent=2, ensure_ascii=False) + "\n"
    assert path.stat().st_mode & 0o777 == 0o600
    assert sorted(p.name for p in tmp_path.iterdir()) == ["data", "settings.json"]


def test_a_new_settings_file_gets_mode_644(tmp_path: Path) -> None:
    path = tmp_path / "settings.json"
    old = os.umask(0o077)
    try:
        assert sm.write(path, FRAGMENT, None, tmp_path / "data") is True
    finally:
        os.umask(old)
    assert path.stat().st_mode & 0o777 == 0o644


def test_an_empty_result_for_a_missing_file_writes_nothing(tmp_path: Path) -> None:
    assert sm.write(tmp_path / "settings.json", {}, None, tmp_path / "data") is False
    assert not (tmp_path / "settings.json").exists()


def test_a_write_is_refused_when_another_program_changed_the_file(tmp_path: Path) -> None:
    path = tmp_path / "settings.json"
    path.write_text(json.dumps(owner_settings()))
    live, original = sm.preflight(path)
    new = sm.merge(live, FRAGMENT, {}).settings

    def harness_saves() -> None:
        path.write_text('{"theme": "light"}')

    with pytest.raises(SettingsChanged, match="another program"):
        sm.write(path, new, original, tmp_path / "data", before_replace=harness_saves)
    assert path.read_text() == '{"theme": "light"}'
    assert sorted(p.name for p in tmp_path.iterdir()) == ["data", "settings.json"]


def test_a_write_is_refused_while_another_cfg_holds_the_lock(tmp_path: Path) -> None:
    path = tmp_path / "settings.json"
    with state.cfg_lock(tmp_path / "data"):
        with pytest.raises(LockBusy):
            sm.write(path, FRAGMENT, None, tmp_path / "data")
    assert not path.exists()


def test_missing_shows_as_missing() -> None:
    assert repr(sm.MISSING) == "MISSING"


def test_a_group_with_the_matcher_but_not_the_handler_reads_missing() -> None:
    live = owner_settings()
    key = "hooks/PreToolUse/Bash/python3 -I /h/.claude/hooks/guard.py"
    assert sm.live_values(live, [key])[key] is sm.MISSING


def test_removing_the_last_owned_handler_removes_the_hooks_key() -> None:
    old = {"hooks": {"Stop": [{"hooks": [{"type": "command", "command": "bye"}]}]}}
    deployed = sm.merge({"theme": "dark"}, old, {}).settings
    result = sm.merge(deployed, {}, sm.owned_items(old))
    assert result.settings == {"theme": "dark"}


def test_a_caller_holding_the_lock_can_write_without_taking_it_again(tmp_path: Path) -> None:
    path = tmp_path / "settings.json"
    with state.cfg_lock(tmp_path / "data"):
        assert sm.write(path, FRAGMENT, None, tmp_path / "data", locked=True)
    assert json.loads(path.read_text()) == FRAGMENT
