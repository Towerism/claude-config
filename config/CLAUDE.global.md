# Global preferences

## Default workflow for new projects
This pipeline has produced very robust software; use it by default when starting a new project:
1. **Architecture first.** Before any feature work, use `/engineering-skills:senior-architect` to draft the architecture doc and ADRs (stack, data model, module boundaries). Have the user review them, then treat them as the source of truth; departures need a new or updated ADR.
2. **Spec every piece of work on its issue** with `/issue-spec-driven-workflow` (approval rules below: the adversarial reviewer approves by default; user-only for "manual review" issues). Draft the spec's test plan with the test skills (see "Writing code" below).
3. **Test and implement** per "Writing code" below.

## Writing code (every project, existing and new)
- When writing any code, always use the `/engineering-skills:tdd-guide` skill (red-green-refactor).
- Apply the test skills **before writing a spec's test plan**, not just before writing tests: `/engineering-skills:senior-qa` (unit, integration, E2E/browser journeys, coverage strategy) and/or `/engineering-advanced-skills:api-test-suite-builder` (HTTP API routes, contract and integration suites). Pick whichever fits each piece of the plan; use both when different pieces call for different ones.
- **Apply the test skills; loading them isn't enough.** Before any test plan or tests, read `/engineering-skills:senior-qa`'s reference guides (`references/testing_strategies.md`, `qa_best_practices.md`, `test_automation_patterns.md`), not just its SKILL.md, plus `api-test-suite-builder`'s matrices for API routes. The plan must visibly use them:
  - pyramid placement per acceptance criterion: push logic down, and keep E2E for critical journeys;
  - coverage targets set by risk;
  - each flake hazard and how it's made deterministic (gates, fake clocks, no wall-clock assertions);
  - isolation and fixtures/factories;
  - behaviour over mocks;
  - which existing tests get rewritten or deleted;
  - which criteria are review checks rather than tests.
  If asked whether a skill was used, answer honestly about how deeply it was applied.
- Load the same skill(s) again before writing the tests themselves, so the tests provide as much value as possible, aimed at the riskiest behaviour, not line coverage.

## CI/CD pipelines (every project, existing and new)
- Whenever a spec or change creates or edits a CI/CD pipeline (`.github/workflows/*`, `.gitlab-ci.yml`, deploy jobs, or the scripts they call), apply `/engineering-advanced-skills:ci-cd-pipeline-builder` **before writing the spec's pipeline plan**, not just before editing YAML.
- **Apply the skill; loading it isn't enough.** Before any pipeline plan or edit, read its reference guides (`references/pipeline-design-notes.md`, `deployment-gates.md`, and `github-actions-templates.md` or `gitlab-ci-templates.md` for the platform), not just its SKILL.md, and run `scripts/stack_detector.py` against the repo so stages come from detected stack signals, not guesswork. The plan must visibly use them:
  - stages and commands traced to detected stack signals, and each referenced command confirmed to exist in the repo;
  - gate order (lint → test → build → deploy), with deploy requiring green CI and a build artifact;
  - cache keys matched to the package manager and lockfile;
  - matrix builds only where compatibility truly requires them, with the reason stated;
  - secrets and env vars listed and documented, never embedded in YAML;
  - environment protection: staging vs. production, protected branches, and manual approval for production;
  - a rollback command or procedure for every deploy job;
  - each pitfall from `pipeline-design-notes.md` checked and either addressed or marked not applicable;
  - how the change is validated before merge (YAML parses, run locally where possible, e.g. `act`), and which items are review checks rather than automated checks.
- Load the skill again before writing the pipeline itself, and add one optimization at a time (cache, matrix, split jobs).
- If asked whether the skill was used, answer honestly about how deeply it was applied.

## Adversarial review: one pass, then raise what changed
These rules apply to every `/engineering-skills:adversarial-reviewer` review (specs and PRs). The skill requires each persona to report at least one finding, so a review never comes back empty; "no findings" is never the goal.
- **One pass, every time. No re-runs, no loop.** Run the reviewer once on the spec or changeset, handle its findings, and stop. Re-runs cost too much; don't re-run after fixing, not even after a CRITICAL. (A spec or changeset that changes later for another reason gets its own single pass.)
- **CRITICAL for a spec** means following the spec as written would cause data loss, a security hole, or an outage, or the spec contradicts the accepted architecture/ADRs, or an acceptance criterion can't be tested or implemented as written. "Could go wrong if implemented badly" is not CRITICAL.
- **Handle each finding once, by severity:**
  - CRITICAL: fix it, then raise it with the user (below).
  - WARNING: fix it, or accept it with a one-line reason in the review note.
  - NOTE: optional. Fix only if trivial; otherwise list it in the note.
- **Quota findings aren't CRITICAL.** A finding raised only to meet the skill's at-least-one rule (e.g. "the most fragile assumption" on otherwise sound work) is at most a WARNING, and the promotion rule (2+ personas → one level up) never promotes a finding to CRITICAL unless it would be CRITICAL on its own.
- **Raise it with the user when either is true:** (a) the review had a CRITICAL you fixed, or (b) the review led to any change in the spec (from a spec review, or a PR review that sent a change back into the spec). Post the review note, say what was CRITICAL, what you changed and why (quote the spec change), then stop and wait for the user's go-ahead before approving the spec / implementing, or before opening the PR. If neither is true, carry on without asking.

## Specs: adversarial-review approval by default, user approval for "manual review", self-approval for "no review"
- **Adversarial review first, unless "no review".** Every generated or revised spec must be reviewed with `/engineering-skills:adversarial-reviewer` before it's approved or presented, following the one-pass rules above. Keep a short note of what the review found and how each finding was handled (fixed, accepted with reason, or noted).
- **Default (no "manual review"): the adversarial review is the approval.** After the single `/engineering-skills:adversarial-reviewer` pass on the current spec: if it had no CRITICAL and didn't change the spec, apply the `approved` label/status yourself, post the review note on the issue, and go on to implement without asking. If it had a CRITICAL you fixed, or changed the spec, raise it with the user (above) and approve only after their go-ahead.
- **"Manual review" issues: only the user approves.** If the user says "manual review" for an issue, never self-approve its spec:
  - A spec is approved only when the user explicitly says so in their own words; until then it stays a draft.
  - Don't mark it approved yourself (no `approved` label, status field, checkbox or "Approved" heading), and don't treat silence, a passing validation, or a reviewer verdict as approval.
  - Don't implement against the draft. Present it with the review note, then wait for the user.
  - Any change at all to the spec after approval, however small, sends it back to draft and needs the user's explicit approval again.
- **"No review" sessions: self-approve without the adversarial reviewer.** If the user says "no review", then for the rest of that session skip `/engineering-skills:adversarial-reviewer` on specs: approve each spec yourself (apply the `approved` label/status, note on the issue that it was self-approved under "no review") and go on to implement. It lasts only for that session, and "manual review" on a specific issue still wins: that spec waits for the user.
- In the default and "manual review" modes, any change to an approved spec sends it back through adversarial review before it's re-approved; under "no review" you re-approve it yourself.

## PRs: review before opening, ship-gate before merging
- **Before opening a PR**, review the full changeset (diff against the base branch) with `/engineering-skills:adversarial-reviewer`, once, following the one-pass rules above: fix CRITICALs, fix or accept WARNINGs, and if a CRITICAL was fixed or the spec changed, raise it with the user and wait for their go-ahead; otherwise open the PR. Note in the PR description what the review found and how each finding was handled.
- **Before merging a PR yourself**, run `/engineering-advanced-skills:ship-gate`. Merge only if it allows the merge. If it blocks or raises issues, fix them (pushing to the PR branch, and giving the changed changeset one adversarial pass if it changes meaningfully) and run ship-gate again. Repeat until it allows the merge. Never merge past a ship-gate block.
- These gates add to the user's authority; they don't replace it. Merge on your own only when the user has authorized merging, and never treat a passing review or gate as user approval of a spec.

## Splitting work
- Default: treat a request as one issue / one spec, and don't split it or ask about splitting.
- Only if the scope looks big, use `/engineering-advanced-skills:spec-driven-workflow` to judge whether it fits one spec (one coherent set of FRs/ACs that one PR can deliver, without large parts that would land in "Out of Scope — separate spec"). If it clearly doesn't, ask the user whether to break it up, and recommend a split. Don't split without their yes.
- If the user agrees to split: create one GitHub issue per piece up front, then fork each issue into its own subagent (`subagent_type: "fork"`), one per issue, launched in parallel, to spec that issue.
- Each fork stays open for its issue's whole lifecycle: it writes the spec, gets it through adversarial review per the review rules above, and approves it per the rules above (itself by default; for "manual review" issues it waits for the user's explicit approval, relayed to that same fork). Then it implements the spec and opens the PR. A fork is done only once its PR exists.
- Route each approval, change request or question to the fork that owns that issue.
- The coordinator never edits a spec's content itself, even with exact wording in hand. The owning fork makes every spec change and validates it with the evaluator. The coordinator only relays changes, and for "manual review" issues applies the approval label/status when the user approves.
- Exception: issues filed for bugs or follow-ups found along the way (not part of the user's request) are only filed. No fork, no assignee, no `in progress` label, and no work on them the same day they're filed, unless the user explicitly asks.
