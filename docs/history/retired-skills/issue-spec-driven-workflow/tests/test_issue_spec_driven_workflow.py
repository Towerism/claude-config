"""
Test suite for: Issue Spec-Driven Workflow Skill (SPEC.md).

Generated as stubs by scripts/test_extractor.py, then implemented.
Two kinds of test:
  - static: SKILL.md must instruct a behavior (agent behavior itself is OS-1);
  - executed: bash blocks are lifted out of SKILL.md and run against a fake `gh`
    that records calls and serves canned issue bodies (no network).
"""

import filecmp
import json
import os
import re
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

SKILL_DIR = Path(__file__).resolve().parent.parent
SKILL = (SKILL_DIR / "SKILL.md").read_text()
SPEC = (SKILL_DIR / "SPEC.md").read_text()

BASE_VERSION = "2.9.0"
PLUGIN_CACHE = Path.home() / ".claude/plugins/cache"

# Base-skill lines rewritten on purpose (SPEC.md, Changed-Lines Allowlist).
CHANGED_LINES = {
    'name: "spec-driven-workflow"',
    "# Spec-Driven Workflow — POWERFUL",
    "## Workflow — 6 Phases",
    "**Exit criteria:** The spec can be handed to a developer who was not in the "
    "requirements meeting, and they can implement the feature without asking clarifying questions.",
    "Run `spec_validator.py` against the spec file:",
    "python spec_validator.py --file spec.md --strict",
    "python test_extractor.py --file spec.md --framework pytest --output tests/",
    "- If you discover a missing requirement, STOP and update the spec first.",
    "Phase 2: Write Spec",
    "python spec_validator.py --file specs/auth.md --strict",
    "python test_extractor.py --file specs/auth.md --framework pytest --output tests/test_auth.py",
}


# ── helpers ────────────────────────────────────────────────────────────────


def find_base_skill(cache_root: Path):
    hits = sorted(
        cache_root.glob(
            f"*/engineering-advanced-skills/{BASE_VERSION}/skills/spec-driven-workflow"
        )
    )
    return hits[0] if hits else None


def base_skill_or_skip() -> Path:
    base = find_base_skill(PLUGIN_CACHE)
    if base is None:
        pytest.skip(f"base spec-driven-workflow {BASE_VERSION} not in {PLUGIN_CACHE}")
    return base


def phase(n: int) -> str:
    m = re.search(rf"^### Phase {n}:.*?(?=^### Phase |^## )", SKILL, re.M | re.S)
    assert m, f"Phase {n} not found in SKILL.md"
    return m.group(0)


def section(heading: str) -> str:
    m = re.search(rf"^## {re.escape(heading)}\n.*?(?=^## )", SKILL, re.M | re.S)
    assert m, f"section {heading!r} not found"
    return m.group(0)


def bash_block(text: str, containing: str) -> str:
    blocks = [
        textwrap.dedent(b)
        for b in re.findall(r"```bash\n(.*?)```", text, re.S)
        if containing in b
    ]
    assert len(blocks) == 1, f"expected one bash block containing {containing!r}, got {len(blocks)}"
    return blocks[0]


FAKE_GH = """\
import json, os, sys
args = sys.argv[1:]
with open(os.environ["GH_LOG"], "a") as log:
    log.write(json.dumps(args) + "\\n")
state = os.environ["GH_STATE"]
if args[:2] == ["label", "create"]:
    labels = os.path.join(state, "labels")
    existing = open(labels).read().split("\\n") if os.path.exists(labels) else []
    if args[2] in existing:
        sys.stderr.write(f"label with name {args[2]!r} already exists\\n")
        sys.exit(1)
    with open(labels, "a") as f:
        f.write(args[2] + "\\n")
    sys.exit(0)
if args[:2] == ["issue", "view"] and "body" in args:
    sys.stdout.buffer.write(open(os.path.join(state, "body"), "rb").read())
    sys.exit(0)
if args[:2] == ["issue", "edit"]:
    sys.exit(0)
sys.stderr.write("fake gh: unhandled " + " ".join(args) + "\\n")
sys.exit(3)
"""


class FakeGh:
    def __init__(self, tmp: Path):
        self.tmp = tmp
        self.bin = tmp / "bin"
        self.state = tmp / "state"
        self.log = tmp / "gh.log"
        self.bin.mkdir()
        self.state.mkdir()
        gh = self.bin / "gh"
        gh.write_text(f"#!{sys.executable}\n" + FAKE_GH)
        gh.chmod(0o755)

    def set_body(self, body: str):
        (self.state / "body").write_bytes(body.encode())

    def calls(self):
        if not self.log.exists():
            return []
        return [json.loads(line) for line in self.log.read_text().splitlines()]

    def run(self, block: str):
        script = block.replace("/tmp/", f"{self.tmp}/")
        env = {
            **os.environ,
            "PATH": f"{self.bin}:{os.environ['PATH']}",
            "GH_LOG": str(self.log),
            "GH_STATE": str(self.state),
        }
        return subprocess.run(
            ["bash", "-c", script], env=env, cwd=self.tmp, capture_output=True, text=True, timeout=30
        )


@pytest.fixture
def gh(tmp_path):
    return FakeGh(tmp_path)


MARK_IN_PROGRESS = 'gh label create "in progress"'
EXTRACT = "spec:start"
ORIGINAL = "Users want to export the roster as CSV."


def body_with_spec() -> str:
    return f"{ORIGINAL}\n\n<!-- spec:start -->\n{SPEC}\n<!-- spec:end -->\n"


# ── acceptance criteria ────────────────────────────────────────────────────


class TestIssueSpecDrivenWorkflowSkill:
    """Tests for Issue Spec-Driven Workflow Skill."""

    def test_ac1_installed_under_the_right_name(self):
        """AC-1: Installed under the right name [FR-1]"""
        assert SKILL_DIR.name == "issue-spec-driven-workflow"
        front = re.match(r"---\n(.*?)\n---\n", SKILL, re.S).group(1)
        assert re.search(r'^name: "issue-spec-driven-workflow"$', front, re.M)
        description = re.search(r"^description: (.*)$", front, re.M).group(1)
        assert "issue" in description.lower()

    def test_ac2_base_skill_text_preserved(self):
        """AC-2: Base skill text preserved [FR-2]"""
        base = (base_skill_or_skip() / "SKILL.md").read_text()
        base_body = re.sub(r"\A---\n.*?\n---\n", "", base, flags=re.S)
        wanted = [
            line.strip()
            for line in base_body.splitlines()
            if line.strip() and line.strip() not in CHANGED_LINES
        ]
        have = iter(line.strip() for line in SKILL.splitlines())
        missing = [line for line in wanted if not any(line == h for h in have)]
        assert missing == [], f"base lines missing or out of order: {missing[:3]}"

    def test_ac3_scripts_and_references_copied_unchanged(self):
        """AC-3: Scripts and references copied unchanged [FR-2]"""
        base = base_skill_or_skip()
        for sub in ("scripts", "references"):
            names = sorted(p.name for p in (base / sub).iterdir() if p.is_file())
            assert names, f"base {sub}/ is empty"
            match, mismatch, errors = filecmp.cmpfiles(base / sub, SKILL_DIR / sub, names, shallow=False)
            assert (mismatch, errors) == ([], []), f"{sub}: changed {mismatch}, missing {errors}"

    def test_ac4_phase_0_precedes_phase_1(self):
        """AC-4: Phase 0 precedes Phase 1 [FR-3]"""
        p0, p1 = SKILL.find("### Phase 0:"), SKILL.find("### Phase 1:")
        assert 0 <= p0 < p1

    def test_ac5_all_issue_key_forms_recognised(self):
        """AC-5: All issue key forms recognised [FR-4]"""
        p0 = phase(0)
        for form in ("`#42`", "`GH-42`", "`issue 42`", "`owner/repo#42`", "https://github.com/owner/repo/issues/42"):
            assert form in p0, form

    def test_ac6_no_key_means_the_user_chooses_with_a_create_option(self):
        """AC-6: No key means the user chooses, with a create option [FR-5]"""
        p0 = phase(0)
        assert "gh issue list --state open" in p0
        assert "--state all" in p0
        assert "AskUserQuestion" in p0
        assert "at most 3" in p0
        assert "`Create a new issue`" in p0
        assert "**always** a `Create a new issue` option" in p0
        assert "even when there's exactly one clear match" in p0

    def test_ac7_new_issue_built_from_the_prompt(self):
        """AC-7: New issue built from the prompt [FR-6]"""
        p0 = phase(0)
        assert "gh issue create --title" in p0
        assert "the user's prompt *is* the issue content, or it points to it" in p0
        assert "Don't interview the user for issue content" in p0
        assert "without adding requirements" in p0

    def test_ac8_ask_before_reopening_or_taking_over(self):
        """AC-8: Ask before reopening or taking over [FR-7]"""
        p0 = phase(0)
        assert "ask before reopening (`gh issue reopen 42`)" in p0
        assert "already labelled `in progress`:** ask before taking it over" in p0

    def test_ac9_mark_in_progress_creates_the_labels_and_assigns(self, gh):
        """AC-9: Mark in progress creates the labels and assigns [FR-8]"""
        result = gh.run(bash_block(phase(0), MARK_IN_PROGRESS))
        assert result.returncode == 0, result.stderr
        created = [c[2] for c in gh.calls() if c[:2] == ["label", "create"]]
        assert created == ["in progress", "draft", "approved"]
        assert ["issue", "edit", "42", "--add-assignee", "@me", "--add-label", "in progress"] in gh.calls()

    def test_ac10_spec_block_extracted_from_the_issue_body(self, gh):
        """AC-10: Spec block extracted from the issue body [FR-9, FR-12]"""
        gh.set_body(body_with_spec())
        result = gh.run(bash_block(phase(3), EXTRACT))
        assert result.returncode == 0, result.stdout + result.stderr
        extracted = (gh.tmp / "issue-42-spec.md").read_text()
        assert "# Spec: Issue Spec-Driven Workflow Skill" in extracted
        assert ORIGINAL not in extracted
        assert "Score: 100/100" in result.stdout

    def test_ac11_draft_and_approved_labels_swap(self, gh):
        """AC-11: Draft and approved labels swap [FR-10]"""
        p2 = phase(2)
        assert gh.run(bash_block(p2, "--add-label draft")).returncode == 0
        assert gh.run(bash_block(p2, "--add-label approved")).returncode == 0
        assert gh.calls() == [
            ["issue", "edit", "42", "--add-label", "draft", "--remove-label", "approved"],
            ["issue", "edit", "42", "--add-label", "approved", "--remove-label", "draft"],
        ]

    def test_ac12_implementation_gated_on_the_approved_label(self):
        """AC-12: Implementation gated on the approved label [FR-11]"""
        assert "Implementation (Phase 4 on) waits for `approved`" in phase(2)

    def test_ac13_spec_changes_go_back_to_draft(self):
        """AC-13: Spec changes go back to draft [FR-13]"""
        p5 = phase(5)
        assert "edit the spec block in the **issue body**" in p5
        assert "set the label back to `draft`" in p5

    def test_ac14_pr_closes_the_issue(self):
        """AC-14: PR closes the issue [FR-14]"""
        p7 = phase(7)
        assert re.search(r"^\s*Closes #42\s*$", p7, re.M)
        assert "defaultBranchRef" in p7
        assert "closingIssuesReferences" in p7

    def test_ac15_no_manual_close_after_merge(self):
        """AC-15: No manual close after merge [FR-15]"""
        p7 = phase(7)
        assert "don't close it by hand without asking" in p7
        assert 'gh issue edit 42 --remove-label "in progress"' in p7

    def test_ac16_no_spec_file_in_the_repo(self):
        """AC-16: No spec file in the repo [FR-9]"""
        assert "**No spec in the repo.**" in section("Self-Review Checklist")

    def test_ac17_auth_failure_stops_the_workflow(self):
        """AC-17: Auth failure stops the workflow [NFR-1]"""
        assert "If `gh auth status` fails, stop and ask the user to run `! gh auth login`" in SKILL

    def test_ac18_label_setup_is_idempotent(self, gh):
        """AC-18: Label setup is idempotent [NFR-2]"""
        block = bash_block(phase(0), MARK_IN_PROGRESS)
        first, second = gh.run(block), gh.run(block)
        assert (first.returncode, second.returncode) == (0, 0), second.stderr

    def test_ac19_missing_markers_yield_empty_output(self, gh):
        """AC-19: Missing markers yield empty output [NFR-3]"""
        gh.set_body(ORIGINAL + "\n")
        gh.run(bash_block(phase(3), EXTRACT))
        assert (gh.tmp / "issue-42-spec.md").read_text() == ""

    def test_ac20_drafter_additions_are_present_and_optional(self):
        """AC-20: Drafter additions are present and optional [FR-16, FR-17]"""
        p0 = phase(0)
        assert "gh project item-edit" in p0
        assert "include the issue number" in p0

    def test_ac21_extraction_is_portable(self):
        """AC-21: Extraction is portable [NFR-4]"""
        block = bash_block(phase(3), EXTRACT)
        seds = re.findall(r"\bsed\s+([^|>\n]*)", block)
        assert seds, "no sed in the extraction block"
        for expr in seds:
            assert not re.search(r"(^|\s)-[A-Za-z]*[Er]", expr), expr
            for gnu_only in ("\\?", "\\+", "\\r"):
                assert gnu_only not in expr, f"{gnu_only} in {expr}"

    def test_ac22_stubs_are_de_keyed_before_commit(self):
        """AC-22: Stubs are de-keyed before commit [FR-18]"""
        p4 = phase(4)
        assert "Rename each stub after the behavior it checks" in p4
        assert "remove spec IDs (`FR-N`, `NFR-N`, `AC-N`, `EC-N`, `OS-N`) from test names, docstrings and comments before committing" in p4

    def test_ac23_self_review_greps_for_spec_ids_in_the_diff(self, tmp_path):
        """AC-23: Self-review greps for spec IDs in the diff [FR-18]"""
        checklist = section("Self-Review Checklist")
        cmd = re.search(r"`(git diff [^`]*\(FR\|NFR\|AC\|EC\|OS\)-\[0-9\]\+[^`]*)`", checklist)
        assert cmd, "no git diff | grep for spec IDs in the checklist"
        assert "no matches" in checklist
        cmd = cmd.group(1).replace("<default-branch>", "main")

        def git(*args):
            subprocess.run(["git", *args], cwd=tmp_path, check=True, capture_output=True)

        git("init", "-q", "-b", "main")
        git("-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q", "--allow-empty", "-m", "base")
        git("checkout", "-q", "-b", "work")
        (tmp_path / "t.py").write_text("def test_export_writes_header():\n    pass\n")
        git("add", ".")
        git("-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q", "-m", "clean")
        clean = subprocess.run(["bash", "-c", cmd], cwd=tmp_path, capture_output=True, text=True)
        assert clean.stdout == ""
        (tmp_path / "t.py").write_text("def test_export_writes_header():\n    # AC-3\n    pass\n")
        git("-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q", "-am", "keyed")
        keyed = subprocess.run(["bash", "-c", cmd], cwd=tmp_path, capture_output=True, text=True)
        assert "AC-3" in keyed.stdout

    def test_ac24_spec_ids_in_code_is_a_named_anti_pattern(self):
        """AC-24: Spec-IDs-in-code is a named anti-pattern [FR-18]"""
        assert re.search(r"^### \d+\. Spec IDs in Code$", section("Anti-Patterns"), re.M)

    # ── edge cases ──────────────────────────────────────────────────────────

    def test_ec1_gh_is_not_authenticated(self):
        """EC-1: gh is not authenticated"""
        assert "`! gh auth login`" in SKILL

    def test_ec2_the_issue_key_in_the_prompt_points_to_an_issue_that_doesn_t_exist(self):
        """EC-2: The issue key in the prompt points to an issue that doesn't exist"""
        p0 = phase(0)
        assert "If `gh issue view` fails" in p0
        assert "fall back to the search-and-choose flow" in p0

    def test_ec3_the_search_returns_zero_issues(self):
        """EC-3: The search returns zero issues"""
        assert "If the search found nothing, it's the recommended option." in phase(0)

    def test_ec4_the_search_returns_more_than_3_issues(self):
        """EC-4: The search returns more than 3 issues"""
        p0 = phase(0)
        assert "at most 3, best match first" in p0
        assert "*Other*" in p0

    def test_ec5_the_chosen_issue_is_closed(self):
        """EC-5: The chosen issue is closed"""
        assert "**The issue is closed:** ask before reopening" in phase(0)

    def test_ec6_the_issue_is_assigned_to_someone_else_or_already_labelled_in_progress(self):
        """EC-6: The issue is assigned to someone else or already labelled in progress"""
        assert "**Assigned to someone else, or already labelled `in progress`:**" in phase(0)

    def test_ec7_a_workflow_label_already_exists(self, gh):
        """EC-7: A workflow label already exists"""
        (gh.state / "labels").write_text("in progress\ndraft\napproved\n")
        result = gh.run(bash_block(phase(0), MARK_IN_PROGRESS))
        assert result.returncode == 0, result.stderr
        assert ["issue", "edit", "42", "--add-assignee", "@me", "--add-label", "in progress"] in gh.calls()

    def test_ec8_the_issue_body_has_no_spec_markers_yet(self, gh):
        """EC-8: The issue body has no spec markers yet"""
        gh.set_body(ORIGINAL + "\n")
        result = gh.run(bash_block(phase(3), EXTRACT))
        assert result.returncode != 0, "validating an empty spec must fail, not pass"

    def test_ec9_someone_edits_the_issue_body_while_work_is_under_way(self):
        """EC-9: Someone edits the issue body while work is under way"""
        assert "Always re-pull before validating" in phase(3)
        assert "replace only the marked block. Never drop the original description" in phase(2)

    def test_ec10_the_pr_must_target_a_branch_other_than_the_default(self):
        """EC-10: The PR must target a branch other than the default"""
        assert "the issue will **not** close automatically on merge" in phase(7)

    def test_ec11_closingissuesreferences_does_not_include_the_issue(self):
        """EC-11: closingIssuesReferences does not include the issue"""
        assert "fix the PR description (`gh pr edit --body-file ...`) and check again" in phase(7)

    def test_ec12_the_merge_did_not_close_the_issue(self):
        """EC-12: The merge did not close the issue"""
        p7 = phase(7)
        assert "If it's still open, don't close it by hand without asking" in p7
        assert "wrong base branch" in p7

    def test_ec13_the_gh_token_lacks_the_project_scope(self):
        """EC-13: The gh token lacks the project scope"""
        assert "That needs the `project` token scope. If it's missing, say so and continue." in phase(0)

    def test_ec14_the_issue_is_in_a_different_repo_from_the_pr(self):
        """EC-14: The issue is in a different repo from the PR"""
        assert "`Closes owner/repo#42`" in phase(7)

    def test_ec15_the_base_skill_is_not_in_the_plugin_cache(self, tmp_path):
        """EC-15: The base skill is not in the plugin cache"""
        assert find_base_skill(tmp_path) is None
        with pytest.raises(pytest.skip.Exception):
            original, globals()["PLUGIN_CACHE"] = PLUGIN_CACHE, tmp_path
            try:
                base_skill_or_skip()
            finally:
                globals()["PLUGIN_CACHE"] = original

    def test_ec16_the_spec_text_mentions_the_marker_strings_mid_line(self, gh):
        """EC-16: The spec text mentions the marker strings mid-line"""
        gh.set_body(
            f"{ORIGINAL}\n\n<!-- spec:start -->\n# Spec: X\n"
            "The block ends at `<!-- spec:end -->` on its own line.\nAFTER-MENTION\n"
            "<!-- spec:end -->\nTRAILER\n"
        )
        gh.run(bash_block(phase(3), EXTRACT))
        extracted = (gh.tmp / "issue-42-spec.md").read_text()
        assert "AFTER-MENTION" in extracted
        assert "TRAILER" not in extracted and ORIGINAL not in extracted

    def test_ec17_the_issue_body_has_crlf_line_endings(self, gh):
        """EC-17: The issue body has CRLF line endings"""
        gh.set_body(f"{ORIGINAL}\r\n\r\n<!-- spec:start -->\r\n# Spec: X\r\nBODY\r\n<!-- spec:end -->\r\n")
        gh.run(bash_block(phase(3), EXTRACT))
        extracted = (gh.tmp / "issue-42-spec.md").read_bytes()
        assert b"# Spec: X" in extracted and b"BODY" in extracted
        assert b"\r" not in extracted

    def test_ec18_a_spec_id_must_be_checked_in_the_code(self):
        """EC-18: A spec ID must be checked in the code"""
        assert "map each acceptance criterion to the test that covers it" in phase(7)
