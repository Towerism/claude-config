# Global rules

Project CLAUDE.md files win on specifics (commands, paths, conventions); this file is cross-project only.

## Pipeline

Use these in order. Each injects a "Skill overlay"; if none appears, read `~/.claude/skill-overlays/<full skill name>.md`.

1. New project or major design: `senior-architect` drafts the architecture doc and ADRs (stack, data model, module boundaries) before feature work. Once reviewed they're the source of truth; a departure needs a new or updated ADR.
2. Start: `github-issue-lifecycle`. Every code-change request in a GitHub repo, even a bare "fix X", starts here, before reading or editing code.
3. Spec: `spec-driven-workflow`, stored where the lifecycle skill says (the issue body).
4. Test plan: `senior-qa` and/or `api-test-suite-builder` (HTTP routes), before the spec's test plan and again before the tests. Changes to `.github/workflows/*`, `.gitlab-ci.yml`, deploy jobs or the scripts they call: `ci-cd-pipeline-builder` before the pipeline plan and again before the YAML.
5. Implement: `tdd-guide` for all code (red-green-refactor).
6. Review: `adversarial-reviewer`, once on the spec and once on the PR diff.
7. Merge: `ship-gate`, then `github-issue-lifecycle` closes out.

Prose, alongside every step: load `plain-prose` once per session before the first human-facing text (chat replies and questions included). Its rules then cover every later text. Never re-invoke it while its `Base directory for this skill:` block is still in context; only reload after compaction drops it.

Rarely self-trigger; load: flaky tests, bugs: `focused-fix`; migrations: `migration-architect`, `database-schema-designer`; API changes: `api-design-reviewer`; dependency/bot PRs: `dependency-auditor`; ops docs: `senior-devops`, `runbook-generator`; fork orchestration: `agent-workflow-designer`.

## Approval modes

Before each spec, ask which approval mode applies, listing "Manual review with adversarial pass" first, as default. Skip asking if the prompt states a mode (its specs, or the session if it says so). With no possible user turn (a scheduled job, not `-p`), use the default.

- **Manual review with adversarial pass** (default): one adversarial pass, then present the spec with the review note and wait. Only the user's explicit words approve; set no approval marker yourself; silence, a validator or a reviewer verdict never approves. Don't implement against a draft. Any change after approval, however small, returns it to draft. In a split, the coordinator applies the approval label/status when the user approves.
- **Adversarial approval**: a clean pass (no CRITICAL, no change) approves; apply `approved`, post the review note on the issue, implement; otherwise raise and wait.
- **No review**: skip the reviewer on specs only (the PR-diff pass still runs); self-approve with a note saying so; implement.

Conflicts: manual review beats "no review"; else the more specific statement wins; if neither, ask. A change to an approved spec re-enters its mode.

## Reviews, merging

- One adversarial pass per spec or changeset; never re-run it, even after fixes or a CRITICAL. A later change for another reason gets its own pass. CRITICAL: fix, then raise. WARNING: fix, or accept with a one-line reason in the review note. NOTE: fix only if trivial, else list it.
- Raise: post the review note, say what was CRITICAL, quote each spec change with its reason, then stop until the user's go-ahead. Triggered by a fixed CRITICAL or any review-driven spec change. Manual mode folds this into presenting the spec.
- Before opening a PR, one pass on the full diff against the base branch; the PR description lists findings and their handling.
- Before reporting done or opening a PR, run tests, lint and typecheck; quote results.
- IMPORTANT: merge on your own only when the user has authorized merging. If a merge prompt is declined or unanswerable, don't merge another way; report it as waiting on the user.
- Gates add to the user's authority and never stand in for it.

## Git

- Conventional Commits typed by release impact (`feat`, `fix`, `!` plus a `BREAKING CHANGE:` footer); subject ends with `(#N)`; atomic commits; no squash, so every branch commit is conventional.
- Branch/worktree: `feature|fix|hotfix|docs|chore/{issue}-{slug}`. Delete after merge. Never commit to or force-push the default branch. Roll back with `git revert`. Only semantic-release tags.

## Splitting and forks

- One request is one issue and one spec by default; don't ask. If scope looks big, judge fit with `spec-driven-workflow`; if it clearly doesn't, recommend a split and ask. Never split without a yes.
- On a yes: one issue per piece, one fork (`subagent_type: "fork"`) per issue, in parallel. Settle each issue's approval mode first (one question), pass it on quoting the user; forks never re-ask.
- Each fork owns its issue through an opened PR; merges follow the merge rule. Route each approval, change request or question to the fork that owns that issue. The coordinator never edits a spec; the owning fork makes every spec change and validates it with the spec validator.
- Bugs or follow-ups found en route are only filed: no fork, assignee, `in progress` label or same-day work unless asked.

## Skills

- New skills always use `spec-driven-workflow`, with `SPEC.md` beside `SKILL.md`; an existing skill uses it only if a `SPEC.md` is already there: update the spec first, re-review it under its mode, keep status in its `**Status:**` line and the note in `REVIEW-NOTE.md` beside it. Edit other skills directly. Use `anthropic-skills:skill-creator`, `skill-tester`, and `skill-security-auditor` (third-party skills, before install).
- Run skill scripts only as `python3 -I <literal absolute path>` (overlay path or base dir + relative); never via shell variables or `$(...)`.
- Asked whether a skill was used? Say how deeply: loaded, references read, outputs used.
