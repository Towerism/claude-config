"""The shared config names no tracker, and the lifecycle hook is wired in (ADR-0011).

Both machines deploy the same global CLAUDE.md and overlays, so neither may
assume GitHub. The default lifecycle comes from the hook instead, which the
settings fragment runs on SessionStart and SubagentStart.
"""

import json

from tests.checker_helpers import REPO_ROOT, run_script

CLAUDE_MD = REPO_ROOT / "config" / "CLAUDE.global.md"
OVERLAYS = REPO_ROOT / "config" / "skill-overlays"
CHECKER = REPO_ROOT / "tools" / "check_config.py"
HOOK_COMMAND = "python3 -I {claude_home}/hooks/lifecycle_default.py"

STEP_2 = (
    "2. Start: the lifecycle skill for the repo's tracker: the one the repo's CLAUDE.md "
    "names, else the machine default stated at session start. Every code-change request "
    'in a repo with an issue tracker, even a bare "fix X", starts here, before reading or '
    "editing code."
)
STEP_3_END = "stored where the lifecycle skill says (the issue or ticket body)."
STEP_7 = "7. Merge: `ship-gate`, then the lifecycle skill closes out."
GIT_RULE = "subject ends with the issue or ticket reference, `(#N)` or `(KEY-N)`"
SPEC_OVERLAY_LINE = (
    "The spec lives where the repo's lifecycle skill says (the issue or ticket body), "
    "not in `specs/*.md`."
)
TDD_OVERLAY_START = (
    "When the work is on an issue or ticket, the spec lives where the repo's lifecycle "
    "skill says (the issue or ticket body)"
)
TDD_OVERLAY_REST = (
    ": don't create `specs/*.md`, and don't put AC IDs in code or test docstrings, "
    'whatever this skill\'s "Spec-First Workflow" section says.'
)


def test_the_global_claude_md_and_overlays_name_no_tracker_skill():
    for path in [CLAUDE_MD, *sorted(OVERLAYS.glob("*.md"))]:
        assert "github-issue-lifecycle" not in path.read_text(), path.name


def test_the_pipeline_and_git_rule_use_the_tracker_agnostic_wording():
    lines = CLAUDE_MD.read_text().splitlines()
    assert STEP_2 in lines
    assert next(x for x in lines if x.startswith("3. Spec:")).endswith(STEP_3_END)
    assert STEP_7 in lines
    assert GIT_RULE in CLAUDE_MD.read_text()
    assert "subject ends with `(#N)`" not in CLAUDE_MD.read_text()


def test_the_overlays_point_at_the_repos_lifecycle_skill():
    spec = (OVERLAYS / "engineering-advanced-skills:spec-driven-workflow.md").read_text()
    tdd = (OVERLAYS / "engineering-skills:tdd-guide.md").read_text()
    assert f"- {SPEC_OVERLAY_LINE}" in spec.splitlines()
    assert f"- {TDD_OVERLAY_START}{TDD_OVERLAY_REST}" in tdd.splitlines()


def test_the_clause_inventory_tracks_the_new_wording_and_passes_the_checker(tmp_path):
    rows = json.loads((REPO_ROOT / "eval" / "clauses.json").read_text())
    by_id = {r["id"]: r for r in rows}
    assert by_id["C-03"]["mustAppear"] == STEP_3_END.removesuffix(".")
    new = {r["mustAppear"]: r for r in rows if "#15" in r["text"] and "ADR-0011" in r["text"]}
    assert set(new) == {"the lifecycle skill for the repo's tracker", "`(#N)` or `(KEY-N)`"}
    assert {r["disposition"] for r in new.values()} == {"claude-md"}
    assert len(by_id) == len(rows)

    home = tmp_path / ".claude"
    (home / "plugins").mkdir(parents=True)
    args = [
        "--host", str(REPO_ROOT / "hosts" / "personal.json"),
        "--plugin-index", str(REPO_ROOT / "plugins.lock.json"),
        "--claude-home", str(home),
        "--clauses", str(REPO_ROOT / "eval" / "clauses.json"),
        "--json",
    ]  # fmt: skip
    r = run_script(CHECKER, "", home, args)
    rep = json.loads(r.stdout)
    assert r.returncode == 0, r.stdout
    assert rep["clauseProblems"] == []
    assert all(t["tokens"] <= t["limit"] for t in rep["tokenCounts"])


def test_the_fragment_runs_the_lifecycle_hook_at_session_and_subagent_start():
    hooks = json.loads((REPO_ROOT / "config" / "settings.fragment.json").read_text())["hooks"]
    for event in ("SessionStart", "SubagentStart"):
        assert hooks[event] == [{"hooks": [{"type": "command", "command": HOOK_COMMAND}]}]


def test_the_personal_profile_deploys_the_lifecycle_hook():
    profile = json.loads((REPO_ROOT / "hosts" / "personal.json").read_text())
    assert "lifecycle_default.py" in profile["hooks"]
    assert profile["defaultLifecycle"] == "github-issue-lifecycle"
