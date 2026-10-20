"""The config uses the repo's own copies of the skills the claude-code-skills plugins had.

#62 copied the skills the owner uses into config/skills/ and removed the two
plugins, so overlays are named after bare repo skills, both host profiles
deploy those skills, and nothing enables the plugins again.
"""

import json

import pytest

from tests.checker_helpers import REPO_ROOT

OVERLAYS = REPO_ROOT / "config" / "skill-overlays"
REMOVED_PLUGINS = {
    "engineering-skills@claude-code-skills",
    "engineering-advanced-skills@claude-code-skills",
}
DROPPED_SKILLS = ["focused-fix", "senior-devops"]
PROFILES = sorted((REPO_ROOT / "hosts").glob("*.json"))


def overlay_skills() -> list[str]:
    return sorted(p.stem for p in OVERLAYS.glob("*.md"))


@pytest.mark.parametrize("profile", PROFILES, ids=lambda p: p.name)
def test_every_overlay_names_a_skill_the_profile_deploys(profile):
    # The eval harness resolves overlays against the tree's skills, not a profile,
    # so this keeps what it sees in step with what each machine gets.
    skills = set(json.loads(profile.read_text())["skills"])
    assert set(overlay_skills()) - skills == set()


def test_every_overlay_is_named_after_a_repo_skill():
    for name in overlay_skills():
        assert ":" not in name, f"{name}.md still has a plugin namespace"
        assert (REPO_ROOT / "config" / "skills" / name / "SKILL.md").is_file(), name


@pytest.mark.parametrize("profile", PROFILES, ids=lambda p: p.name)
def test_no_profile_installs_the_removed_plugins(profile):
    assert set(json.loads(profile.read_text())["plugins"]) & REMOVED_PLUGINS == set()


def test_the_settings_fragment_enables_neither_removed_plugin():
    fragment = json.loads((REPO_ROOT / "config" / "settings.fragment.json").read_text())
    assert set(fragment["enabledPlugins"]) & REMOVED_PLUGINS == set()


def test_the_marketplace_stays_known_for_upstream_reports():
    # Owner's decision on #62 (D-1): keep the marketplace so lock.py --check can
    # compare the vendored files with its clone; a known marketplace lists no skills.
    fragment = json.loads((REPO_ROOT / "config" / "settings.fragment.json").read_text())
    assert "claude-code-skills" in fragment["extraKnownMarketplaces"]


@pytest.mark.parametrize("skill", DROPPED_SKILLS)
def test_the_global_claude_md_no_longer_routes_to_a_dropped_skill(skill):
    assert skill not in (REPO_ROOT / "config" / "CLAUDE.global.md").read_text()
