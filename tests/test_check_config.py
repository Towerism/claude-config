"""The config checker, run as a subprocess against temp Claude homes."""

import json

import pytest

from tests.checker_helpers import REPO_ROOT, make_overlay, make_skill, run_tool

SCRIPT = REPO_ROOT / "tools" / "check_config.py"
QA = "engineering-skills:senior-qa"
GOOD_MD = (
    "# Global rules\n"
    "- Test plans: `engineering-skills:senior-qa`.\n"
    "- Start work with `my-lifecycle`.\n"
    "- IMPORTANT: merge only with the user's authorization.\n"
)


@pytest.fixture
def setup(claude_home, tmp_path):
    make_skill(
        claude_home,
        "engineering-skills",
        "2.9.0",
        "senior-qa",
        ["references/testing_strategies.md"],
    )
    staging = tmp_path / "staging"
    (staging / "my-lifecycle").mkdir(parents=True)
    (staging / "my-lifecycle" / "SKILL.md").write_text(
        "---\nname: my-lifecycle\n---\nspec lives in the issue body\n"
    )
    md = tmp_path / "CLAUDE.md"
    md.write_text(GOOD_MD)
    make_overlay(
        claude_home, QA, "Read `references/testing_strategies.md`. Pyramid placement per AC."
    )
    return claude_home, staging, md


def check(home, staging, md, *extra, overlays=None):
    args = [
        "--claude-md",
        str(md),
        "--overlays",
        str(overlays or home / "skill-overlays"),
        "--claude-home",
        str(home),
        "--skill-root",
        str(staging),
        "--json",
        *extra,
    ]
    r = run_tool(SCRIPT, args)
    return r, (json.loads(r.stdout) if r.stdout.strip().startswith("{") else None)


def test_clean_config_passes(setup):
    r, rep = check(*setup)
    assert r.returncode == 0, r.stdout + r.stderr
    assert rep["ok"] is True and rep["unresolvedSkills"] == []


def test_unknown_skill_name_fails_naming_it(setup):
    home, staging, md = setup
    md.write_text(GOOD_MD + "- Also `engineering-skills:does-not-exist`.\n")
    r, rep = check(home, staging, md)
    assert r.returncode == 1 and rep["unresolvedSkills"] == ["engineering-skills:does-not-exist"]


def test_overlay_for_unknown_skill_fails(setup):
    home, staging, md = setup
    make_overlay(home, "engineering-skills:ghost", "x")
    r, rep = check(home, staging, md)
    assert r.returncode == 1 and "engineering-skills:ghost" in rep["unresolvedSkills"]


def test_overlay_path_missing_under_active_install_fails(setup):
    home, staging, md = setup
    make_overlay(home, QA, "Read `references/nope.md`.")
    r, rep = check(home, staging, md)
    assert r.returncode == 1 and rep["missingPaths"] == [
        {"skill": QA, "path": "references/nope.md"}
    ]


def test_a_synced_skill_name_is_a_finding_even_when_synced_here(setup):
    home, staging, md = setup
    d = home / "skills" / "synced" / "uuid-1" / "skill-creator"
    d.mkdir(parents=True)
    (d / "SKILL.md").write_text("x")
    md.write_text(GOOD_MD + "- `anthropic-skills:skill-creator`\n")
    r, rep = check(home, staging, md)
    assert r.returncode == 1
    assert rep["syncedSkills"] == ["anthropic-skills:skill-creator"]
    assert rep["unresolvedSkills"] == []


def test_repo_name_email_and_token_are_forbidden(setup):
    home, staging, md = setup
    md.write_text(GOOD_MD + "selfhost-kit\nme@example.com\nghp_" + "a" * 20 + "\n")
    r, rep = check(home, staging, md, "--repo-name", "selfhost-kit")
    kinds = sorted(f["kind"] for f in rep["forbiddenContent"])
    assert r.returncode == 1 and kinds == ["email", "repo-name", "token"]


@pytest.mark.parametrize(
    "body",
    [
        "# x\n- merge only with authorization.\n",  # no emphasis
        GOOD_MD + "- IMPORTANT: always run tests.\n",  # two emphasised lines
        "# x\n- IMPORTANT: always run tests.\n",  # wrong rule emphasised
    ],
)
def test_emphasis_must_be_exactly_the_merge_line(setup, body):
    home, staging, md = setup
    md.write_text(body)
    r, rep = check(home, staging, md)
    assert r.returncode == 1 and any(f["kind"] == "caps" for f in rep["forbiddenContent"])


def test_token_and_line_limits(setup):
    home, staging, md = setup
    md.write_text(GOOD_MD + ("word " * 1500 + "\n"))
    r, rep = check(home, staging, md)
    t = [x for x in rep["tokenCounts"] if x["file"].endswith("CLAUDE.md")][0]
    assert r.returncode == 1 and t["tokens"] > t["limit"] == 1800
    md.write_text(GOOD_MD + "x\n" * 201)
    r, rep = check(home, staging, md)
    assert r.returncode == 1 and any(
        x["file"].endswith("CLAUDE.md (lines)")
        for x in rep["tokenCounts"]
        if x["tokens"] > x["limit"]
    )


def test_overlay_token_limit(setup):
    home, staging, md = setup
    make_overlay(home, QA, "word " * 700)
    r, rep = check(home, staging, md)
    assert r.returncode == 1


def clauses(tmp_path, rows):
    p = tmp_path / "clauses.json"
    p.write_text(json.dumps(rows))
    return p


def test_clauses_all_good(setup, tmp_path):
    home, staging, md = setup
    rows = [
        {
            "id": "C-01",
            "text": "t",
            "disposition": "claude-md",
            "mustAppear": "merge only with",
            "reason": None,
        },
        {
            "id": "C-02",
            "text": "t",
            "disposition": f"overlay:{QA}",
            "mustAppear": "Pyramid placement",
            "reason": None,
        },
        {
            "id": "C-03",
            "text": "t",
            "disposition": "lifecycle-skill",
            "mustAppear": "issue body",
            "reason": None,
        },
        {
            "id": "C-04",
            "text": "t",
            "disposition": "superseded-by:FR-7",
            "mustAppear": None,
            "reason": "D-4",
        },
        {
            "id": "C-05",
            "text": "t",
            "disposition": "dropped",
            "mustAppear": None,
            "reason": "restates skill",
        },
    ]
    r, rep = check(
        home,
        staging,
        md,
        "--clauses",
        str(clauses(tmp_path, rows)),
        "--lifecycle-skill",
        "my-lifecycle",
    )
    assert r.returncode == 0, rep


@pytest.mark.parametrize(
    "row,problem",
    [
        (
            {"id": "C-01", "text": "t", "disposition": None, "mustAppear": None, "reason": None},
            "no-disposition",
        ),
        (
            {
                "id": "C-01",
                "text": "t",
                "disposition": "claude-md",
                "mustAppear": "absent phrase",
                "reason": None,
            },
            "phrase-missing",
        ),
        (
            {"id": "C-01", "text": "t", "disposition": "dropped", "mustAppear": None, "reason": ""},
            "no-reason",
        ),
        (
            {
                "id": "C-01",
                "text": "t",
                "disposition": f"overlay:{QA}",
                "mustAppear": "merge only with",
                "reason": None,
            },
            "phrase-missing",
        ),
    ],
)
def test_clause_problems(setup, tmp_path, row, problem):
    home, staging, md = setup
    r, rep = check(
        home,
        staging,
        md,
        "--clauses",
        str(clauses(tmp_path, [row])),
        "--lifecycle-skill",
        "my-lifecycle",
    )
    assert r.returncode == 1 and rep["clauseProblems"] == [{"id": "C-01", "problem": problem}]


def test_duplicate_clause_ids(setup, tmp_path):
    home, staging, md = setup
    row = {
        "id": "C-01",
        "text": "t",
        "disposition": "claude-md",
        "mustAppear": "merge only with",
        "reason": None,
    }
    r, rep = check(
        home,
        staging,
        md,
        "--clauses",
        str(clauses(tmp_path, [row, row])),
        "--lifecycle-skill",
        "my-lifecycle",
    )
    assert r.returncode == 1 and {"id": "C-01", "problem": "duplicate"} in rep["clauseProblems"]


def test_phrase_match_ignores_case_and_whitespace(setup, tmp_path):
    home, staging, md = setup
    row = {
        "id": "C-01",
        "text": "t",
        "disposition": "claude-md",
        "mustAppear": "MERGE   only\nwith",
        "reason": None,
    }
    r, rep = check(home, staging, md, "--clauses", str(clauses(tmp_path, [row])))
    assert r.returncode == 0, rep


def test_missing_claude_md_is_usage_error(setup, tmp_path):
    home, staging, _ = setup
    r, _ = check(home, staging, tmp_path / "nope.md")
    assert r.returncode == 2 and "not found" in r.stdout + r.stderr


def test_short_name_resolves_to_unique_plugin_skill(setup):
    home, staging, md = setup
    md.write_text(GOOD_MD + "- judge fit with `senior-qa`; run `-p` headless.\n")
    r, rep = check(home, staging, md)
    assert r.returncode == 0, rep


def test_ambiguous_short_name_is_unresolved(setup):
    home, staging, md = setup
    make_skill(home, "other-bundle", "1.0.0", "senior-qa", [])
    md.write_text(GOOD_MD + "- `senior-qa` is ambiguous here.\n")
    r, rep = check(home, staging, md)
    assert r.returncode == 1 and rep["unresolvedSkills"] == ["senior-qa"]
