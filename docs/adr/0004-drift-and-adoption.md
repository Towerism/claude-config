# ADR-0004: Drift and adoption (hash-verified adoption that links after a warning and a backup; drift read from the release worktree)

**Status:** Accepted by the owner on 2026-10-16 (proposed 2026-10-16).

On a machine that already has the config installed, the first deploy proves by sha256 that the repo holds exactly what is live. It then replaces the live files with symlinks, after a warning and a backup. After that, `cfg status` reports drift, meaning live changes the repo doesn't have. Drift is never overwritten silently: it either goes back into the repo through a reviewed commit, or is discarded on an explicit command. Terms used here are defined in the [architecture overview](../architecture/README.md#terms).

## Context

The config was installed on 2026-10-15 by the v2 installer, and on that date all 34 files in its sha256 manifests matched what was live. Adoption is the first contact between `cfg` and that install. It has to show by hash that the repo matches the live config. Then, following the owner's decision of 2026-10-16 on symlinks ([ADR-0001](0001-deployment-model.md)), it replaces the live files with links, warning and backing up first.

After adoption, the live config can change without going through the repo:

- `/revise-claude-md` edits `CLAUDE.md`, now through the link;
- a tool may replace a link with a plain file;
- the harness toggles entries in `enabledPlugins`;
- a plugin updates itself, since the owner chose on 2026-10-16 to keep marketplace auto-update on;
- claude.ai skill sync adds a skill whose name collides with a repo skill.

The owner needs to see each change, know which direction it went, and turn the wanted ones into commits.

## Options

For adoption:

1. Reinstall over the top. This hides any mismatch and writes the same content again for no reason.
2. Trust the repo and create the links without checking. Nothing is verified.
3. Verify that the repo, the v2 manifest and the live file all have the same sha256, then warn, back up and link. On any mismatch, abort before changing anything.

For drift:

1. The repo always wins, and deploy overwrites live edits. Work gets lost without warning.
2. The live config always wins, and live edits are committed automatically. Unreviewed changes then reach `main` without passing the eval gate.
3. Detect drift, refuse to deploy over it, and reconcile explicitly, either with `cfg capture` (live edits go onto a branch) or with `--discard-live`. A discarded edit stays in the old release worktree instead of being destroyed.

## Decision

Option 3 for adoption. `cfg adopt --host personal` works in six steps:

1. It refuses to run if a deploy state already exists.
2. It creates the first release worktree at HEAD and reads every `~/.claude/backups/claude-md-v2-*/{deliverable,bundle}-manifest.sha256`.
3. It checks each managed path. A path with a manifest entry needs the repo sha, the manifest sha and the live sha to be equal. Some files have no manifest entry because the v2 installer never wrote them: the lifecycle skill's `SPEC.md` and `REVIEW-NOTE.md`, and linux-troubleshooting. Those need the repo sha to equal the live sha, and are reported separately. For settings, the rendered fragment must mean the same as the live owned keys. `__pycache__/`, `.pytest_cache/` and `*.pyc` are ignored on both sides.
4. On any mismatch it aborts with the list, having changed nothing.
5. If everything matches, it prints the list of every regular file and directory it is about to replace and asks to continue. It then moves each one into `~/.claude/backups/claude-config/<timestamp>/` with a manifest, creates the links, writes the state file, and runs the hook smoke test through the links. `settings.json` is not rewritten, because its entries already match.
6. It reports how many paths were adopted by manifest, how many by direct comparison, and how many were linked, along with the backup location and its `restore.sh`.

Skill directories that aren't listed in the host profile are invisible to `cfg`, which never reads, reports, copies or touches them. Adoption also changes nothing in the `claude-md-v2-*` backup. The owner doesn't intend to return to the v1 config, so after adoption that backup and the retired skill in `~/.old-claude/skills/issue-spec-driven-workflow/` serve no rollback purpose. On 2026-10-16 the owner told Claude to delete both once adoption passes on the personal machine; issue 7 does it, listing the two paths first. See the cleanup question in [plan.md](../architecture/plan.md).

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
