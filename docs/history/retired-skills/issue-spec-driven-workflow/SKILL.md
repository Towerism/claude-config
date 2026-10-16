---
name: "issue-spec-driven-workflow"
description: "Spec-first development tracked on a GitHub issue. Use when the user asks to work an issue (\"#42\", \"GH-42\", an issue URL, or \"the issue about X\") spec-first, or wants spec-driven work whose spec lives on the issue instead of in the repo. Finds the issue (or offers to create one from the prompt), marks it in progress, tracks spec status with draft/approved labels, writes and validates the spec in the issue body, implements test-first, and opens a PR that closes the issue on merge."
---

# Issue Spec-Driven Workflow — POWERFUL

> This is `spec-driven-workflow` plus a **GitHub issue lifecycle**: a Phase 0 that finds the issue and marks it in progress, a spec that lives **only on the issue**, and a Phase 7 that opens a PR that closes the issue when it merges. Everything else is the same as `spec-driven-workflow`.

## Overview

Spec-driven workflow enforces a single, non-negotiable rule: **write the specification BEFORE you write any code.** Not alongside. Not after. Before.

This is not documentation. This is a contract. A spec defines what the system MUST do, what it SHOULD do, and what it explicitly WILL NOT do. Every line of code you write traces back to a requirement in the spec. Every test traces back to an acceptance criterion. If it is not in the spec, it does not get built.

### Why Spec-First Matters

1. **Eliminates rework.** 60-80% of defects originate from requirements, not implementation. Catching ambiguity in a spec costs minutes; catching it in production costs days.
2. **Forces clarity.** If you cannot write what the system should do in plain language, you do not understand the problem well enough to write code.
3. **Enables parallelism.** Once a spec is approved, frontend, backend, QA, and documentation can all start simultaneously.
4. **Creates accountability.** The spec is the definition of done. No arguments about whether a feature is "complete" — either it satisfies the acceptance criteria or it does not.
5. **Feeds TDD directly.** Acceptance criteria in Given/When/Then format translate 1:1 into test cases. The spec IS the test plan.

### The Iron Law

```
NO CODE WITHOUT AN APPROVED SPEC.
NO EXCEPTIONS. NO "QUICK PROTOTYPES." NO "I'LL DOCUMENT IT LATER."
```

If the spec is not written, reviewed, and approved, implementation does not begin. Period.

### The Issue Is the Source of Truth

```
ONE ISSUE PER PIECE OF WORK. THE SPEC LIVES ON THE ISSUE. NEVER IN THE REPO.
```

- The spec is written into the **issue body**, never committed: no `specs/`, `docs/specs/`, `SPEC.md`, or spec comments in code. The validator and test extractor read a temporary copy pulled from the issue, kept outside the working tree.
- The issue tracks the lifecycle: **found or created → `in progress` → spec `draft` → spec `approved` → PR open → closed by the merge**.
- If the spec changes at any point (a missing requirement, a scope decision), update the **issue body** first, then the code.
- All GitHub actions use the `gh` CLI. If `gh auth status` fails, stop and ask the user to run `! gh auth login`.

---

## The Spec Format

Every spec follows this structure. No sections are optional — if a section does not apply, write "N/A — [reason]" so reviewers know it was considered, not forgotten.

### Mandatory Sections

| # | Section | Key Rules |
|---|---------|-----------|
| 1 | **Title and Metadata** | Author, date, status (Draft/In Review/Approved/Superseded), reviewers |
| 2 | **Context** | Why this feature exists. 2-4 paragraphs with evidence (metrics, tickets). |
| 3 | **Functional Requirements** | RFC 2119 keywords (MUST/SHOULD/MAY). Numbered FR-N. Each is atomic and testable. |
| 4 | **Non-Functional Requirements** | Performance, security, accessibility, scalability, reliability — all with measurable thresholds. |
| 5 | **Acceptance Criteria** | Given/When/Then format. Every AC references at least one FR-* or NFR-*. |
| 6 | **Edge Cases** | Numbered EC-N. Cover failure modes for every external dependency. |
| 7 | **API Contracts** | TypeScript-style interfaces. Cover success and error responses. |
| 8 | **Data Models** | Table format with field, type, constraints. Every entity from requirements must have a model. |
| 9 | **Out of Scope** | Explicit exclusions with reasons. Prevents scope creep during implementation. |

### RFC 2119 Keywords

| Keyword | Meaning |
|---------|---------|
| **MUST** | Absolute requirement. Non-conformant without it. |
| **MUST NOT** | Absolute prohibition. |
| **SHOULD** | Recommended. Omit only with documented justification. |
| **MAY** | Optional. Implementer's discretion. |

See [spec_format_guide.md](references/spec_format_guide.md) for the complete template with section-by-section examples, good/bad requirement patterns, and feature-type templates (CRUD, Integration, Migration).

See [acceptance_criteria_patterns.md](references/acceptance_criteria_patterns.md) for a full pattern library of Given/When/Then criteria across authentication, CRUD, search, file upload, payment, notification, and accessibility scenarios.

---

## Bounded Autonomy Rules

These rules define when an agent (human or AI) MUST stop and ask for guidance vs. when they can proceed independently.

### STOP and Ask When:

1. **Scope creep detected.** The implementation requires something not in the spec. Even if it seems obviously needed, STOP. The spec might have excluded it deliberately.

2. **Ambiguity exceeds 30%.** If you cannot determine the correct behavior from the spec for more than 30% of a given requirement, the spec is incomplete. Do not guess.

3. **Breaking changes required.** The implementation would change an existing API contract, database schema, or public interface. Always escalate.

4. **Security implications.** Any change that touches authentication, authorization, encryption, or PII handling requires explicit approval.

5. **Performance characteristics unknown.** If a requirement says "MUST complete in < 500ms" but you have no way to measure or guarantee that, escalate before implementing a guess.

6. **Cross-team dependencies.** If the spec requires coordination with another team or service, confirm the dependency before building against it.

### Continue Autonomously When:

1. **Spec is clear and unambiguous** for the current task.
2. **All acceptance criteria have passing tests** and you are refactoring internals.
3. **Changes are non-breaking** — no public API, schema, or behavior changes.
4. **Implementation is a direct translation** of a well-defined acceptance criterion.
5. **Error handling follows established patterns** already documented in the codebase.

### Escalation Protocol

When you must stop, provide:

```markdown
## Escalation: [Brief Title]

**Blocked on:** [requirement ID, e.g., FR-3]
**Question:** [Specific, answerable question — not "what should I do?"]
**Options considered:**
  A. [Option] — Pros: [...] Cons: [...]
  B. [Option] — Pros: [...] Cons: [...]
**My recommendation:** [A or B, with reasoning]
**Impact of waiting:** [What is blocked until this is resolved?]
```

Never escalate without a recommendation. Never present an open-ended question. Always give options.

See `references/bounded_autonomy_rules.md` for the complete decision matrix.

---

## Workflow — 8 Phases (0–7)

### Phase 0: Find the Issue and Mark It In Progress

**Goal:** Tie the work to exactly one GitHub issue before anything else happens.

1. **Look for a key in the user's prompt.** Accept any of:
   - `#42`, `GH-42`, `issue 42`, `issue #42`
   - `owner/repo#42`
   - `https://github.com/owner/repo/issues/42`

   If found, load it:
   ```bash
   gh issue view 42 --json number,title,body,state,labels,assignees,url
   ```
   If `gh issue view` fails (no such issue, or no access), tell the user and fall back to the search-and-choose flow below (steps 2–3).
2. **No key? Search.** Build keywords from the request and search open issues first:
   ```bash
   gh issue list --state open --search "<keywords> in:title,body" --limit 10 \
     --json number,title,labels,assignees,updatedAt
   ```
   If nothing turns up, retry with fewer or different keywords, then with `--state all`.
3. **No key: always let the user choose, and always offer a new issue.** Don't pick an issue yourself, even when there's exactly one clear match. Ask with AskUserQuestion:
   - one option per candidate (`#42 — <title>`), at most 3, best match first (the user can name any other issue through the *Other* answer);
   - **always** a `Create a new issue` option, even when the search found good matches. If the search found nothing, it's the recommended option.
4. **Creating a new issue:** the user's prompt *is* the issue content, or it points to it (a file, a pasted block, a link, an earlier message). Don't interview the user for issue content. Build the issue from what the prompt has or references:
   - **Title:** a short imperative summary of the request.
   - **Body:** the request, faithfully restated with its details (or the referenced content), without adding requirements. The spec is added later in Phase 2.
   ```bash
   gh issue create --title "<title>" --body-file /tmp/new-issue-body.md
   ```
   Use the number it returns and give the user its URL.
5. **Resolve the chosen issue** (applies to a key from the prompt too):
   - **The issue is closed:** ask before reopening (`gh issue reopen 42`).
   - **Assigned to someone else, or already labelled `in progress`:** ask before taking it over.
6. **Mark it in progress:**
   ```bash
   # create the workflow's labels once per repo (no-op if they exist)
   gh label create "in progress" --color FBCA04 --description "Work has started" 2>/dev/null || true
   gh label create "draft"       --color D4C5F9 --description "Spec written, awaiting approval" 2>/dev/null || true
   gh label create "approved"    --color 0E8A16 --description "Spec approved, ready to implement" 2>/dev/null || true
   gh issue edit 42 --add-assignee @me --add-label "in progress"
   ```
   If `gh issue view 42 --json projectItems` shows the issue is on a GitHub Project with a Status field, also set Status to *In Progress* with `gh project item-edit`. That needs the `project` token scope. If it's missing, say so and continue.
7. **Branch naming:** when you create a branch for this work, include the issue number (e.g. `42-short-slug`).

**Exit criteria:** One issue is identified, assigned, and labelled `in progress`. Its number is known for every phase that follows.

### Phase 1: Gather Requirements

**Goal:** Understand what needs to be built and why.

0. **Read the issue first:** its body, its comments (`gh issue view 42 --comments`), and any linked issues or PRs. Ask only what the issue doesn't already answer.
1. **Interview the user.** Ask:
   - What problem does this solve?
   - Who are the users?
   - What does success look like?
   - What explicitly should NOT be built?
2. **Read existing code.** Understand the current system before proposing changes.
3. **Identify constraints.** Performance budgets, security requirements, backward compatibility.
4. **List unknowns.** Every unknown is a risk. Surface them now, not during implementation.

**Exit criteria:** You can explain the feature to someone unfamiliar with the project in 2 minutes.

### Phase 2: Write Spec

**Goal:** Produce a complete spec document following The Spec Format above.

1. Fill every section of the template. No section left blank.
2. Number all requirements (FR-*, NFR-*, AC-*, EC-*, OS-*).
3. Use RFC 2119 keywords precisely.
4. Write acceptance criteria in Given/When/Then format.
5. Define API contracts with TypeScript-style types.
6. List explicit exclusions in Out of Scope.
7. **Write the spec into the issue body, not into the repo.** Keep the original issue text and put the spec below it, between markers. Each marker sits alone on its own line, and only whole-line markers count, so the spec may mention them mid-line:
   ```markdown
   <original issue description, unchanged>

   <!-- spec:start -->
   # Spec: <title>
   ...all 9 sections...
   <!-- spec:end -->
   ```
   Draft it in a temp file outside the working tree (`$CLAUDE_JOB_DIR/tmp/` in a background job, otherwise `/tmp/`), then publish it:
   ```bash
   gh issue view 42 --json body -q .body > /tmp/issue-42-body.md   # start from the current body
   # ...add or replace the spec:start/spec:end block in that file...
   gh issue edit 42 --body-file /tmp/issue-42-body.md
   ```
   When you replace the spec, replace only the marked block. Never drop the original description.
8. **Approval lives on the issue too, as labels.** Labels are the spec's status. Keep the metadata `Status:` line in the body in sync with them.
   - When the spec is first posted (or reposted after a change), label the issue `draft`:
     ```bash
     gh issue edit 42 --add-label draft --remove-label approved
     ```
   - When the user explicitly approves, swap to `approved`:
     ```bash
     gh issue edit 42 --add-label approved --remove-label draft
     ```
   - The issue never has both labels at once. Implementation (Phase 4 on) waits for `approved`. Check with `gh issue view 42 --json labels -q '.labels[].name'`.

**Exit criteria:** The spec can be handed to a developer who was not in the requirements meeting, and they can implement the feature without asking clarifying questions. It is in the issue body, and no spec file exists in the repo.

### Phase 3: Validate Spec

**Goal:** Verify the spec is complete, consistent, and implementable.

Pull the spec from the issue into a temp file, then run `spec_validator.py` against it. The scripts live in this skill's `scripts/` directory.

```bash
gh issue view 42 --json body -q .body | tr -d '\r' \
  | sed -n '/^<!-- spec:start -->$/,/^<!-- spec:end -->$/p' > /tmp/issue-42-spec.md
python3 ~/.claude/skills/issue-spec-driven-workflow/scripts/spec_validator.py --file /tmp/issue-42-spec.md --strict
```

Fix problems in the **issue body**, then pull and validate again. Always re-pull before validating: someone may have edited the issue.

Manual validation checklist:
- [ ] Every functional requirement has at least one acceptance criterion
- [ ] Every acceptance criterion is testable (no subjective language)
- [ ] API contracts cover all endpoints mentioned in requirements
- [ ] Data models cover all entities mentioned in requirements
- [ ] Edge cases cover failure modes for every external dependency
- [ ] Out of scope is explicit about what was considered and rejected
- [ ] Non-functional requirements have measurable thresholds

**Exit criteria:** Spec scores 80+ on validator, and all manual checklist items pass.

### Phase 4: Generate Tests

**Goal:** Extract test cases from acceptance criteria before writing implementation code.

Run `test_extractor.py` against the approved spec:

```bash
python3 ~/.claude/skills/issue-spec-driven-workflow/scripts/test_extractor.py --file /tmp/issue-42-spec.md --framework pytest --output tests/
```

(`--file` is the temp copy pulled from the issue in Phase 3. Re-pull it if the issue changed. Use the framework the project uses.)

1. Each acceptance criterion becomes one or more test cases.
2. Each edge case becomes a test case.
3. Tests are stubs — they define the assertion but not the implementation.
4. All tests MUST fail initially (red phase of TDD).
5. **Strip spec IDs before anything is committed.** `test_extractor.py` puts IDs into names and docstrings (`test_ac3_...`, `"""AC-3: ... [FR-2]"""`). The spec lives on the issue, not in the repo, so IDs in code point to a document the code doesn't carry, and they go stale. Rename each stub after the behavior it checks (e.g. `test_export_rejects_empty_roster`), and remove spec IDs (`FR-N`, `NFR-N`, `AC-N`, `EC-N`, `OS-N`) from test names, docstrings and comments before committing. Keep the AC → test mapping out of the code; it goes in the PR description (Phase 7).

**Exit criteria:** You have a test file where every test fails with "not implemented" or equivalent.

### Phase 5: Implement

**Goal:** Write code that makes failing tests pass, one acceptance criterion at a time.

1. Pick one acceptance criterion (start with the simplest).
2. Make its test(s) pass with minimal code.
3. Run the full test suite — no regressions.
4. Commit.
5. Pick the next acceptance criterion. Repeat.

**Rules:**
- Do NOT implement anything not in the spec.
- Do NOT optimize before all acceptance criteria pass.
- Do NOT refactor before all acceptance criteria pass.
- If you discover a missing requirement, STOP and update the spec first. That means: edit the spec block in the **issue body**, set the label back to `draft` (`--add-label draft --remove-label approved`), get the user's approval (back to `approved`), then re-pull.

**Exit criteria:** All tests pass. All acceptance criteria satisfied.

### Phase 6: Self-Review

**Goal:** Verify implementation matches spec before marking done.

Run through the Self-Review Checklist below. If any item fails, fix it before declaring the task complete.

### Phase 7: Open the PR That Closes the Issue

**Goal:** Ship the work in a PR that references the issue, so GitHub closes the issue when the PR merges.

1. **Find the default branch.** GitHub only auto-closes issues when the PR merges into it:
   ```bash
   gh repo view --json defaultBranchRef -q .defaultBranchRef.name
   ```
   If the PR must target a different base, tell the user that the issue will **not** close automatically on merge.
2. **Push and open the PR.** Its description MUST contain a closing keyword with the issue number on its own line:
   ```markdown
   Closes #42
   ```
   - Use `Closes owner/repo#42` when the issue is in a different repo from the PR.
   - Give each issue its own keyword (`Closes #42` / `Closes #43`). `Closes #42, #43` closes only #42.
   - The keyword goes in the **description**. A keyword in the title does nothing.
   - Summarize the change against the spec's FR/AC IDs, and point to the spec on the issue (`Spec: #42`) instead of copying it into the PR.
   - In the description, map each acceptance criterion to the test that covers it (`AC-3 → test_export_rejects_empty_roster`). This is the only place the AC → test mapping lives, never in the code.
   ```bash
   gh pr create --base <default-branch> --title "<title> (#42)" --body-file /tmp/pr-42-body.md
   ```
   Follow the user's or the repo's rules on draft vs ready PRs and attribution lines.
3. **Verify that GitHub linked the PR:**
   ```bash
   gh pr view --json closingIssuesReferences -q '.closingIssuesReferences[].number'
   ```
   The output MUST include `42`. If it doesn't, fix the PR description (`gh pr edit --body-file ...`) and check again.
4. **After the merge (if you're still around for it):** confirm the issue closed on its own:
   ```bash
   gh issue view 42 --json state,stateReason   # expect CLOSED / COMPLETED
   gh issue edit 42 --remove-label "in progress"
   ```
   If it's still open, don't close it by hand without asking. Report why the link didn't fire (wrong base branch, missing or malformed keyword) and let the user decide.

**Exit criteria:** The PR is open against the default branch, and `closingIssuesReferences` includes the issue. Once merged, the issue closes automatically.

---

## Self-Review Checklist

Before marking any implementation as done, verify ALL of the following:

- [ ] **Every acceptance criterion has a passing test.** No exceptions. If AC-3 exists, a test for AC-3 exists and passes.
- [ ] **Every edge case has a test.** EC-1 through EC-N all have corresponding test cases.
- [ ] **No scope creep.** The implementation does not include features not in the spec. If you added something, either update the spec or remove it.
- [ ] **API contracts match implementation.** Request/response shapes in code match the spec exactly. Field names, types, status codes — all of it.
- [ ] **Error scenarios tested.** Every error response defined in the spec has a test that triggers it.
- [ ] **Non-functional requirements verified.** If the spec says < 500ms, you have evidence (benchmark, load test, profiling) that it meets the threshold.
- [ ] **Data model matches.** Database schema matches the spec. No extra columns, no missing constraints.
- [ ] **Out-of-scope items not built.** Double-check that nothing from the Out of Scope section leaked into the implementation.
- [ ] **No spec in the repo.** `git diff <default-branch>...HEAD --stat` shows no spec file. The spec exists only in the issue body.
- [ ] **No spec IDs in the code.** `git diff <default-branch>...HEAD -U0 | grep -E '^\+' | grep -nE '(FR|NFR|AC|EC|OS)-[0-9]+'` prints no matches. Rename or reword anything it finds (Phase 4, step 5).
- [ ] **The issue spec is current.** Every spec change made during implementation is in the issue body, and the issue is labelled `approved` (not `draft`).

---

## Integration with TDD Guide

Spec-driven workflow and TDD are complementary, not competing:

```
Spec-Driven Workflow          TDD (Red-Green-Refactor)
─────────────────────         ──────────────────────────
Phase 0: Find Issue, In Progress
Phase 1: Gather Requirements
Phase 2: Write Spec (on issue)
Phase 3: Validate Spec
Phase 4: Generate Tests  ──→  RED: Tests exist and fail
Phase 5: Implement       ──→  GREEN: Minimal code to pass
Phase 6: Self-Review     ──→  REFACTOR: Clean up internals
Phase 7: PR "Closes #N"  ──→  merge closes the issue
```

**The handoff:** Spec-driven workflow produces the test stubs (Phase 4). TDD takes over from there. The spec tells you WHAT to test. TDD tells you HOW to implement.

Use `engineering-team/tdd-guide` for:
- Red-green-refactor cycle discipline
- Coverage analysis and gap detection
- Framework-specific test patterns (Jest, Pytest, JUnit)

Use `engineering/spec-driven-workflow` for:
- Defining what to build before building it
- Acceptance criteria authoring
- Completeness validation
- Scope control

---

## Examples

A complete worked example (Password Reset spec with extracted test cases) is available in [spec_format_guide.md](references/spec_format_guide.md#full-example-password-reset). It demonstrates all 9 sections, requirement numbering, acceptance criteria, edge cases, and the corresponding pytest stubs generated by `test_extractor.py`.

---

## Anti-Patterns

### 1. Coding Before Spec Approval

**Symptom:** "I'll start coding while the spec is being reviewed."
**Problem:** The review will surface changes. Now you have code that implements a rejected design.
**Rule:** Implementation does not begin until spec status is "Approved."

### 2. Vague Acceptance Criteria

**Symptom:** "The system should work well" or "The UI should be responsive."
**Problem:** Untestable. What does "well" mean? What does "responsive" mean?
**Rule:** Every acceptance criterion must be verifiable by a machine. If you cannot write a test for it, rewrite the criterion.

### 3. Missing Edge Cases

**Symptom:** Happy path is specified, error paths are not.
**Problem:** Developers invent error handling on the fly, leading to inconsistent behavior.
**Rule:** For every external dependency (API, database, file system, user input), specify at least one failure scenario.

### 4. Spec as Post-Hoc Documentation

**Symptom:** "Let me write the spec now that the feature is done."
**Problem:** This is documentation, not specification. It describes what was built, not what should have been built. It cannot catch design errors because the design is already frozen.
**Rule:** If the spec was written after the code, it is not a spec. Relabel it as documentation.

### 5. Gold-Plating Beyond Spec

**Symptom:** "While I was in there, I also added..."
**Problem:** Untested code. Unreviewed design. Potential for subtle bugs in the "bonus" feature.
**Rule:** If it is not in the spec, it does not get built. File a new spec for additional features.

### 6. Acceptance Criteria Without Requirement Traceability

**Symptom:** AC-7 exists but does not reference any FR-* or NFR-*.
**Problem:** Orphaned criteria mean either a requirement is missing or the criterion is unnecessary.
**Rule:** Every AC-* MUST reference at least one FR-* or NFR-*.

### 7. Skipping Validation

**Symptom:** "The spec looks fine, let's just start."
**Problem:** Missing sections discovered during implementation cause blocking delays.
**Rule:** Always run `spec_validator.py --strict` before starting implementation. Fix all warnings.

### 8. Committing the Spec

**Symptom:** `specs/feature.md` or `docs/spec-42.md` shows up in the diff.
**Problem:** Two copies of the spec drift apart, and the issue stops being the source of truth.
**Rule:** The spec lives only in the issue body. Temp copies stay outside the working tree.

### 9. Orphaned PR

**Symptom:** The PR says "for #42", "see #42", or puts the issue number only in the title.
**Problem:** GitHub doesn't link it as closing, so the issue stays open after the merge.
**Rule:** Put `Closes #42` in the PR description, target the default branch, and check `closingIssuesReferences`.

### 10. Silent Start

**Symptom:** Work begins, but the issue is still unassigned and unlabelled.
**Problem:** Someone else picks it up, or nobody can tell it's being worked on.
**Rule:** Phase 0 (assign + `in progress`) comes before Phase 1.

### 11. Spec IDs in Code

**Symptom:** `test_ac3_rejects_empty_roster`, `# FR-2`, or `"""AC-3: ..."""` in the diff.
**Problem:** The spec lives on the issue, not in the repo. The IDs point to a document the code doesn't carry, and they go stale as soon as the spec is renumbered.
**Rule:** Name tests for behavior, and keep the AC → test mapping in the PR description. The self-review grep must come back empty.

---

## Cross-References

- **`engineering-team/tdd-guide`** — Red-green-refactor cycle, test generation, coverage analysis. Use after Phase 4 of this workflow.
- **`engineering/focused-fix`** — Deep-dive feature repair. When a spec-driven implementation has systemic issues, use focused-fix for diagnosis.
- **`engineering/rag-architect`** — If the feature involves retrieval or knowledge systems, use rag-architect for the technical design within the spec.
- **`references/spec_format_guide.md`** — Complete template with section-by-section explanations.
- **`references/bounded_autonomy_rules.md`** — Full decision matrix for when to stop vs. continue.
- **`references/acceptance_criteria_patterns.md`** — Pattern library for writing Given/When/Then criteria.

---

## Tools

| Script | Purpose | Key Flags |
|--------|---------|-----------|
| `spec_generator.py` | Generate spec template from feature name/description | `--name`, `--description`, `--format`, `--json` |
| `spec_validator.py` | Validate spec completeness (0-100 score) | `--file`, `--strict`, `--json` |
| `test_extractor.py` | Extract test stubs from acceptance criteria | `--file`, `--framework`, `--output`, `--json` |

```bash
# Generate a spec template
python spec_generator.py --name "User Authentication" --description "OAuth 2.0 login flow"

# Pull the spec from issue #42 into a temp file (never into the repo)
gh issue view 42 --json body -q .body | tr -d '\r' \
  | sed -n '/^<!-- spec:start -->$/,/^<!-- spec:end -->$/p' > /tmp/issue-42-spec.md

# Validate a spec
python spec_validator.py --file /tmp/issue-42-spec.md --strict

# Extract test cases
python test_extractor.py --file /tmp/issue-42-spec.md --framework pytest --output tests/test_auth.py
```

The scripts are in this skill's `scripts/` directory. They only read and print, and they never touch GitHub. All issue and PR changes go through `gh`.
