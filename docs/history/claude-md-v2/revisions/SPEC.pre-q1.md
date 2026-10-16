# Spec: Layered Claude Code configuration — lean global CLAUDE.md + just-in-time skill overlays

**Author:** Claude (for mfracker)
**Date:** 2026-10-15
**Status:** Approved — by the user ("i approve the spec"), 2026-10-15, after three single adversarial passes (see REVIEW-NOTE.md)
**Reviewers:** `engineering-skills:adversarial-reviewer` (one pass), then the user (this spec replaces global configuration and changes the delivery workflow)
**Supersedes:** `~/dev/scratch/claude-md-rewrite/SPEC.md` (session "claude-skills-plugin-usage"), by the user's decision D-2 (2026-10-15)
**Depends on:** the `github-issue-lifecycle` skill (Option B, chosen by the user on 2026-10-15), which gets its own `SPEC.md` under FR-14 and must be built before this deliverable is installed

---

## Context

The global `~/.claude/CLAUDE.md` (10,980 bytes, ~2,742 tokens, 8 sections) grew from 138 bytes in five days (2026-10-10 → 10-15), one correction at a time. It encodes a pipeline that works — architecture first, a spec on every issue, test-skill-driven test plans, TDD, one adversarial pass, ship-gate before merge — but its *form* fights the platform:

Official guidance used throughout (code.claude.com/docs/en/memory, /best-practices, /skills, /hooks): CLAUDE.md should "target under 200 lines… longer files consume more context and reduce adherence"; "Claude treats CLAUDE.md files as context, not enforced configuration"; "use hooks for actions that must happen every time with zero exceptions"; a multi-step procedure or something that "only matters for one part" belongs in a skill; test each line with "would removing this cause Claude to make mistakes?"; and emphasise a line only if Claude keeps skipping it, because "if you emphasize many lines, none of them stands out."

1. **Prose enforcement failed where it mattered most.** senior-qa was loaded 127 times but its reference guides were opened ~16 times; the user's answer was more CLAUDE.md prose ("Apply the test skills; loading them isn't enough"). Anthropic's guidance is that CLAUDE.md is advisory context while hooks are deterministic: anything that must happen every time belongs in a hook. The previous spec (claude-md-rewrite) explicitly put hooks out of scope (its OS-1), so it could only re-word the instruction that already failed.
2. **Everything is always loaded.** About 70% of the file (CI/CD checklist, test-plan checklist, reviewer severity rules, ship-gate loop) matters only while one specific skill is active. Skills exist precisely to load such detail on demand (progressive disclosure); paying for it in every session — including Arch troubleshooting and research — dilutes the rules that apply everywhere.
3. **It overrides skills by restating them.** Rules like "spec lives on the issue, not in `specs/*.md`" contradict the loaded skill's own text, and the transcripts show Claude follows the loaded skill over a distant general rule. A correction delivered *at the moment the skill loads* sits next to the text it overrides.
4. **Monolithic custom skill.** `issue-spec-driven-workflow` (≈4,200 words) is a fork of upstream `spec-driven-workflow` plus ≈2,200 words of GitHub procedure; it misses upstream improvements. The user chose to make skills modular and let CLAUDE.md weave them together (Option B: a lifecycle-only `github-issue-lifecycle` skill + upstream `spec-driven-workflow`).
5. The previous session's coverage review proposed further rules (W1–W7, N1–N3); the user voided them (D-1), so they are not part of this spec.

Decisions already made by the user in that conversation and carried here as constraints: Option B; adopt `standards/git/git-workflow-standards.md` with the adaptations listed in FR-9 (Conventional Commits, `type/{issue}-{slug}` branches, hotfix flow, delete merged branches; reject manual tags and the claude-skills scope list); spikes keep full TDD; keep per-commit history (no squash); `claude-md-management` installed and used; new skills always use `spec-driven-workflow`, existing skills only when a `SPEC.md` sits beside the `SKILL.md`; the user is "fine with changing details about how I work".

### Design: where each kind of rule lives

| Layer | Loaded | Holds | Why this layer |
|---|---|---|---|
| `~/.claude/CLAUDE.md` | Every session | Authority (who approves and merges), the pipeline weave (which skill at which stage), git conventions, precedence, skill-authoring rule | Applies broadly; short enough to be followed |
| `~/.claude/skill-overlays/<skill>.md` | Injected when that skill loads | The user's policy for that skill: references to read (as absolute paths), plan checklists, stack translation, storage overrides, severity handling | Progressive disclosure; arrives next to the text it amends |
| PostToolUse + UserPromptSubmit hooks (`~/.claude/hooks/skill_overlay.py`) | Harness, deterministic | Injects the overlay; never depends on Claude remembering | "Must happen every time" ⇒ hook |
| PreToolUse `Bash` hook (`~/.claude/hooks/merge_confirm.py`) | Harness, deterministic | Makes every PR merge wait for the user's click (FR-24) | The one rule already violated (10-12) |
| `github-issue-lifecycle` skill (separate spec) | When a repo change starts/ends | Issue pickup/creation, labels, spec markers in the issue body, branch/worktree naming, PR with `Closes #N` | Procedure, used only in repos |
| Project `CLAUDE.md` / repo `docs/` | Per repo | Commands, paths, release specifics | Project facts beat global defaults |

Alternatives considered and rejected: `@imports` and `~/.claude/rules/*.md` (both load at launch, so they don't reduce always-loaded context, and `paths:` scoping keys on files touched, not on skills used); forking each plugin skill to embed the user's policy (drift from upstream, the problem Option B removes); a personal plugin bundling overlays and hooks (packaging overhead for one user; can be adopted later without changing the overlay format).

### Skills used to build this deliverable

| Skill | Contribution |
|---|---|
| `engineering-advanced-skills:spec-driven-workflow` | This spec's format, validator (`--strict` ≥ 80) and self-review (the scratch directory has no repo/issue, so the issue-based skill doesn't apply) |
| `claude-md-management:claude-md-improver` | `quality-criteria.md` rubric and `update-guidelines.md` ("every line must earn its place"; no generic advice) as the review rubric for the CLAUDE.md (AC-4) |
| `engineering-skills:senior-prompt-engineer` | `prompt_optimizer.py` baseline/compare (`claude-md-rewrite/baseline/baseline.json` is reused); eval set before editing |
| `engineering-skills:senior-qa` (all three references) + `engineering-skills:tdd-guide` | Test plan below; TDD for the hook and checker scripts |
| `engineering-advanced-skills:skill-tester` | Validates the `github-issue-lifecycle` skill package (in that skill's spec) |
| `engineering-skills:adversarial-reviewer` | The single review pass on this spec and on the final changeset |
| `update-config` | Writes the hook entries into `~/.claude/settings.json` correctly |
| `engineering-advanced-skills:self-eval` | Honest final scoring of the deliverable reported to the user |

---

## Functional Requirements

### A. Rule inventory (no silent loss)

- FR-1: Every clause of the current `~/.claude/CLAUDE.md` MUST be listed in `eval/clauses.json` (quoted from `baseline/CLAUDE.md.orig`) with exactly one disposition: `claude-md`, `overlay:<skill>`, `lifecycle-skill`, `superseded-by:<FR>` (a user decision recorded in this spec replaced it), or `dropped:<reason>`. `dropped` is allowed only for clauses that restate a skill's own instructions or the harness's defaults, and each one MUST be listed for the user at the review stop (FR-22).

### B. The global CLAUDE.md

- FR-2: **precedence** — It MUST state that a project `CLAUDE.md` wins on project specifics (commands, paths, conventions), and that the global file holds only cross-project rules.
- (FR-3 withdrawn — the user voided the previous session's coverage-review fixes (D-1, 2026-10-15).)
- FR-4: **pipeline weave** — It MUST name, in order, the skill used at each stage of a repo change, as a compact ordered list:
  1. New project or major design: `engineering-skills:senior-architect` drafts architecture doc + ADRs before feature work; user reviews; ADRs are then the source of truth and departures need a new/updated ADR;
  2. Start: `github-issue-lifecycle` (find or offer to create the issue, assign, label, branch/worktree).
  3. Spec: `engineering-advanced-skills:spec-driven-workflow`, stored where the lifecycle skill says (issue body).
  4. Test plan: `engineering-skills:senior-qa` and/or `engineering-advanced-skills:api-test-suite-builder` (HTTP routes), before the spec's test plan is written and again before the tests themselves. Pipelines: any change to `.github/workflows/*`, `.gitlab-ci.yml`, deploy jobs or the scripts they call → `engineering-advanced-skills:ci-cd-pipeline-builder` before the pipeline plan and again before writing YAML.
  5. Implement: `engineering-skills:tdd-guide` for all code (red-green-refactor).
  6. Review: `engineering-skills:adversarial-reviewer` — once on the spec, once on the PR diff.
  7. Merge: `engineering-advanced-skills:ship-gate`, then `github-issue-lifecycle` closes out.
- (FR-5 withdrawn — the user voided the previous session's coverage-review fixes (D-1, 2026-10-15).)
- FR-6: **non-obvious routing only** — It MUST contain a routing table limited to work whose skill would not reliably auto-trigger from its description or where the user picked a specific skill. Each row MUST cite why it is there (a transcript miss, or the user's pick, or a skill whose description doesn't name the user's trigger); rows whose skill auto-triggers from its description are dropped. Candidate rows: flaky tests/bugs → `focused-fix`; schema/data migrations → `migration-architect` (+ `database-schema-designer` for new schemas); HTTP or published-library API changes → `api-design-reviewer`; auth/session/secrets/PII → `senior-security`; dependency/bot PRs → `dependency-auditor`; deploy/ops docs → `senior-devops`, `runbook-generator`; multi-agent orchestration → `agent-workflow-designer`; CLAUDE.md upkeep → `claude-md-improver` / `/revise-claude-md`. (`linux-troubleshooting`, `senior-security` and `claude-md-improver` stay only if a transcript shows they failed to trigger.) Skills not listed are used when their description fits or the user asks.
- FR-7: **authority — approval modes** (user decision D-4, 2026-10-15) — It MUST state:
  - *Choosing the mode:* before writing each spec (so it is known whether the reviewer runs), Claude asks the user which approval mode applies, offering the three modes below with **"Manual review with adversarial pass" first and pre-selected as the default**. It does not ask when the user's prompt already states a mode: a stated mode covers the specs that prompt asks for, or the rest of the session if the prompt says so. If no user turn can follow (a scheduled or routine job started without a user prompt; not a headless `-p` run, which outputs the question and ends its turn), the default applies.
  - *Manual review with adversarial pass* (default): the single adversarial pass runs before the spec is presented; then only the user's explicit words approve; Claude sets no approval marker; silence, a passing validator or a reviewer verdict is never approval; no implementing against a draft — present it with the review note and wait; any change after approval, however small, returns it to draft. In a split, the coordinator applies the approval label/status when the user approves.
  - *Adversarial approval:* the single adversarial pass is the approval if it had no CRITICAL and changed nothing; then apply `approved`, post the review note on the issue (for skill specs, in `REVIEW-NOTE.md` beside `SPEC.md`), implement. Otherwise raise (FR-8) and wait.
  - *No review:* skip the reviewer on specs only (FR-8's PR-diff pass still runs); self-approve with a note on the issue saying so (for skill specs, in `REVIEW-NOTE.md` beside `SPEC.md`), and implement.
  - Conflicting stated modes: manual review always wins over a conflicting "no review"; otherwise the statement specific to that spec or issue wins over a session-wide one; if neither is more specific, ask rather than pick.
  - Any change to an approved spec re-enters its mode's process: user re-approval after one adversarial pass (manual), a new adversarial pass (adversarial approval), or self-re-approval (no review).
- FR-8: **authority — raising and merging** — It MUST state:
  - In manual mode, raising is folded into presenting the spec, and the go-ahead must be the user's explicit approval of the spec.
  - Raise = post the review note, say what was CRITICAL, quote each spec change with its reason, then stop until the user's go-ahead — triggered by a fixed CRITICAL or any review-driven spec change (including a PR review that changed the spec).
  - Merge only with the user's authorization. This is the one emphasised line (FR-16); FR-24 also enforces it with a hook. Merge on your own only when the user has authorized merging. If a merge prompt is declined or can't be answered, don't merge another way; report the merge as waiting on the user. Gates (review, ship-gate) add to the user's authority and never stand in for it.
  - Handling a review (the caller's rules, wherever the reviewer runs): one pass per spec or changeset, never re-run — not after fixes, not after a CRITICAL; a later change for another reason gets its own pass. CRITICAL → fix, then raise; WARNING → fix, or accept with a one-line reason in the review note; NOTE → fix only if trivial, else list it. Before opening a PR, one pass on the full diff against the base branch, and the PR description notes what it found and how each finding was handled.
  - Before reporting work done or opening a PR, run the repo's test/lint/typecheck commands and quote the result ("give Claude a way to verify its work").
- FR-9: **git conventions, adapted from git-workflow-standards** — It MUST state: Conventional Commits whose type matches the change's release impact (`feat`/`fix`/`!` + `BREAKING CHANGE:` footer drive semantic-release), subject ends with `(#N)`; branches `feature|fix|hotfix|docs|chore/{issue}-{slug}`, worktree named the same; atomic commits; per-commit history (merge commit or rebase, no squash), so every commit on the branch must itself be a Conventional Commit; delete the branch after merge; never commit to or force-push the default branch; roll back with `git revert`; semantic-release, not humans, creates tags.
- (FR-10 withdrawn — the user voided the previous session's coverage-review fixes (D-1, 2026-10-15).)
- FR-11: **splitting and forks** — It MUST state:
  - One request = one issue/spec by default, no asking. Only if scope looks big, judge fit with `spec-driven-workflow`; if it clearly doesn't fit one spec, recommend a split and ask; never split without a yes.
  - On a yes: one issue per piece up front, one fork (`subagent_type: "fork"`) per issue in parallel; before forking, the coordinator settles each issue's approval mode under FR-7 (one question covering all pieces, per-issue overrides allowed) and passes it to the fork, quoting the user's words; the fork treats the relayed mode as stated by the user and never re-asks, and a mode change comes only from the user, relayed to that fork. Each fork owns its issue's lifecycle through an opened PR, approves under FR-7 like any session, and merges only under FR-8. Route each approval, change request or question to the fork that owns that issue; the coordinator never edits a spec itself; the owning fork makes every spec change and validates it with the spec validator.
  - Bugs/follow-ups found along the way are only filed: no fork, assignee, `in progress` label, or same-day work unless the user asks.
- FR-12: **skill application honesty** — It MUST state that when asked whether a skill was used, Claude answers how deeply it was applied (loaded only / references read / outputs used).
- FR-13: **plugin scripts** — It MUST state that skill scripts are run as `python3 -I <literal absolute path>` (the overlay supplies the path; otherwise "Base directory for this skill" + relative path), never via a shell variable or command substitution, and only scripts shipped inside the loaded skill.
- FR-14: **skill authoring** — It MUST state: new skills always use `spec-driven-workflow` with `SPEC.md` beside `SKILL.md`; an existing skill uses it only if a `SPEC.md` is already beside its `SKILL.md` (spec updated first, re-reviewed under FR-7, approval status in the spec's `**Status:**` line, review note saved beside it); skills without one are edited directly; `skill-creator` guides structure and `skill-tester` validates; third-party skills are vetted with `skill-security-auditor` before install.
- FR-15: **overlay fallback** — It MUST contain one line telling Claude that if a skill loads and no "Skill overlay" block follows it, Claude reads `~/.claude/skill-overlays/<skill-name>.md` itself when that file exists.
- FR-16: **style** — It MUST be written as direct imperatives with a one-clause reason where the reason changes behaviour; emphasis (`IMPORTANT`) on exactly one line, the rule actually violated before — merge only with authorization (violated 10-12); no generic advice the model already follows (e.g. "write clean code"); no repo names; no section duplicating another.

### C. Skill overlays

- FR-17: An overlay file MUST exist for each of: `engineering-skills:senior-qa`, `engineering-advanced-skills:api-test-suite-builder`, `engineering-advanced-skills:ci-cd-pipeline-builder`, `engineering-skills:tdd-guide`, `engineering-skills:adversarial-reviewer`, `engineering-advanced-skills:ship-gate`, `engineering-advanced-skills:spec-driven-workflow`. Each overlay MUST contain only: (a) references/scripts to read or run, as paths relative to the skill's base directory; (b) the user's policy for that skill (the clauses FR-1 assigns to it); (c) stack translation where the skill targets another stack. Required content:
  - *senior-qa:* read `references/testing_strategies.md`, `qa_best_practices.md`, `test_automation_patterns.md` before any test plan; the plan visibly states pyramid placement per AC, coverage targets by risk, each flake hazard and how it is made deterministic, isolation and fixtures/factories, behaviour over mocks, tests rewritten/deleted, criteria that are review checks; aim tests at the riskiest behaviour, not line coverage; React/Jest examples are illustrations — translate them to the repo's test stack (e.g. when the repo uses Nuxt/Vitest: `@nuxt/test-utils`, `@vue/test-utils`, MSW, Playwright); don't run its React generator scripts on a non-React repo.
  - *api-test-suite-builder:* its Auth Test Matrix, Input Validation Matrix and error-code matrix plus `references/example-test-files.md`; its route detection doesn't cover every framework — apply the matrices to the repo's route files (e.g. Nitro/h3 `server/api/**`).
  - *ci-cd-pipeline-builder:* read `references/pipeline-design-notes.md`, `deployment-gates.md`, and `github-actions-templates.md` or `gitlab-ci-templates.md` for the platform; run `scripts/stack_detector.py`; the full pipeline-plan checklist from the current CLAUDE.md (stages traced to detected signals with commands confirmed, lint→test→build→deploy, cache keys by lockfile, matrices only with reason, secrets documented never embedded, environment protection + manual prod approval, rollback per deploy job, every pitfall addressed or N/A, pre-merge validation incl. `act`, and which items are review checks rather than automated checks); reload before writing YAML; one optimization at a time.
  - *tdd-guide:* red-green-refactor for all code; before writing tests, reload senior-qa / api-test-suite-builder; before writing pipeline YAML, reload ci-cd-pipeline-builder (these reloads are also in FR-4, since this overlay may load in a later session); specs are not stored in `specs/*.md` and AC IDs are not put in code when work is on an issue (the lifecycle skill's rule).
  - *adversarial-reviewer* (reviewer-side only; the caller's handling rules are in CLAUDE.md FR-8 because reviews usually run in a subagent): the spec-CRITICAL definition; a finding raised only to meet the per-persona quota is at most a WARNING; the 2+-persona promotion never creates a CRITICAL on its own; flag only gaps that affect correctness, the stated requirements or the user's rules.
  - *ship-gate:* run before any self-merge; on block, fix on the PR branch, one adversarial pass if the changeset changed meaningfully, re-run ship-gate; never merge past a block; a pass is not merge authorization.
  - *spec-driven-workflow:* confirm the FR-7 approval mode is settled before drafting; in a GitHub repo the spec lives in the issue body per `github-issue-lifecycle` (not `specs/*.md`); for skills it is `SPEC.md` beside `SKILL.md`; the "big scope" fit judgement per FR-11.
- FR-18: Each overlay MUST be ≤ 700 estimated tokens and MUST NOT restate the skill's own instructions.

### D. Hooks

- FR-18a: **verify the mechanism first** — Before the hook is built, a time-boxed check MUST confirm against the installed Claude Code (2.1.299) that (a) a `PostToolUse` hook with matcher `Skill` fires on a Skill tool call, in the main session and in a subagent, and (b) its `hookSpecificOutput.additionalContext` reaches the model. It MUST also record whether a PreToolUse `ask` still prompts under `--permission-mode auto` and `bypassPermissions` (FR-24); if it does not, that is reported to the user before install. If (a) or (b) fails, the injection point falls back, in order, to `UserPromptExpansion` / `UserPromptSubmit` (user-typed skills) plus a `PreToolUse` `Skill` hook returning `additionalContext`, and finally to FR-15's fallback line alone. The result is recorded in the spec before implementation; changing the injection event is not a spec change, but dropping deterministic injection entirely is, and goes back to the user.
- FR-19: **PostToolUse injection** — A `PostToolUse` hook with matcher `Skill` MUST run `~/.claude/hooks/skill_overlay.py`, which reads the hook JSON from stdin, takes `tool_input.skill`, and, if `~/.claude/skill-overlays/<skill>.md` exists, returns `hookSpecificOutput.additionalContext` beginning `Skill overlay for <skill>:` followed by the overlay text with every relative reference/script path rewritten to an absolute path under the skill's *active* base directory: for plugin skills the `installPath` recorded in `~/.claude/plugins/installed_plugins.json`, for user skills `~/.claude/skills/<name>/`. If no overlay exists it outputs nothing and exits 0.
- FR-20: **user-typed slash commands** — A `UserPromptExpansion` hook (or, if FR-18a shows it doesn't carry the skill name, a `UserPromptSubmit` hook) MUST run the same script in prompt mode: if the prompt starts with `/<skill-name>` for a skill that has an overlay, it injects the same `additionalContext`.
- FR-21: **fail visible, never block** — On any internal error (bad JSON, unreadable overlay, unresolvable skill directory) the script MUST exit 0, inject `Skill overlay for <skill> could not be loaded (<reason>); read ~/.claude/skill-overlays/<skill>.md manually.` when the skill name is known, and write the traceback to stderr. It MUST never block a tool call or prompt.


### E. Merge guard

- FR-24: **merge confirmation hook** (user decision D-3) — A `PreToolUse` hook with matcher `Bash` MUST run `python3 -I ~/.claude/hooks/merge_confirm.py`, which reads `tool_input.command` and returns `hookSpecificOutput.permissionDecision: "ask"` with `permissionDecisionReason` "Merging needs your confirmation (global rule: merge only with your authorization)" when the command matches `\bgh\b(\s+-\S+(\s+\S+)?)*\s+pr\s+merge\b` or `\bgh\s+api\b.*\/merge\b` anywhere in the string (so `gh -R o/r pr merge 5`, `cd x && gh pr merge`, and `bash -c "gh pr merge …"` are caught); otherwise it outputs nothing. It MUST NOT deny, MUST exit 0, and MUST NOT match other `gh` commands (e.g. `gh pr view`, `gh pr list --search merge`). This applies in the main session, forks and subagents.

### F. Rollout

- FR-22: **review stop** — Before install, Claude MUST present to the user: the new CLAUDE.md, the overlays, the clause inventory's `dropped` and `superseded-by` rows, the eval results (AC-6/AC-7), and the token figures; installation waits for the user's go-ahead.
- FR-23: **install & rollback** — Install MUST:
  1. Abort unless the sha256 of the live `~/.claude/CLAUDE.md` equals `claude-md-rewrite/baseline/CLAUDE.md.orig` (the inventory's source); otherwise rebuild `eval/clauses.json` from the live file and re-run AC-1 and AC-6 first.
  2. Back up `~/.claude/CLAUDE.md`, `~/.claude/settings.json` and `~/.claude/skills/issue-spec-driven-workflow/` to `~/.claude/backups/claude-md-v2-<timestamp>/`.
  3. Write the new CLAUDE.md, overlays and both hook scripts (`skill_overlay.py`, `merge_confirm.py`); add the overlay hooks and the FR-24 merge hook via `update-config`.
  4. Disable `issue-spec-driven-workflow` by *moving* its directory into the backup directory (outside `~/.claude/skills/`, so it is no longer discovered); it is deleted only when the user confirms after two weeks of use.
  `restore.sh` in the backup directory MUST be non-destructive: it first snapshots the current CLAUDE.md, settings.json and skills dir to `restore-<timestamp>/`; removes only the hook entries this install added (matched by command path) instead of overwriting settings.json; overwrites CLAUDE.md only if it still equals the installed deliverable, otherwise stops and prints the diff; and moves the old skill back.

## Non-Functional Requirements

### Performance (context budget)
- NFR-P1: The new CLAUDE.md MUST be ≤ 200 lines (official target) and ≤ 1,800 estimated tokens (`prompt_optimizer.py --analyze --json`) and SHOULD be ≤ 1,400 (baseline 2,742). If FR-2…FR-16 cannot fit 1,800, the overage is reported to the user rather than rules being cut.
- NFR-P2: Each overlay ≤ 700 tokens (FR-18). The hook MUST finish in < 500 ms (p95 over 20 runs on this machine) so it adds no noticeable latency.

### Reliability
- NFR-R1: Every skill name in the CLAUDE.md and every overlay filename MUST resolve to an installed skill (plugin cache `*/<version>/skills/<skill>/SKILL.md`, `~/.claude/skills/<name>/SKILL.md`, or `~/.claude/skills/synced/*/<name>/SKILL.md`), and every path an overlay names MUST exist in that skill.
- NFR-R2: The hook MUST NOT hard-code plugin version directories; it resolves the active `installPath` from `installed_plugins.json` at run time.
- NFR-R3: Install is reversible by one command (FR-23) and is verified by running it in a temp `HOME`.

### Security
- NFR-S1: No secrets, tokens, email addresses or customer data in any deliverable file.
- NFR-S2: The hook script reads only `~/.claude/skill-overlays/` and the skill directories; it makes no network calls and executes no content from overlays. Skill names are validated against `^[a-z0-9-]+(:[a-z0-9-]+)?$` before any path is built (no path traversal).

### Maintainability
- NFR-M1: Each rule appears in exactly one layer (checked by the clause inventory: one disposition per clause).
- NFR-M2: No repo names in the CLAUDE.md or overlays.
- NFR-M3: The CLAUDE.md scores ≥ 80/100 on `claude-md-improver`'s `quality-criteria.md` as applied to a global file (Commands/Architecture criteria scored on "workflow and skill map" rather than build commands), assessed by a fresh subagent.

## Acceptance Criteria

### AC-1: No rule lost (FR-1, NFR-M1)
Given `eval/clauses.json` built from `baseline/CLAUDE.md.orig`
When the checker runs with `--clauses`
Then every clause has exactly one disposition, every `claude-md`/`overlay:*`/`lifecycle-skill` clause's key phrase (`mustAppear`) is present in that target file, and every `dropped`/`superseded-by` row has a non-empty reason; otherwise exit 1 naming the clause.

### AC-2: Names and paths resolve (NFR-R1, FR-6, FR-17)
Given the installed plugin cache and user skills
When the checker parses the CLAUDE.md and overlays
Then every skill name resolves, every overlay path exists under its skill's active installPath, and an injected unknown name (`engineering-skills:does-not-exist`) makes it exit 1 naming it.

### AC-3: Budget (NFR-P1, NFR-P2, FR-18)
Given the optimizer and the overlays
When measured
Then CLAUDE.md `token_count` ≤ 1,800 (figure reported; > 1,400 flagged), each overlay ≤ 700, and the hook p95 < 500 ms over 20 runs.

### AC-4: Quality rubric (NFR-M3, FR-16)
Given `claude-md-improver/references/quality-criteria.md`
When a headless `claude -p` run isolated as in AC-6 scores the new CLAUDE.md against the rubric
Then the score is ≥ 80, and the checker finds exactly one emphasised line and it is the merge-authorization rule, no repo names, no email/token patterns (`[\w.+-]+@[\w-]+\.[\w.]+`, `(ghp_|gho_|github_pat_|sk-|xox[bp]-)[A-Za-z0-9_-]{16,}`).

### AC-5: Overlay injection (FR-17, FR-18a, FR-19, FR-20, FR-21, NFR-S2)
Given a PostToolUse payload `{"tool_name":"Skill","tool_input":{"skill":"engineering-skills:senior-qa"}}`
When `skill_overlay.py` runs
Then stdout is JSON whose `hookSpecificOutput.additionalContext` starts `Skill overlay for engineering-skills:senior-qa:` and contains the absolute path of `references/testing_strategies.md`; a skill with no overlay yields empty output and exit 0; a prompt `/engineering-skills:senior-qa plan tests` in prompt mode yields the same context; malformed JSON, a missing overlay directory, or skill `../../etc/passwd` yields exit 0 and no file read outside the allowed roots.

### AC-6: Behaviour preserved and improved — scenario eval (FR-2, FR-4, FR-6, FR-7, FR-8, FR-9, FR-11, FR-12, FR-13, FR-14, FR-15)
Given `eval/scenarios.json` (from real transcript friction where possible; each non-withdrawn FR-2…FR-15 covered at least once; includes "write a spec, prompt names no mode" (must ask, manual listed first as the default), "prompt says no review" (must not ask), "work on #42 in a repo", "fix the flaky test", "hotfix branch naming", "make a new skill", "edit linux-troubleshooting", "fork merges after green", "Arch boot problem", "release with an API change (commit type)")
When each scenario runs headless as `claude -p --output-format json` once per arm, where each arm's run sees only that arm's global CLAUDE.md — via a temp `CLAUDE_CONFIG_DIR` holding that file (credentials copied in and removed in a `trap`), or `--setting-sources` if FR-18a's check shows it excludes the user CLAUDE.md — and the new arm also gets the overlay text the hook would inject for any skill the scenario loads; arm isolation is first proven by a probe whose answer must contain a canary line present only in that arm's file
Then every scenario passing under the original also passes under the new config, except scenarios tagged `superseded:D-4` (listed at the FR-22 stop); the scenarios for the new rules (FR-7, FR-9, FR-13, FR-14, FR-15) pass under the new one; the must-ask scenario passes when the output asks for a mode, lists all three modes with "Manual review with adversarial pass" first and marked as the default, and contains no spec text; and a delegated-review scenario ("the reviewer subagent returned 1 CRITICAL and 2 WARNINGs — what now?") shows the caller's handling rules (FR-8) without the reviewer overlay loaded.

### AC-7: Hook works in the real harness (FR-19)
Given a test overlay for `engineering-skills:senior-qa` containing a unique canary string, and the hook supplied with `claude -p --settings <tmp-hooks.json>` against the real config (no credential copying)
When `claude -p --output-format stream-json "Load the engineering-skills:senior-qa skill, then quote any skill overlay you received."` runs, and again with a prompt that makes a subagent load the skill
Then both answers contain the canary, the stream shows no Read of `skill-overlays/` (so the hook, not the FR-15 fallback, delivered it), and a negative-control run without `--settings` does not contain the canary. (One re-run on failure; fails only if it fails twice.)

### AC-8: Safe install and rollback (FR-22, FR-23, NFR-R3)
Given the user's go-ahead
When install runs
Then backups match the pre-install files byte-for-byte, `~/.claude/CLAUDE.md` equals the deliverable, the settings hooks validate as JSON and contain the FR-24 entry (and don't after `restore.sh`), and `issue-spec-driven-workflow` no longer appears in a fresh session's skill list (`claude -p` listing). In a temp `HOME`: an unchanged install is restored byte-for-byte by `restore.sh`; after adding an unrelated settings entry (e.g. an enabled plugin) and editing CLAUDE.md, `restore.sh` keeps the settings entry, removes only the overlay hooks, refuses to overwrite the edited CLAUDE.md (non-zero exit, diff printed), and leaves a `restore-<ts>/` snapshot. Install refuses when the live CLAUDE.md's sha256 differs from the baseline.

### AC-9: Merge needs a click (FR-24)
Given `claude -p --settings <tmp-hooks.json> --allowedTools 'Bash(gh pr view *)' 'Bash(gh pr merge *)' --permission-prompts none --output-format json` (both commands pre-allowed, so only the hook can stop one)
When Claude is asked to run `gh pr merge --help` and then `gh pr view --help`
Then the result's `permission_denials` contains the merge call and not the view call, and a control run with the same flags but without `--settings` shows both executed. Unit tests on `merge_confirm.py` assert `ask` plus the exact reason for `gh pr merge 5`, `gh -R o/r pr merge 5`, `cd x && gh pr merge 5`, `bash -c "gh pr merge 5"`, `gh api repos/o/r/pulls/5/merge`, and no output for `gh pr view 5` and `gh pr list --search merge`. (One re-run on failure; fails only if it fails twice.)

## Edge Cases

- EC-1: Plugin update changes the version directory or removes a referenced file → hook resolves the active installPath (NFR-R2); a missing referenced file is reported inside the injected context ("missing: …") and by the checker (AC-2).
- EC-2: Skill loaded inside a subagent/fork → hooks fire for subagent tool calls as well; AC-7's subagent run checks it. If the harness does not fire it there, FR-15's fallback line applies and the gap is reported to the user.
- EC-3: The same skill is loaded twice in a session → the overlay is injected again (small, idempotent).
- EC-4: A project `.claude/settings.json` also defines hooks → both run; the overlay hook has no side effects, so order doesn't matter.
- EC-5: The still-open "claude-skills-plugin-usage" session (superseded by D-2, but still able to act if resumed) or anything else changes `~/.claude/CLAUDE.md` after the baseline was taken → install aborts on the sha256 check (FR-23 step 1) and the inventory is rebuilt.
- EC-6: `github-issue-lifecycle` isn't built yet → install is blocked (dependency); the checker fails on the unresolved name.
- EC-7: The user states two modes that conflict for one spec → manual review wins over "no review"; otherwise the statement specific to the spec/issue wins over a session-wide one; if neither is more specific, Claude asks (FR-7).
- EC-8: Scenario-eval nondeterminism → binary must-include / must-not-include checks; one re-run per failing case; identical policy for old and new config.
- EC-9: A skill is invoked by its short name (e.g. `/senior-qa`) → the hook normalises by matching the overlay whose name ends with `:<short-name>` when exactly one does.
- EC-10: The work machine (Jira-based spec skill) lacks these skills → out of scope (OS-3); the overlay hook is a no-op there.
- EC-11: A merge attempted where nobody can click (headless `-p` run, unattended background job) → the call is denied; FR-8's "don't merge another way" line applies. This is the intended safe failure.
- EC-12: A merge done some other way (GitHub UI, `git push` of a merge to the default branch — already forbidden by FR-9, a custom script) → not caught by FR-24; FR-8 still applies.

## API Contracts

N/A for HTTP — no endpoints. Programmatic interfaces:

```typescript
// Hook script: python3 -I ~/.claude/hooks/skill_overlay.py [--mode tool|prompt]
// stdin: Claude Code hook JSON; exit code always 0
interface ToolModeInput   { hook_event_name: "PostToolUse"; tool_name: "Skill"; tool_input: { skill: string; args?: string } }
interface PromptModeInput { hook_event_name: "UserPromptSubmit" | "UserPromptExpansion"; prompt: string }
interface HookOutput {                      // printed only when an overlay applies or failed to load
  hookSpecificOutput: { hookEventName: "PostToolUse" | "PreToolUse" | "UserPromptSubmit" | "UserPromptExpansion"; additionalContext: string }
}

// Merge guard: python3 -I ~/.claude/hooks/merge_confirm.py   (PreToolUse, matcher "Bash"; exit code always 0)
interface MergeGuardInput  { hook_event_name: "PreToolUse"; tool_name: "Bash"; tool_input: { command: string } }
interface MergeGuardOutput {               // printed only when the command is a merge
  hookSpecificOutput: { hookEventName: "PreToolUse"; permissionDecision: "ask"; permissionDecisionReason: string }
}

// Checker: python3 -I check_config.py --claude-md <path> --overlays <dir> --skill-root <dir>... [--clauses eval/clauses.json] [--json]
// exit 0 = pass, 1 = findings, 2 = usage error
interface CheckReport {
  ok: boolean;
  unresolvedSkills: string[];
  missingPaths: { skill: string; path: string }[];
  clauseProblems: { id: string; problem: "no-disposition" | "phrase-missing" | "no-reason" | "duplicate" }[];
  forbiddenContent: { kind: "repo-name" | "email" | "token" | "caps"; match: string }[];
  tokenCounts: { file: string; tokens: number; limit: number }[];
}
interface UsageError { error: string }
```

## Data Models

### Clause (`eval/clauses.json`)
| Field | Type | Constraints |
|---|---|---|
| id | string | `C-NN`, unique |
| text | string | quoted from `baseline/CLAUDE.md.orig` |
| disposition | string | `claude-md` \| `overlay:<skill>` \| `lifecycle-skill` \| `superseded-by:FR-N` \| `dropped` |
| mustAppear | string \| null | key phrase that must be present in the target; null only for `dropped`/`superseded-by` |
| reason | string \| null | required for `dropped`/`superseded-by` |

### Overlay (`~/.claude/skill-overlays/<skill>.md`)
| Field | Type | Constraints |
|---|---|---|
| filename | string | exact skill name incl. namespace, `:` kept (e.g. `engineering-skills:senior-qa.md`) |
| body | markdown | ≤ 700 tokens; paths relative to the skill base dir, written as `` `references/x.md` `` |

### Scenario (`eval/scenarios.json`)
| Field | Type | Constraints |
|---|---|---|
| id | string | `S-NN`, unique |
| prompt | string | from real transcripts where possible |
| covers | string[] | FR IDs |
| loadsSkills | string[] | overlays to include as hook-injected context |
| mustInclude / mustNotInclude | string[] | binary-graded behaviours |

## Out of Scope

- OS-1: Building the `github-issue-lifecycle` skill — its own `SPEC.md` (FR-14); this spec only depends on its name and its role in FR-4.
- OS-2: Installing further marketplace plugins (skill-doctor, grill-me, pw, self-improving-agent, security-guidance) — separate decisions; routing names only installed skills.
- OS-3: The work-machine variant.
- OS-4: Creating the missing project CLAUDE.md files for discord-pokemon-tournament-genie and selfhost-kit, and adding commitlint — follow-up issues filed only (FR-11).
- OS-5: Permission allow-rules for plugin scripts (`/fewer-permission-prompts`) — useful, separate settings change.
- OS-7: A Stop-hook verification gate (block turn end until tests pass) — it would misfire in non-repo sessions; FR-8's verify-before-done line covers it in prose for now.
- OS-6: Editing the previous spec in `claude-md-rewrite/` — it belongs to the other session; it is superseded by D-2, and marking it so is left to the user or that session.

---

## Test Plan

Built from senior-qa's `testing_strategies.md` (pyramid, decision framework, coverage by risk), `qa_best_practices.md` (testable code via dependency injection, isolation checklist, flaky-test causes) and `test_automation_patterns.md` (factories, fixtures); red-green-refactor via tdd-guide for both scripts. Framework: pytest (`python3 -I -m pytest`).

**Pyramid placement per AC** (logic pushed down; the expensive model-in-the-loop layer only where only a model can show the behaviour):

| AC | Layer | Why |
|---|---|---|
| AC-5, EC-1, EC-3, EC-9, NFR-S2 traversal | Unit (pytest, `tmp_path` skill trees + overlay dirs) | Pure parsing/resolution/path-rewriting; fast, exact; highest-risk code |
| AC-1, AC-2, AC-4 (lint part) | Unit for the rules + one integration run against the real cache and real drafts | Rules are logic; the integration run proves names resolve on this machine |
| AC-3 | Integration: existing `prompt_optimizer.py` + a 20-run timing loop of the hook | Reuse the tool; timing is a measurement, reported not asserted per run |
| AC-6, AC-4 (rubric part) | Model eval: isolated headless `claude -p` per arm, old vs new | Rule comprehension can only be shown by a model reading the files |
| AC-7 | E2E: one headless `claude -p` journey (+ one subagent load for EC-2) | The single critical journey: hook fires in the real harness |
| AC-9 | Unit (settings entry) + one headless `--settings` run | The deterministic part is config; one real run proves the harness honours `ask` |
| AC-8 | Scripted install/restore in a temp `HOME` with `cmp` | Verifies byte-exact backup and rollback without touching the real config |

**Coverage targets by risk:** `skill_overlay.py` path validation, resolution and rewriting — every branch unit-tested (it runs on every Skill call and guards file access); checker rules — every rule has a passing and a failing fixture; CLI argument glue — one integration test each. pytest-cov is not installed, so branch coverage is reviewed against the function list rather than measured.

**Flake hazards and how each is made deterministic:**
- Real plugin cache contents change on update → unit tests use injected roots (`--skill-root`, `CLAUDE_HOME` env injected per test); only the single integration run reads the real cache.
- Model nondeterminism (AC-4, AC-6, AC-7) → binary must-include/must-not-include grading, one re-run, fail only on two failures, same policy for old and new config (EC-8); no assertions on wording.
- Timing (NFR-P2) → measured as p95 over 20 runs and reported; not asserted inside unit tests (no wall-clock assertions).
- Real `~/.claude` mutation → every install/restore test runs against a temp `HOME`.
- Arm isolation in model evals (C1 of the review: subagents inherit the live CLAUDE.md) → every model-in-the-loop run is a headless `claude -p` with its own config dir, proven by a canary probe before scoring; no in-session subagent is used for old-vs-new comparison.
- False pass in AC-7 (the model can derive paths without the hook) → canary + negative control + no-Read assertion.

**Isolation and fixtures/factories:** `make_skill(root, bundle, version, name, files=[])` and `make_overlay(dir, skill, body)` factories build fresh trees under `tmp_path` per test; `hook_payload(kind, **kw)` builds stdin JSON. No shared mutable state; env vars restored per test (monkeypatch).

**Behaviour over mocks:** no mocking — the scripts run as subprocesses against real temp files, asserting on stdout JSON and exit codes.

**Existing tests rewritten or deleted:** none for this deliverable. `issue-spec-driven-workflow/tests/` remain with the disabled skill (FR-23) and are handled by the lifecycle skill's spec.

**Review checks rather than tests:** `/doctor prompt-audit` on the installed file, run by the user (Claude can't invoke it) with its findings answered; wording fidelity of moved clauses beyond `mustAppear`; NFR-M1 duplication beyond the inventory; FR-16 tone; the user's taste in structure (FR-22 review stop).

## Decisions (resolved by the user, 2026-10-15)

- **D-1:** The previous session's coverage-review fixes are void; FR-3, FR-5 and FR-10 are withdrawn and their clauses removed elsewhere.
- **D-2:** This spec supersedes `claude-md-rewrite/SPEC.md`.
- **D-3:** Merge-confirmation hook adopted as FR-24.
- **D-4:** Approval modes changed: Claude always asks which mode applies unless the prompt states it; the default (pre-selected) is manual review with an adversarial pass. This supersedes two clauses of the current CLAUDE.md, marked `superseded-by:FR-7` in the inventory: "adversarial review is the approval by default" and "no review lasts for the rest of the session" (a stated mode now covers that prompt's specs unless the prompt says the session). "Manual review wins" and "no review skips the reviewer on specs only" are kept.
