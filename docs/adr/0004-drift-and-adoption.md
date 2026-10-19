# ADR-0004: Drift and the first deploy (the first deploy warns, backs up and links; drift read from the release worktree)

**Status:** Accepted by the owner on 2026-10-16 (proposed 2026-10-16). Revised on 2026-10-18, when the owner dropped hash-verified adoption and chose the first deploy for a machine that already has the config installed ([plan Q-19](../architecture/plan.md#open-questions)).

On a machine that already has the config installed, the first deploy compares every managed path with the repo. It stops on any difference until the owner decides about each one, then replaces the live files with symlinks, after a warning and a backup. After that, `cfg status` reports drift, meaning live changes the repo doesn't have. Drift is never overwritten silently: it either goes back into the repo through a reviewed commit, or is discarded on an explicit command. Terms used here are defined in the [architecture overview](../architecture/README.md#terms).

## Context

The config was installed on 2026-10-15 by the v2 installer, and on that date all 34 files in its sha256 manifests matched what was live. The first contact between `cfg` and that install has to take it over without losing anything. Then, following the owner's decision of 2026-10-16 on symlinks ([ADR-0001](0001-deployment-model.md)), it replaces the live files with links, warning and backing up first.

By 2026-10-18 the live config no longer matched those manifests. The owner had edited the live `~/.claude/CLAUDE.md` (#28), and merged issues had changed files the manifests list (#35). A check that the repo, the manifest and the live file all have the same hash would abort on every run.

After the first deploy, the live config can change without going through the repo:

- `/revise-claude-md` edits `CLAUDE.md`, now through the link;
- a tool may replace a link with a plain file;
- the harness toggles entries in `enabledPlugins`;
- a plugin updates itself, since the owner chose on 2026-10-16 to keep marketplace auto-update on;
- claude.ai skill sync adds a skill whose name collides with a repo skill.

The owner needs to see each change, know which direction it went, and turn the wanted ones into commits.

## Options

For the first contact with an installed machine:

1. Reinstall over the top. This hides any mismatch and writes the same content again for no reason.
2. Trust the repo and create the links without checking. Nothing is verified.
3. Adoption: verify that the repo, the v2 manifest and the live file all have the same sha256, then warn, back up and link. On any mismatch, abort before changing anything.
4. The first deploy: compare each live path with the repo, stop on any difference until the owner decides about that path, then warn, back up and link, using [ADR-0001](0001-deployment-model.md)'s rule for files that are already there.

For drift:

1. The repo always wins, and deploy overwrites live edits. Work gets lost without warning.
2. The live config always wins, and live edits are committed automatically. Unreviewed changes then reach `main` without passing the eval gate.
3. Detect drift, refuse to deploy over it, and reconcile explicitly, either with `cfg capture` (live edits go onto a branch) or with `--discard-live`. A discarded edit stays in the old release worktree instead of being destroyed.

## Decision

Option 4 for the first contact, decided on 2026-10-18. The owner chose option 3 on 2026-10-16; it is superseded because the v2 manifests no longer match the live config (#28, #35), so adoption would always abort, and because option 4 already protects everything adoption protected. Issue 7, which was to build `cfg adopt`, is dropped.

The first deploy is `cfg deploy --host personal` on a machine with no deploy state yet. It works in six steps:

1. It sorts every managed path into one of four kinds: missing; a link already pointing where `cfg` would put it; a plain file or directory matching the repo; or a plain file or directory that differs from the repo. It also compares the rendered settings fragment with the live owned keys. `__pycache__/`, `.pytest_cache/` and `*.pyc` are ignored on both sides.
2. If any path or owned settings key differs, it stops before changing anything. It shows a unified diff for each difference and says how to resolve it.
3. The owner resolves each difference. `cfg capture <path>` copies the live version onto a feature branch, where it goes through review like any other change. `--discard-live <path>` accepts the repo's version, and the live file still goes into the backup. A difference in the owned settings keys is resolved the same way, with `settings.json` as the path: `cfg capture settings.json` writes the live values back into `config/settings.fragment.json` on a branch, and `--discard-live settings.json` lets the repo's values win.
4. It lists every file and directory it is about to replace and asks to continue. `--yes` skips the question, but never a difference from step 2.
5. It backs up, links (or copies, for any component the symlink check in issue 14 records as copied), merges the owned settings keys, writes the state file and runs the hook smoke test, as the [deploy section of the architecture overview](../architecture/README.md#deploying) describes.
6. It reports what it linked, what already matched, what was discarded, and where the backup and its `restore.sh` are.

What the first deploy guarantees:

- Nothing is replaced or deleted without first being moved into `~/.claude/backups/claude-config/<timestamp>/` and recorded in its manifest.
- No path whose content differs from the repo is replaced without the owner's explicit decision for that path.
- `settings.json` is backed up before it is written, and only the owned keys change.
- `restore.sh` in the backup returns the machine to plain files, checking hashes first.
- Skill directories that aren't listed in the host profile are invisible to `cfg`, which never reads, reports, copies or touches them.
- The `claude-md-v2-*` backup is left alone by `cfg`. The owner doesn't intend to return to the v1 config, so once the first deploy passes on the personal machine, that backup and the retired skill in `~/.old-claude/skills/issue-spec-driven-workflow/` serve no rollback purpose. Issue 8 lists the two paths and deletes them after the owner's go-ahead. See Q-2 in [plan.md](../architecture/plan.md#open-questions).

Option 3 for drift. `cfg status` reports these states:

| What `cfg` sees | State | What to do |
|---|---|---|
| Links intact, `current` is the release for HEAD, release worktree clean | clean | Nothing |
| `main` is ahead of the deployed release | behind | `cfg deploy` |
| `git status` in the current release worktree shows changes, made through a link | drift | `cfg capture [path]` on a feature branch, which applies the release worktree's diff to the clone. Or `cfg deploy --discard-live`, which deploys a new release and leaves the edited one on disk. |
| A managed path is a regular file or directory, not a link, because a tool replaced it | unlinked | Compare the content, then capture it or relink it after a backup (the rule in ADR-0001) |
| A managed link is missing or points somewhere else | broken | `cfg deploy` creates the link again, warning first if something is in the way |
| Owned settings keys differ from the last deploy | settings drift | Capture them into the fragment, or deploy again |

`cfg status` also reports three other conditions. A plugin's version may differ from the plugin lock (`plugins.lock.json`, which records installed plugin versions). A synced skill may have the same name as a repo skill. And `current` may point at a commit other than HEAD, as happens after `deploy --ref` or a rollback. `cfg diff [path]` shows unified diffs of the live config against the deployed commit; for settings it covers only the owned keys.

`cfg capture` refuses to run on `main`; it needs a branch created through the lifecycle skill's flow. It never copies unmanaged paths. For settings it writes the values back into the fragment and puts the `{claude_home}` placeholder back in. It prints which eval suites the change will require. The change then goes through commit, review and the eval gates like any other, and the next deploy brings everything back to clean.

The repo is the source of truth, but nothing live is lost without an explicit command. A discarded edit survives in its old release worktree until that release is pruned, and a release with uncommitted edits is never pruned automatically. Nothing commits itself, and nothing runs on a schedule.

## Consequences

`/revise-claude-md` keeps working, provided the symlink check in plan issue 14 confirms it writes through links. The owner runs it as before, then `cfg capture`, and the edit gets the clause check and the scenario eval like any other change. (The scenario eval runs headless `claude -p` against the old and new CLAUDE.md and compares the results.)

Detecting drift comes down to `git status` plus checks on the links, which is simpler than the three-way hash comparison a copy-based deploy would need.

Each machine has its own releases and its own state. The repo doesn't record what each machine has deployed. `cfg status` on each machine answers that, which the owner judged enough on 2026-10-16.

## Revisit when

- The symlink check shows that Claude's Edit tool replaces links instead of writing through them. "Unlinked" would then be the normal state after any live edit, and `cfg capture` should treat it as the main case.
- Drift turns out to be frequent and harmless. Consider a `cfg capture --pr` that opens the issue and the PR itself.
