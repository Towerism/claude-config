# ADR-0006: How changes to this repo are made, versioned and rolled back

**Status:** Accepted by the owner on 2026-10-16 (proposed 2026-10-15).

Changes to this repo go through the owner's normal development pipeline. Commits follow Conventional Commits, but the repo publishes no tags or releases. A bad change is undone with `git revert`, and when a deploy has broken Claude Code itself, `cfg rollback` switches back to the previous release.

Terms such as `cfg`, release worktree, `current`, first deploy, overlay and scenario eval are defined in the [Terms section of the architecture doc](../architecture/README.md#terms).

## Context

The owner's global `~/.claude/CLAUDE.md` sets a pipeline for every code change in a GitHub repo:

1. The lifecycle skill (`github-issue-lifecycle`) opens or claims the issue.
2. A spec goes on the issue, after the approval mode has been settled.
3. `senior-qa`, or `ci-cd-pipeline-builder` for CI work, plans the tests.
4. The code is written test-first, with `tdd-guide`.
5. One adversarial review runs on the spec, and one on the PR diff.
6. `ship-gate` runs.
7. Claude merges only after the owner confirms the merge prompt.

This repo follows that pipeline. That leaves three questions specific to this repo. Do tags and version numbers earn their keep when one person uses the config? How does a change to the global CLAUDE.md show that it doesn't make Claude behave worse? And now that git holds the source of truth, how does rollback relate to the `restore.sh` scripts the old installer wrote?

## Options

### Versioning

1. semantic-release (Node). It generates tags and a changelog from commit types. It adds Node to an otherwise Python-only CI, which the pipeline-design notes list as a common pitfall. It also needs a write token and adds run time. Its only consumer here would be the question "which version is deployed?", and the deployed commit SHA already answers that.
2. python-semantic-release. The same benefit in Python, but still a release job with write permissions to maintain.
3. No tags or releases for now, with Conventional Commits still enforced on every commit. `cfg` records the deployed commit SHA. A release tool can be added later without rewriting history.

### Rollback

1. Keep `restore.sh` as the main rollback. It isn't git-native, and afterwards the history still says the bad change is current.
2. Git only: revert, then deploy. This fails when the breakage stops Claude Code itself from working, so Claude can't help make the revert.
3. `git revert` as the normal path, `cfg rollback` as the emergency path, and `cfg deploy --ref` as a bridge. `cfg rollback` is a single symlink swap back to the previous release.

## Decision

### The pipeline in this repo

The repo's own `CLAUDE.md` maps each pipeline stage to this repo:

| Stage | In this repo |
|---|---|
| Start | The lifecycle skill creates or claims the issue, sets its labels (`draft`, `approved`, `approval: *`, `in progress`, `hotfix`; plan issue 1 creates them), and names the branch `{type}/{N}-{slug}`. |
| Spec | The approval mode is settled first. The global rule offers manual review with an adversarial pass as the default; for the build-plan issues the owner chose adversarial approval ([plan](../architecture/plan.md#approval-mode)). Changes to how claude-config works or coordinates the skills (`cfg`, CI, CLAUDE.md, hooks, overlays, settings) keep their spec in the issue body, and the same PR brings `config/SPEC.md` up to date. Changes to how a skill works keep their spec in `config/skills/<skill>/SPEC.md`, created with the first change if the skill has none, and the issue links to it ([ADR-0002](0002-repo-layout.md)). Files under `docs/history/` are never edited. |
| Test plan | `senior-qa` for tools, hooks and the checker. `ci-cd-pipeline-builder` for `.github/workflows/*` and the scripts CI calls: the checker, the eval-record verifier and the lifecycle skill's scripts. |
| Implement | `tdd-guide`, red-green-refactor |
| Review | One adversarial review of the spec, and one of the full PR diff. The PR description lists the findings and how each was handled. |
| Gate | `pytest`, ruff, mypy, `cfg check` and any required `cfg eval` records, with results quoted before work is reported done. Then `ship-gate`. `gh pr checks <N>` must be green; the ruleset on `main` enforces this too. |
| Merge | Only with the owner's authorization, through the merge-confirm hook. Merge commit or rebase, never squash. The branch is deleted afterwards. |
| Deploy | After the merge, `cfg deploy` runs on each machine, by the owner or by Claude when asked. Deploying is never part of the merge itself. |

### Commit types

Commit types follow Conventional Commits and are chosen by release impact:

| Type | Used for |
|---|---|
| `feat` | A new rule, overlay, skill, hook behaviour or `cfg` command |
| `fix` | A correction to behaviour, including reverting a regression |
| `docs`, `test`, `chore(deps)` | No behaviour change. A plugin lock bump is `chore(deps)` unless overlays had to change with it. |
| `!` plus a `BREAKING CHANGE:` footer | Anything that needs manual action on a machine beyond running `cfg deploy`: a new owned settings key that collides with an existing value, a renamed managed path, or a changed state-file format |

### Versioning

The repo takes option 3: no tags and no releases. The global rule that only semantic-release creates tags is satisfied by creating none.

### Showing that a CLAUDE.md change doesn't regress

Four checks cover a change to the global CLAUDE.md, two in CI and two run locally.

1. Clause inventory (CI). `eval/clauses.json` becomes a living registry of the rules. Each row names a rule, the file it lives in, and a key phrase (`mustAppear`) that must still be present there. A PR that moves or removes a rule has to change that row's disposition to `dropped:<reason>` or `superseded-by:<ref>`, so the loss shows in the diff. A new rule should get a new row; CI can't detect a missing row, so the reviewer checks for it.
2. Scenario eval (local, its record verified by CI). The old arm uses the merge-base's `config/CLAUDE.global.md` and overlays, taken with `git show`; the new arm uses HEAD. The pass rule comes from the v2 spec's behaviour-eval criterion (AC-6). No scenario that passes under the old config may fail under the new one, and every scenario written for a new rule must pass. A PR that adds a rule adds or updates a scenario for it.
3. Budgets and lint (CI). CLAUDE.md stays within 200 lines and 1,800 estimated tokens, with a target of 1,400. Each overlay stays within 700 tokens. Exactly one line is emphasised, the merge rule. No repo names, email addresses or token patterns appear.
4. Quality rubric (local, CLAUDE.md changes only). The score must be at least 80, with one re-run allowed.

### Rollback

The repo takes option 3. The rollback section of the [architecture doc](../architecture/README.md) walks through each case.

- **Normal:** `git revert` on a `fix/` branch, then PR, merge and `cfg deploy`.
- **Emergency:** `cfg rollback` points `current` back at the previous release. If `cfg` can't run, `ln -sfn` does the same by hand. Undoing the first deploy itself uses the `restore.sh` written into that deploy's backup. That script is standalone stdlib Python and keeps the v2 installer's restore rules (v2 spec FR-23). It acts only on the paths in its manifest, checks hashes first, and removes only its own settings entries, so it works without the repo and without Claude.
- **Bridge:** `cfg deploy --ref <sha>` deploys a known-good commit. `cfg status` flags it until `main` contains the same fix.

## Consequences

Without tags, the changelog is `git log --oneline`. That stays readable because every commit is conventional and ends with its issue number, `(#N)`.

Every change to the repo pays the full pipeline cost, one-line fixes included. The owner set that rule deliberately. The "no review" approval mode exists for small specs, and even then the PR-diff review still runs.

## Revisit when

- A second consumer appears: another person, or a plugin build as described in [ADR-0001](0001-deployment-model.md). Adopt python-semantic-release then.
- The owner wants `cfg status` to say "the work machine is 3 releases behind" rather than "at <sha>".
