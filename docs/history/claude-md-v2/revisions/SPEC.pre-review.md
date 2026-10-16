# Spec: Layered Claude Code configuration — lean global CLAUDE.md + just-in-time skill overlays

**Author:** Claude (for mfracker)
**Date:** 2026-10-15
**Status:** Draft
**Reviewers:** `engineering-skills:adversarial-reviewer` (one pass), then the user (this spec replaces global configuration and changes the delivery workflow)
**Supersedes:** `~/dev/scratch/claude-md-rewrite/SPEC.md` (session "claude-skills-plugin-usage"), if the user agrees — see OS-6
**Depends on:** the `github-issue-lifecycle` skill (Option B, chosen by the user on 2026-10-15), which gets its own `SPEC.md` under FR-14 and must be built before this deliverable is installed

---

## Context

The global `~/.claude/CLAUDE.md` (10,980 bytes, ~2,742 tokens, 8 sections) grew from 138 bytes in five days (2026-10-10 → 10-15), one correction at a time. It encodes a pipeline that works — architecture first, a spec on every issue, test-skill-driven test plans, TDD, one adversarial pass, ship-gate before merge — but its *form* fights the platform:

Official guidance used throughout (code.claude.com/docs/en/memory, /best-practices, /skills, /hooks): CLAUDE.md should "target under 200 lines… longer files consume more context and reduce adherence"; "Claude treats CLAUDE.md files as context, not enforced configuration"; "use hooks for actions that must happen every time with zero exceptions"; a multi-step procedure or something that "only matters for one part" belongs in a skill; test each line with "would removing this cause Claude to make mistakes?"; and emphasise a line only if Claude keeps skipping it, because "if you emphasize many lines, none of them stands out."

1. **Prose enforcement failed where it mattered most.** senior-qa was loaded 127 times but its reference guides were opened ~16 times; the user's answer was more CLAUDE.md prose ("Apply the test skills; loading them isn't enough"). Anthropic's guidance is that CLAUDE.md is advisory context while hooks are deterministic: anything that must happen every time belongs in a hook. The previous spec (claude-md-rewrite) explicitly put hooks out of scope (its OS-1), so it could only re-word the instruction that already failed.
2. **Everything is always loaded.** About 70% of the file (CI/CD checklist, test-plan checklist, reviewer severity rules, ship-gate loop) matters only while one specific skill is active. Skills exist precisely to load such detail on demand (progressive disclosure); paying for it in every session — including Arch troubleshooting and research — dilutes the rules that apply everywhere.
3. **It overrides skills by restating them.** Rules like "spec lives on the issue, not in `specs/*.md`" contradict the loaded skill's own text, and the transcripts show Claude follows the loaded skill over a distant general rule. A correction delivered *at the moment the skill loads* sits next to the text it overrides.
4. **Monolithic custom skill.** `issue-spec-driven-workflow` (≈4,200 words) is a fork of upstream `spec-driven-workflow` plus ≈2,200 words of GitHub procedure; it misses upstream improvements. The user chose to make skills modular and let CLAUDE.md weave them together (Option B: a lifecycle-only `github-issue-lifecycle` skill + upstream `spec-driven-workflow`).
5. **Gaps found by the coverage review** of the previous spec (2026-10-15, 0 CRITICAL / 7 WARNING): repos without a project CLAUDE.md (only play-pokemon-attendance has one; selfhost-kit's release rules live only in `docs/releasing.md`), routed skills replacing instead of nesting inside the issue workflow, work with no issue (Renovate PRs, hotfixes), background-job/STOP semantics, merge-grant scope, non-code tasks, cross-repo ordering, ADR edits, synced skills.

Decisions already made by the user in that conversation and carried here as constraints: Option B; adopt `standards/git/git-workflow-standards.md` with the adaptations listed in FR-9 (Conventional Commits, `type/{issue}-{slug}` branches, hotfix flow, delete merged branches; reject manual tags and the claude-skills scope list); spikes keep full TDD; keep per-commit history (no squash); `claude-md-management` installed and used; new skills always use `spec-driven-workflow`, existing skills only when a `SPEC.md` sits beside the `SKILL.md`; the user is "fine with changing details about how I work".

### Design: where each kind of rule lives

| Layer | Loaded | Holds | Why this layer |
|---|---|---|---|
| `~/.claude/CLAUDE.md` | Every session | Authority (who approves/merges, STOP), the pipeline weave (which skill at which stage), git conventions, precedence, skill-authoring rule | Applies broadly; short enough to be followed |
| `~/.claude/skill-overlays/<skill>.md` | Injected when that skill loads | The user's policy for that skill: references to read (as absolute paths), plan checklists, stack translation, storage overrides, severity handling | Progressive disclosure; arrives next to the text it amends |
| PostToolUse + UserPromptSubmit hooks (`~/.claude/hooks/skill_overlay.py`) | Harness, deterministic | Injects the overlay; never depends on Claude remembering | "Must happen every time" ⇒ hook |
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

- FR-2: **precedence** — It MUST state that a project `CLAUDE.md` wins on project specifics (commands, paths, conventions), and that in a repo **without** one Claude reads `README`, `docs/` (architecture, ADRs, releasing/deploy) and `package.json` scripts before changing anything.
- FR-3: **when the delivery pipeline applies** — It MUST state that the pipeline in FR-4 applies to changes in a git repo with a GitHub remote, and that troubleshooting, research, reports and questions need no issue or spec.
- FR-4: **pipeline weave** — It MUST name, in order, the skill used at each stage of a repo change, as a compact ordered list:
  1. New project or major design: `engineering-skills:senior-architect` drafts architecture doc + ADRs before feature work; user reviews; ADRs are then the source of truth and departures need a new/updated ADR; ADR edits also go through senior-architect.
  2. Start: `github-issue-lifecycle` (find or offer to create the issue, assign, label, branch/worktree).
  3. Spec: `engineering-advanced-skills:spec-driven-workflow`, stored where the lifecycle skill says (issue body).
  4. Test plan: `engineering-skills:senior-qa` and/or `engineering-advanced-skills:api-test-suite-builder`; pipelines: `engineering-advanced-skills:ci-cd-pipeline-builder`.
  5. Implement: `engineering-skills:tdd-guide` for all code (red-green-refactor).
  6. Review: `engineering-skills:adversarial-reviewer` — once on the spec, once on the PR diff.
  7. Merge: `engineering-advanced-skills:ship-gate`, then `github-issue-lifecycle` closes out.
- FR-5: **specialist skills nest, never replace** — It MUST state that specialist skills (table in FR-6) are applied *inside* the FR-4 stage they serve, never instead of the issue/spec/review stages.
- FR-6: **non-obvious routing only** — It MUST contain a routing table limited to work whose skill would not reliably auto-trigger from its description or where the user picked a specific skill. Initial rows: flaky tests/bugs → `focused-fix`; schema/data migrations → `migration-architect` (+ `database-schema-designer` for new schemas); HTTP or published-library API changes → `api-design-reviewer`; auth/session/secrets/PII → `senior-security`; dependency/bot PRs → `dependency-auditor`; deploy/ops docs → `senior-devops`, `runbook-generator`; multi-agent orchestration → `agent-workflow-designer`; CLAUDE.md upkeep → `claude-md-improver` / `/revise-claude-md`; Arch Linux → `linux-troubleshooting`. Skills not listed are used when their description fits or the user asks.
- FR-7: **authority — approval modes** — It MUST state the three spec-approval modes, with these clauses intact:
  - *Default:* the single adversarial pass is the approval if it had no CRITICAL and changed nothing; then apply `approved`, post the review note on the issue, implement. Otherwise raise (FR-8) and wait.
  - *"Manual review"* (per issue): only the user's explicit words approve; Claude sets no approval marker; silence, a passing validator or a reviewer verdict is never approval; no implementing against a draft; any change after approval returns it to draft.
  - *"No review"* (rest of session): skip the reviewer on specs, self-approve with a note saying so; "manual review" on an issue still wins.
  - Any change to an approved spec re-enters review (default/manual) or self-re-approval ("no review").
- FR-8: **authority — raising and merging** — It MUST state:
  - Raise = post the review note, say what was CRITICAL, quote each spec change with its reason, then stop until the user's go-ahead — triggered by a fixed CRITICAL or any review-driven spec change (including a PR review that changed the spec).
  - Merge only with the user's authorization. A grant covers exactly the issues or session the user names; the coordinator relays it to forks in scope. Gates (review, ship-gate) add to the user's authority and never stand in for it.
  - "STOP"/"hold off" from the user halts every agent and background job of the session until the user resumes them.
- FR-9: **git conventions, adapted from git-workflow-standards** — It MUST state: Conventional Commits whose type matches the change's release impact (`feat`/`fix`/`!` + `BREAKING CHANGE:` footer drive semantic-release), subject ends with `(#N)`; branches `feature|fix|hotfix|docs|chore/{issue}-{slug}`, worktree named the same; per-commit history (no squash); delete the branch after merge; never commit to or force-push the default branch; roll back with `git revert`; semantic-release, not humans, creates tags.
- FR-10: **work without an issue** — It MUST state: offer to create an issue; bot/dependency PRs need no spec — `dependency-auditor`, then the PR gates (one adversarial pass only if Claude changed the diff, ship-gate, merge only if authorized); a hotfix gets an issue, a `hotfix/` branch, and the user may say "no review" for it.
- FR-11: **splitting and forks** — It MUST state:
  - One request = one issue/spec by default, no asking. Only if scope looks big, judge fit with `spec-driven-workflow`; if it clearly doesn't fit one spec, recommend a split and ask; never split without a yes.
  - On a yes: one issue per piece up front, one fork (`subagent_type: "fork"`) per issue in parallel, one worktree per issue written only by its fork. Each fork owns its issue's lifecycle through an opened PR, approves under FR-7 like any session, and merges only with relayed authorization (FR-8). Approvals, change requests and questions go to the owning fork; the coordinator never edits a spec itself.
  - Cross-repo work: one linked issue per repo; the dependency ships before the consumer bumps it.
  - A fork's or background job's final handoff names the issue, PR URL or blocker, spec status, and what it is waiting on.
  - Bugs/follow-ups found along the way are only filed: no fork, assignee, `in progress` label, or same-day work unless the user asks.
- FR-12: **skill application honesty** — It MUST state that when asked whether a skill was used, Claude answers how deeply it was applied (loaded only / references read / outputs used).
- FR-13: **plugin scripts** — It MUST state that skill scripts are run as `python3 -I <literal absolute path>` (the overlay supplies the path; otherwise "Base directory for this skill" + relative path), never via a shell variable or command substitution, and only scripts shipped inside the loaded skill.
- FR-14: **skill authoring** — It MUST state: new skills always use `spec-driven-workflow` with `SPEC.md` beside `SKILL.md`; an existing skill uses it only if a `SPEC.md` is already beside its `SKILL.md` (spec updated first, re-reviewed under FR-7, approval status in the spec's `**Status:**` line, review note saved beside it); skills without one are edited directly; `skill-creator` guides structure and `skill-tester` validates; third-party skills are vetted with `skill-security-auditor` before install; skills under `~/.claude/skills/synced/` are edited only on claude.ai.
- FR-15: **overlay fallback** — It MUST contain one line telling Claude that if a skill loads and no "Skill overlay" block follows it, Claude reads `~/.claude/skill-overlays/<skill-name>.md` itself when that file exists.
- FR-16: **style** — It MUST be written as direct imperatives with a one-clause reason where the reason changes behaviour; no ALL-CAPS emphasis except at most three rules whose violation is irreversible (merge authorization, STOP, never force-push the default branch); no generic advice the model already follows (e.g. "write clean code"); no repo names; no section duplicating another.

### C. Skill overlays

- FR-17: An overlay file MUST exist for each of: `engineering-skills:senior-qa`, `engineering-advanced-skills:api-test-suite-builder`, `engineering-advanced-skills:ci-cd-pipeline-builder`, `engineering-skills:tdd-guide`, `engineering-skills:adversarial-reviewer`, `engineering-advanced-skills:ship-gate`, `engineering-advanced-skills:spec-driven-workflow`. Each overlay MUST contain only: (a) references/scripts to read or run, as paths relative to the skill's base directory; (b) the user's policy for that skill (the clauses FR-1 assigns to it); (c) stack translation where the skill targets another stack. Required content:
  - *senior-qa:* read `references/testing_strategies.md`, `qa_best_practices.md`, `test_automation_patterns.md` before any test plan; the plan visibly states pyramid placement per AC, coverage targets by risk, each flake hazard and how it is made deterministic, isolation and fixtures/factories, behaviour over mocks, tests rewritten/deleted, criteria that are review checks; load again before writing tests, aiming at the riskiest behaviour; React/Jest examples map to Vitest, `@nuxt/test-utils`, `@vue/test-utils`, MSW, Playwright; don't run its React generator scripts.
  - *api-test-suite-builder:* its Auth Test Matrix, Input Validation Matrix and error-code matrix plus `references/example-test-files.md`; apply to Nitro/h3 routes (`server/api/**`).
  - *ci-cd-pipeline-builder:* read `references/pipeline-design-notes.md`, `deployment-gates.md`, `github-actions-templates.md`; run `scripts/stack_detector.py`; the full pipeline-plan checklist from the current CLAUDE.md (stages traced to detected signals with commands confirmed, lint→test→build→deploy, cache keys by lockfile, matrices only with reason, secrets documented never embedded, environment protection + manual prod approval, rollback per deploy job, every pitfall addressed or N/A, pre-merge validation incl. `act`); reload before writing YAML; one optimization at a time.
  - *tdd-guide:* red-green-refactor for all code; specs are not stored in `specs/*.md` and AC IDs are not put in code when work is on an issue (the lifecycle skill's rule).
  - *adversarial-reviewer:* one pass, never re-run (a later change for another reason gets its own pass); spec-CRITICAL definition; handling by severity (CRITICAL fix+raise, WARNING fix or accept with reason, NOTE optional); quota findings ≤ WARNING; persona promotion never creates a CRITICAL on its own; "iterate until green" never means re-running this reviewer.
  - *ship-gate:* run before any self-merge; on block, fix on the PR branch, one adversarial pass if the changeset changed meaningfully, re-run ship-gate; never merge past a block; a pass is not merge authorization.
  - *spec-driven-workflow:* in a GitHub repo the spec lives in the issue body per `github-issue-lifecycle` (not `specs/*.md`); for skills it is `SPEC.md` beside `SKILL.md`; the "big scope" fit judgement per FR-11.
- FR-18: Each overlay MUST be ≤ 700 estimated tokens and MUST NOT restate the skill's own instructions.

### D. Hooks

- FR-18a: **verify the mechanism first** — Before the hook is built, a time-boxed check MUST confirm against the installed Claude Code (2.1.299) that (a) a `PostToolUse` hook with matcher `Skill` fires on a Skill tool call, in the main session and in a subagent, and (b) its `hookSpecificOutput.additionalContext` reaches the model. If (a) or (b) fails, the injection point falls back, in order, to `UserPromptExpansion` / `UserPromptSubmit` (user-typed skills) plus a `PreToolUse` `Skill` hook returning `additionalContext`, and finally to FR-15's fallback line alone. The result is recorded in the spec before implementation; changing the injection event is not a spec change, but dropping deterministic injection entirely is, and goes back to the user.
- FR-19: **PostToolUse injection** — A `PostToolUse` hook with matcher `Skill` MUST run `~/.claude/hooks/skill_overlay.py`, which reads the hook JSON from stdin, takes `tool_input.skill`, and, if `~/.claude/skill-overlays/<skill>.md` exists, returns `hookSpecificOutput.additionalContext` beginning `Skill overlay for <skill>:` followed by the overlay text with every relative reference/script path rewritten to an absolute path under the installed skill's base directory (highest installed version). If no overlay exists it outputs nothing and exits 0.
- FR-20: **user-typed slash commands** — A `UserPromptExpansion` hook (or, if FR-18a shows it doesn't carry the skill name, a `UserPromptSubmit` hook) MUST run the same script in prompt mode: if the prompt starts with `/<skill-name>` for a skill that has an overlay, it injects the same `additionalContext`.
- FR-21: **fail visible, never block** — On any internal error (bad JSON, unreadable overlay, unresolvable skill directory) the script MUST exit 0, inject `Skill overlay for <skill> could not be loaded (<reason>); read ~/.claude/skill-overlays/<skill>.md manually.` when the skill name is known, and write the traceback to stderr. It MUST never block a tool call or prompt.

### E. Rollout

- FR-22: **review stop** — Before install, Claude MUST present to the user: the new CLAUDE.md, the overlays, the clause inventory's `dropped` and `superseded-by` rows, the eval results (AC-6/AC-7), and the token figures; installation waits for the user's go-ahead.
- FR-23: **install & rollback** — Install MUST back up `~/.claude/CLAUDE.md`, `~/.claude/settings.json` and `~/.claude/skills/issue-spec-driven-workflow/` to `~/.claude/backups/claude-md-v2-<timestamp>/`, then write the new files, then add the hook entries via `update-config`. `issue-spec-driven-workflow` is disabled (renamed to `issue-spec-driven-workflow.disabled`), not deleted, until the user confirms after two weeks of use. A single documented `restore.sh` in the backup directory reverses all of it.

## Non-Functional Requirements

### Performance (context budget)
- NFR-P1: The new CLAUDE.md MUST be ≤ 200 lines (official target) and ≤ 1,800 estimated tokens (`prompt_optimizer.py --analyze --json`) and SHOULD be ≤ 1,400 (baseline 2,742). If FR-2…FR-16 cannot fit 1,800, the overage is reported to the user rather than rules being cut.
- NFR-P2: Each overlay ≤ 700 tokens (FR-18). The hook MUST finish in < 500 ms (p95 over 20 runs on this machine) so it adds no noticeable latency.

### Reliability
- NFR-R1: Every skill name in the CLAUDE.md and every overlay filename MUST resolve to an installed skill (plugin cache `*/<version>/skills/<skill>/SKILL.md`, `~/.claude/skills/<name>/SKILL.md`, or `~/.claude/skills/synced/*/<name>/SKILL.md`), and every path an overlay names MUST exist in that skill.
- NFR-R2: The hook MUST NOT hard-code plugin version directories; it resolves the highest installed version at run time.
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
Then every skill name resolves, every overlay path exists in its skill's highest installed version, and an injected unknown name (`engineering-skills:does-not-exist`) makes it exit 1 naming it.

### AC-3: Budget (NFR-P1, NFR-P2, FR-18)
Given the optimizer and the overlays
When measured
Then CLAUDE.md `token_count` ≤ 1,800 (figure reported; > 1,400 flagged), each overlay ≤ 700, and the hook p95 < 500 ms over 20 runs.

### AC-4: Quality rubric (NFR-M3, FR-16)
Given `claude-md-improver/references/quality-criteria.md`
When a fresh subagent scores the new CLAUDE.md
Then the score is ≥ 80, `/doctor prompt-audit` reports no conflicting or outdated instruction in it (or each report is answered in the review note), and the checker finds no ALL-CAPS emphasis beyond three lines, no repo names, no email/token patterns (`[\w.+-]+@[\w-]+\.[\w.]+`, `(ghp_|gho_|github_pat_|sk-|xox[bp]-)[A-Za-z0-9_-]{16,}`).

### AC-5: Overlay injection (FR-17, FR-18a, FR-19, FR-20, FR-21, NFR-S2)
Given a PostToolUse payload `{"tool_name":"Skill","tool_input":{"skill":"engineering-skills:senior-qa"}}`
When `skill_overlay.py` runs
Then stdout is JSON whose `hookSpecificOutput.additionalContext` starts `Skill overlay for engineering-skills:senior-qa:` and contains the absolute path of `references/testing_strategies.md`; a skill with no overlay yields empty output and exit 0; a prompt `/engineering-skills:senior-qa plan tests` in prompt mode yields the same context; malformed JSON, a missing overlay directory, or skill `../../etc/passwd` yields exit 0 and no file read outside the allowed roots.

### AC-6: Behaviour preserved and improved — scenario eval (FR-2, FR-3, FR-4, FR-5, FR-6, FR-7, FR-8, FR-9, FR-10, FR-11, FR-12, FR-13, FR-14, FR-15)
Given `eval/scenarios.json` (from real transcript friction where possible; each FR-2…FR-15 covered at least once; includes "work on #42 in a repo", "fix the flaky test", "Renovate PR", "hotfix", "make a new skill", "edit linux-troubleshooting", "fork merges after green", "STOP mid-job", "Arch boot problem", "selfhost-kit release with an API change")
When a fresh subagent answers each with only the original config, and again with only the new CLAUDE.md (+ the overlay that the hook would inject for any skill the scenario loads)
Then every scenario passing under the original also passes under the new config, and every scenario targeting a coverage-review gap (FR-2, FR-3, FR-5, FR-10, FR-11 cross-repo/handoff) passes under the new one.

### AC-7: Hook works in the real harness (FR-19)
Given the hook installed in a temporary `CLAUDE_CONFIG_DIR`/`HOME` copy of the config
When `claude -p "Load the engineering-skills:senior-qa skill, then list the files you must read before writing a test plan."` runs headless (JSON output)
Then the answer names all three senior-qa reference files by absolute path. (One re-run on failure; fails only if it fails twice.)

### AC-8: Safe install and rollback (FR-22, FR-23, NFR-R3)
Given the user's go-ahead
When install runs
Then backups match the pre-install files byte-for-byte, `~/.claude/CLAUDE.md` equals the deliverable, the settings hooks validate as JSON, and running `restore.sh` in a temp `HOME` restores the originals byte-for-byte.

## Edge Cases

- EC-1: Plugin update changes the version directory or removes a referenced file → hook resolves the highest version (NFR-R2); a missing referenced file is reported inside the injected context ("missing: …") and by the checker (AC-2).
- EC-2: Skill loaded inside a subagent/fork → hooks fire for subagent tool calls as well; AC-7 includes one subagent-invoked load. If the harness does not fire it there, FR-15's fallback line applies and the gap is reported to the user.
- EC-3: The same skill is loaded twice in a session → the overlay is injected again (small, idempotent).
- EC-4: A project `.claude/settings.json` also defines hooks → both run; the overlay hook has no side effects, so order doesn't matter.
- EC-5: Another session edits `~/.claude/CLAUDE.md` or settings between backup and install (the "claude-skills-plugin-usage" session is still open) → install compares live files with the backup and stops if they differ.
- EC-6: `github-issue-lifecycle` isn't built yet → install is blocked (dependency); the checker fails on the unresolved name.
- EC-7: "Manual review" and "no review" both said in a session → "manual review" wins for that issue (FR-7).
- EC-8: Scenario-eval nondeterminism → binary must-include / must-not-include checks; one re-run per failing case; identical policy for old and new config.
- EC-9: A skill is invoked by its short name (e.g. `/senior-qa`) → the hook normalises by matching the overlay whose name ends with `:<short-name>` when exactly one does.
- EC-10: The work machine (Jira-based spec skill) lacks these skills → out of scope (OS-3); the overlay hook is a no-op there.

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
- OS-6: Editing the previous spec in `claude-md-rewrite/`. If the user adopts this spec, that one is marked Superseded by the user's session that owns it.

---

## Test Plan

Built from senior-qa's `testing_strategies.md` (pyramid, decision framework, coverage by risk), `qa_best_practices.md` (testable code via dependency injection, isolation checklist, flaky-test causes) and `test_automation_patterns.md` (factories, fixtures); red-green-refactor via tdd-guide for both scripts. Framework: pytest (`python3 -I -m pytest`).

**Pyramid placement per AC** (logic pushed down; the expensive model-in-the-loop layer only where only a model can show the behaviour):

| AC | Layer | Why |
|---|---|---|
| AC-5, EC-1, EC-3, EC-9, NFR-S2 traversal | Unit (pytest, `tmp_path` skill trees + overlay dirs) | Pure parsing/resolution/path-rewriting; fast, exact; highest-risk code |
| AC-1, AC-2, AC-4 (lint part) | Unit for the rules + one integration run against the real cache and real drafts | Rules are logic; the integration run proves names resolve on this machine |
| AC-3 | Integration: existing `prompt_optimizer.py` + a 20-run timing loop of the hook | Reuse the tool; timing is a measurement, reported not asserted per run |
| AC-6, AC-4 (rubric part) | Model eval: fresh subagent, old vs new | Rule comprehension can only be shown by a model reading the files |
| AC-7 | E2E: one headless `claude -p` journey (+ one subagent load for EC-2) | The single critical journey: hook fires in the real harness |
| AC-8 | Scripted install/restore in a temp `HOME` with `cmp` | Verifies byte-exact backup and rollback without touching the real config |

**Coverage targets by risk:** `skill_overlay.py` path validation, resolution and rewriting — every branch unit-tested (it runs on every Skill call and guards file access); checker rules — every rule has a passing and a failing fixture; CLI argument glue — one integration test each. pytest-cov is not installed, so branch coverage is reviewed against the function list rather than measured.

**Flake hazards and how each is made deterministic:**
- Real plugin cache contents change on update → unit tests use injected roots (`--skill-root`, `CLAUDE_HOME` env injected per test); only the single integration run reads the real cache.
- Model nondeterminism (AC-4, AC-6, AC-7) → binary must-include/must-not-include grading, one re-run, fail only on two failures, same policy for old and new config (EC-8); no assertions on wording.
- Timing (NFR-P2) → measured as p95 over 20 runs and reported; not asserted inside unit tests (no wall-clock assertions).
- Real `~/.claude` mutation → every install/restore test runs against a temp `HOME`.

**Isolation and fixtures/factories:** `make_skill(root, bundle, version, name, files=[])` and `make_overlay(dir, skill, body)` factories build fresh trees under `tmp_path` per test; `hook_payload(kind, **kw)` builds stdin JSON. No shared mutable state; env vars restored per test (monkeypatch).

**Behaviour over mocks:** no mocking — the scripts run as subprocesses against real temp files, asserting on stdout JSON and exit codes.

**Existing tests rewritten or deleted:** none for this deliverable. `issue-spec-driven-workflow/tests/` remain with the disabled skill (FR-23) and are handled by the lifecycle skill's spec.

**Review checks rather than tests:** wording fidelity of moved clauses beyond `mustAppear`; NFR-M1 duplication beyond the inventory; FR-16 tone; the user's taste in structure (FR-22 review stop).

## Open decisions for the user

- **D-1:** The coverage-review fixes (W1–W5, W7, N1–N3) were never confirmed in the previous session. This spec includes all of them (FR-2, FR-3, FR-5, FR-8, FR-10, FR-11, FR-14, FR-4 ADR line), on the strength of "I am fine with changing details about how I work". Strike any you don't want.
- **D-3:** Optional enforcement hook for the rule that was broken on 10-12 (a fork merged unasked): a `PreToolUse` hook on `Bash` with `if: "Bash(gh pr merge *)"` returning `permissionDecision: "ask"`, so every merge needs a click even when Claude believes it is authorized. Safer, but adds a prompt to merges you've pre-authorized. Not included unless you say so.
- **D-2:** Adopt this spec in place of `claude-md-rewrite/SPEC.md` (OS-6)?
