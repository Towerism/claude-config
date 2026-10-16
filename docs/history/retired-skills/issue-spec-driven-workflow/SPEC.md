# Spec: Issue Spec-Driven Workflow Skill

**Author:** Martin Fracker, Jr. (requirements); drafted by Claude
**Date:** 2026-10-10
**Status:** Approved (by Martin Fracker, Jr., 2026-10-10, re-approved after the FR-18 scope change)
**Reviewers:** Martin Fracker, Jr.
**Related specs:** `engineering-advanced-skills:spec-driven-workflow` (the base skill this one extends)

---

## Context

`spec-driven-workflow` enforces spec-before-code, but it keeps the spec as a file in the repo (`spec.md`, `specs/*.md`). That splits the record of the work between the repo and the GitHub issue that requested it. Nothing in that workflow ties the work to the issue, marks the issue as being worked on, or closes it when the work ships.

This skill is `spec-driven-workflow` with a GitHub issue lifecycle added. The issue is found (or created) first and marked in progress. The spec lives only on the issue, with its status shown by `draft` and `approved` labels. The work ships in a PR whose description says `Closes #N`, so GitHub closes the issue when the PR merges into the default branch.

Evidence: the requirements come from the reviewer's requests on 2026-10-10. Request 1 (base the skill on spec-driven-workflow; add Phase 0; the issue tracks the lifecycle; spec only on the issue; the PR closes the issue). Request 2 (`draft`/`approved` labels; always offer to create a new issue when no number is given, with the prompt as its content). Request 3 (the skill itself MUST have a validated SPEC next to it). GitHub's documented behavior: closing keywords in a PR description close the linked issue only when the PR merges into the default branch.

Note on process: this spec was written after the first version of `SKILL.md`, which is anti-pattern 4 of the base skill. To make up for that, every acceptance criterion below is backed by a test in `tests/`, and `SKILL.md` was self-reviewed against this spec and fixed where it fell short. The reviewer approved this spec on 2026-10-10. It was then changed (FR-18 scoped to the target repository; AC-25 removed), and the reviewer re-approved it the same day.

---

## Functional Requirements

- FR-1: The skill MUST be installed at `~/.claude/skills/issue-spec-driven-workflow/`, and its `SKILL.md` frontmatter `name` MUST be `issue-spec-driven-workflow`.
- FR-2: `SKILL.md` MUST keep every line of the base skill's `SKILL.md` body, in the same order. The only exceptions are the lines in the Changed-Lines Allowlist (Data Models), which assumed a spec file in the repo. `scripts/` and `references/` MUST be byte-identical copies of the base skill's.
- FR-3: The skill MUST define a Phase 0 that comes before Phase 1 and runs before any other phase.
- FR-4: When the user's prompt contains an issue key (`#N`, `GH-N`, `issue N`, `owner/repo#N`, or an issue URL), the skill MUST use that issue.
- FR-5: When the prompt has no issue key, the skill MUST search open issues (then all issues), and MUST ask the user to choose. The question offers at most 3 candidate issues and MUST always include a `Create a new issue` option, even when there is a single clear match.
- FR-6: When the user chooses `Create a new issue`, the skill MUST build the issue title and body from the prompt or the content it references. It MUST NOT interview the user for issue content, and MUST NOT add requirements that the prompt does not contain.
- FR-7: The skill MUST ask before reopening a closed issue, and before taking over an issue that is assigned to someone else or already labelled `in progress`.
- FR-8: The skill MUST mark the chosen issue in progress by assigning it to `@me` and adding the `in progress` label. It MUST first create the `in progress`, `draft`, and `approved` labels when they are missing.
- FR-9: The spec MUST be written into the issue body between `<!-- spec:start -->` and `<!-- spec:end -->` markers, below the original issue description, which MUST be preserved. The spec MUST NOT be committed to the project repo.
- FR-10: Spec status MUST be tracked with labels. Posting or reposting the spec sets `draft` and removes `approved`. The user's explicit approval sets `approved` and removes `draft`. The issue MUST NOT carry both labels at once.
- FR-11: Implementation (Phase 4 onward) MUST NOT begin until the issue is labelled `approved`.
- FR-12: Validation (`spec_validator.py`) and test extraction (`test_extractor.py`) MUST run on a temp copy of the marked spec block, pulled from the issue into a path outside the working tree.
- FR-13: A spec change discovered during implementation MUST be made in the issue body, and MUST return the issue to `draft` until the user approves it again.
- FR-14: The PR MUST target the default branch, and its description MUST contain `Closes #N` (or `Closes owner/repo#N` for an issue in a different repo). The skill MUST verify that `closingIssuesReferences` includes N.
- FR-15: After the merge, the skill MUST NOT close the issue by hand without asking. It SHOULD confirm that the issue closed and remove the `in progress` label.
- FR-16: The skill MAY set a GitHub Project's Status field to In Progress when the issue is on a Project. *(Proposed by the drafter; approved by the reviewer.)*
- FR-17: The skill SHOULD put the issue number in the branch name. *(Proposed by the drafter; approved by the reviewer.)*
- FR-18: Spec IDs (`FR-N`, `NFR-N`, `AC-N`, `EC-N`, `OS-N`) MUST NOT appear in the committed code of the repository the skill is used in: not in test names, docstrings, comments or identifiers. The spec lives on the issue and code doesn't, so IDs in code point to a document the code doesn't carry, and they go stale. Tests MUST be named for the behavior they check. Stubs from `test_extractor.py` (which puts IDs into names and docstrings) MUST be renamed and stripped of IDs before they are committed. Spec IDs MAY appear in the PR description and in issue comments, which live on GitHub next to the spec.

---

## Non-Functional Requirements

### Security

- NFR-1: The skill MUST perform all GitHub operations through the `gh` CLI with the user's existing authentication. It MUST NOT handle or store tokens. When `gh auth status` fails, it MUST stop and ask the user to run `! gh auth login`.

### Reliability

- NFR-2: The label-setup commands MUST be idempotent. Running Phase 0's mark-in-progress block twice MUST exit 0 both times.
- NFR-3: The spec-extraction pipeline MUST produce empty output, not the whole issue body, when the body has no spec markers.
- NFR-4: The spec-extraction pipeline MUST use only POSIX `tr` and `sed` features, so it runs with both GNU and BSD/macOS sed.

### Maintainability

- NFR-5: The spec, the skill and its tests MUST live in the skill directory. The test suite MUST run in under 10 seconds with no network access.

---

## Acceptance Criteria

### AC-1: Installed under the right name (FR-1)
Given the directory `~/.claude/skills/issue-spec-driven-workflow/`
When the frontmatter of its `SKILL.md` is parsed
Then `name` equals `issue-spec-driven-workflow`
And `description` mentions an issue

### AC-2: Base skill text preserved (FR-2)
Given the base skill's `SKILL.md` from the plugin cache
When its body lines are compared with this skill's `SKILL.md`
Then every base line outside the Changed-Lines Allowlist appears in this skill's `SKILL.md`, in the same relative order

### AC-3: Scripts and references copied unchanged (FR-2)
Given the base skill's `scripts/` and `references/`
When each file is compared with this skill's copy
Then the bytes are identical

### AC-4: Phase 0 precedes Phase 1 (FR-3)
Given `SKILL.md`
When the phase headings are located
Then `### Phase 0` appears before `### Phase 1`

### AC-5: All issue key forms recognised (FR-4)
Given Phase 0 of `SKILL.md`
When its text is read
Then it lists `#42`, `GH-42`, `issue 42`, `owner/repo#42` and an `https://github.com/owner/repo/issues/42` URL

### AC-6: No key means the user chooses, with a create option (FR-5)
Given Phase 0 of `SKILL.md`
When its text is read
Then it instructs a search with `gh issue list --state open` followed by `--state all`
And it instructs AskUserQuestion with at most 3 candidates
And it requires a `Create a new issue` option that is offered always, even for a single clear match

### AC-7: New issue built from the prompt (FR-6)
Given Phase 0 of `SKILL.md`
When its text is read
Then it instructs `gh issue create` with a title and a body built from the prompt or the content it references
And it says not to interview the user for issue content
And it says not to add requirements

### AC-8: Ask before reopening or taking over (FR-7)
Given Phase 0 of `SKILL.md`
When its text is read
Then it says to ask before `gh issue reopen`
And it says to ask before taking over an issue assigned to someone else or labelled `in progress`

### AC-9: Mark in progress creates the labels and assigns (FR-8)
Given a fake `gh` that records every call
When Phase 0's mark-in-progress block runs
Then `gh label create` was called for `in progress`, `draft` and `approved`
And `gh issue edit 42 --add-assignee @me --add-label in progress` was called
And the block exits 0

### AC-10: Spec block extracted from the issue body (FR-9, FR-12)
Given a fake `gh` whose issue body holds an original description followed by a marked spec block
When Phase 3's extraction pipeline runs
Then the output contains the spec block
And the output does not contain the original description

### AC-11: Draft and approved labels swap (FR-10)
Given a fake `gh` that records every call
When the draft block and then the approve block from Phase 2 run
Then the draft block adds `draft` and removes `approved`
And the approve block adds `approved` and removes `draft`

### AC-12: Implementation gated on the approved label (FR-11)
Given `SKILL.md`
When Phase 2 is read
Then it states that implementation waits for the `approved` label

### AC-13: Spec changes go back to draft (FR-13)
Given Phase 5 of `SKILL.md`
When its rules are read
Then a missing requirement leads to editing the issue body and relabelling `draft` until re-approved

### AC-14: PR closes the issue (FR-14)
Given Phase 7 of `SKILL.md`
When its text is read
Then it requires a `Closes #42` line in the PR description
And it looks up the default branch with `defaultBranchRef`
And it verifies `closingIssuesReferences`

### AC-15: No manual close after merge (FR-15)
Given Phase 7 of `SKILL.md`
When its after-merge step is read
Then it says not to close the issue by hand without asking
And it removes the `in progress` label

### AC-16: No spec file in the repo (FR-9)
Given the Self-Review Checklist of `SKILL.md`
When it is read
Then it contains a "No spec in the repo" item

### AC-17: Auth failure stops the workflow (NFR-1)
Given `SKILL.md`
When it is read
Then it instructs stopping and asking the user to run `! gh auth login` when `gh auth status` fails

### AC-18: Label setup is idempotent (NFR-2)
Given a fake `gh` whose `label create` fails when the label already exists
When Phase 0's mark-in-progress block runs twice
Then both runs exit 0

### AC-19: Missing markers yield empty output (NFR-3)
Given a fake `gh` whose issue body has no spec markers
When Phase 3's extraction pipeline runs
Then the output is empty

### AC-20: Drafter additions are present and optional (FR-16, FR-17)
Given Phase 0 of `SKILL.md`
When it is read
Then it mentions `gh project item-edit` for the Project Status
And it says to include the issue number in the branch name

### AC-21: Extraction is portable (NFR-4)
Given Phase 3's extraction pipeline
When its `sed` and `tr` expressions are inspected
Then they use no GNU-only escapes (`\?`, `\+`, `\r` in a regex, or `-E`/`-r`)

### AC-22: Stubs are de-keyed before commit (FR-18)
Given Phase 4 of `SKILL.md`
When its text is read
Then it instructs renaming the extracted test stubs after the behavior they check
And it instructs removing spec IDs from test names, docstrings and comments before committing

### AC-23: Self-review greps for spec IDs in the diff (FR-18)
Given the Self-Review Checklist of `SKILL.md`
When it is read
Then it contains an item that runs `git diff` against the default branch through a grep for `(FR|NFR|AC|EC|OS)-[0-9]+`
And it expects no matches

### AC-24: Spec-IDs-in-code is a named anti-pattern (FR-18)
Given the Anti-Patterns section of `SKILL.md`
When it is read
Then it contains an anti-pattern for spec IDs in code

---

## Edge Cases

- EC-1: `gh` is not authenticated → Stop and ask the user to run `! gh auth login`. No GitHub writes.
- EC-2: The issue key in the prompt points to an issue that doesn't exist → `gh issue view` fails. Tell the user, then fall back to the search-and-choose flow (which offers `Create a new issue`).
- EC-3: The search returns zero issues → Still ask, with `Create a new issue` as the recommended option.
- EC-4: The search returns more than 3 issues → Offer the best 3 plus `Create a new issue`. The user can type any number through the *Other* answer.
- EC-5: The chosen issue is closed → Ask before `gh issue reopen`.
- EC-6: The issue is assigned to someone else or already labelled `in progress` → Ask before taking it over.
- EC-7: A workflow label already exists → `gh label create` fails, the failure is ignored, and the block still exits 0.
- EC-8: The issue body has no spec markers yet → Extraction is empty. Write the spec into the issue before validating.
- EC-9: Someone edits the issue body while work is under way → Re-pull before validating. Replace only the marked block, and never drop the original description.
- EC-10: The PR must target a branch other than the default → Warn the user that the issue will not close on merge.
- EC-11: `closingIssuesReferences` does not include the issue → Fix the PR description with `gh pr edit` and check again.
- EC-12: The merge did not close the issue → Report why (wrong base branch, missing or malformed keyword) and ask. Do not close it by hand.
- EC-13: The `gh` token lacks the `project` scope → Skip the Project Status update, tell the user, and continue.
- EC-14: The issue is in a different repo from the PR → Use `Closes owner/repo#N`.
- EC-15: The base skill is not in the plugin cache (tests only) → The AC-2 and AC-3 tests skip with a reason. They don't pass silently.
- EC-16: The spec text mentions the marker strings mid-line (as this spec does) → Only a line that is exactly a marker starts or ends the block. Extraction MUST NOT stop early.
- EC-17: The issue body has CRLF line endings (common after edits in the GitHub web UI) → Strip `\r` before matching the markers.
- EC-18: A spec ID must be checked in the code, for example to confirm coverage → Do it outside the code: the self-review maps each AC to the test that covers it in the PR description or an issue comment, never in the code.

---

## API Contracts

The skill drives GitHub through `gh`. These are the GitHub REST equivalents of the calls it makes. `gh` may use GraphQL internally, but the fields shown are what the skill reads or writes.

```typescript
interface Issue {
  number: number;
  title: string;
  body: string;                       // original description + optional spec block
  state: "OPEN" | "CLOSED";
  stateReason?: "COMPLETED" | "NOT_PLANNED" | "REOPENED";
  labels: { name: string }[];
  assignees: { login: string }[];
  url: string;
}

interface CreateIssueRequest { title: string; body: string; }
interface EditIssueRequest {
  body?: string;
  addLabels?: string[];
  removeLabels?: string[];
  addAssignees?: ("@me" | string)[];
}
interface PullRequest {
  number: number;
  baseRefName: string;                // MUST equal the default branch
  body: string;                       // MUST contain "Closes #N"
  closingIssuesReferences: { number: number }[];
}
interface GhError { exitCode: number; stderr: string; }  // non-zero exit
```

| Operation | gh command | REST equivalent | Success | Errors |
|-----------|-----------|-----------------|---------|--------|
| Read issue | `gh issue view N --json ...` | GET /repos/{owner}/{repo}/issues/{N} | `Issue` | 404 not found (EC-2), 401 auth (EC-1) |
| Search | `gh issue list --search ...` | GET /search/issues?q=... | `Issue[]` (may be empty, EC-3) | 401, 422 bad query |
| Create issue | `gh issue create` | POST /repos/{owner}/{repo}/issues | `Issue` (URL printed) | 401, 410 issues disabled |
| Edit issue | `gh issue edit N ...` | PATCH /repos/{owner}/{repo}/issues/{N} | updated `Issue` | 404, 401, 403 no write access |
| Create label | `gh label create NAME` | POST /repos/{owner}/{repo}/labels | label | 422 already exists (EC-7, ignored) |
| Reopen | `gh issue reopen N` | PATCH /repos/{owner}/{repo}/issues/{N} `{state:"open"}` | `Issue` | 403 |
| Create PR | `gh pr create --base B` | POST /repos/{owner}/{repo}/pulls | `PullRequest` | 422 no commits / exists |
| Check links | `gh pr view --json closingIssuesReferences` | GraphQL `closingIssuesReferences` | `PullRequest` | 404 |
| Default branch | `gh repo view --json defaultBranchRef` | GET /repos/{owner}/{repo} | `{defaultBranchRef:{name}}` | 404 |

---

## Data Models

### Issue body layout

| Part | Type | Constraints |
|------|------|-------------|
| Original description | markdown | MUST be preserved verbatim when the spec is added or replaced |
| `<!-- spec:start -->` | marker line | Exactly one; begins the spec block |
| Spec | markdown | All 9 sections of the base spec format |
| `<!-- spec:end -->` | marker line | Exactly one; ends the spec block |

### Workflow labels

| Label | Color | Meaning | Constraints |
|-------|-------|---------|-------------|
| `in progress` | FBCA04 | Work has started | Added in Phase 0. Removed after the issue closes. |
| `draft` | D4C5F9 | Spec written, awaiting approval | Mutually exclusive with `approved` |
| `approved` | 0E8A16 | Spec approved, ready to implement | Mutually exclusive with `draft`. Required before Phase 4. |

### Temp files

| File | Location | Constraints |
|------|----------|-------------|
| `issue-N-body.md` | `$CLAUDE_JOB_DIR/tmp/` or `/tmp/` | Outside the working tree; never committed |
| `issue-N-spec.md` | same | Re-pulled before every validation |
| `new-issue-body.md` | same | Built from the prompt (FR-6) |

### Changed-Lines Allowlist (FR-2)

These base-skill lines were rewritten because they assumed a spec file in the repo or a 6-phase workflow:

| Base line | Reason |
|-----------|--------|
| `name: "spec-driven-workflow"` | Renamed (FR-1) |
| `description: "Use when the user asks to write specs before code, ...` | New trigger description |
| `# Spec-Driven Workflow — POWERFUL` | Renamed title |
| `## Workflow — 6 Phases` | Phase 0 and Phase 7 added |
| Phase 2 exit criteria line | Adds "in the issue body" |
| `Run \`spec_validator.py\` against the spec file:` | The spec comes from the issue |
| `python spec_validator.py --file spec.md --strict` | Temp file pulled from the issue |
| `python test_extractor.py --file spec.md --framework pytest --output tests/` | Same |
| `- If you discover a missing requirement, STOP and update the spec first.` | Issue body plus `draft` relabel (FR-13) |
| `Phase 2: Write Spec` (TDD diagram) | "(on issue)" |
| `python spec_validator.py --file specs/auth.md --strict` | Temp file pulled from the issue |
| `python test_extractor.py --file specs/auth.md ...` | Same |

---

## Out of Scope

- OS-1: Live agent-behavior evals (running Claude against the skill and grading what it does) — The tests check that the instructions and shell snippets are correct, not that a model follows them. Could be added later with `engineering-advanced-skills:skill-tester` or `claude plugin eval`.
- OS-2: Trackers other than GitHub (GitLab, Jira, Linear) — Only GitHub was requested.
- OS-3: Closing the issue through the API — GitHub closes it from the PR's closing keyword, as requested.
- OS-4: Changing the base skill or its scripts — The new skill starts "100% the same" as the base (FR-2).
- OS-5: Storing the spec in an issue comment — The body is a single, editable place that the extraction pipeline reads. Comments would fragment it.
- OS-6: Several issues per run — One issue per piece of work, as the skill's rule says.
- OS-7: Version-controlling the skill directory — `~/.claude/skills/` is not a repo, and nothing requested putting it in one.
- OS-8: Keeping spec IDs out of this skill's own tests — FR-18 governs the repositories the skill is used in. This skill's spec lives next to its tests in the skill directory, so its tests MAY name spec IDs.
