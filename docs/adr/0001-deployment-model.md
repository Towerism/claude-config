# ADR-0001: Deployment model (symlinks into immutable release worktrees, with `settings.json` merged by `cfg`)

**Status:** Accepted by the owner on 2026-10-16 (proposed 2026-10-16).

The repo's files reach the live config (`~/.claude`) as symlinks that point into a read-only snapshot of a deployed commit, called a release worktree. `settings.json` is the exception: Claude Code rewrites that file itself, so `cfg`, the repo's deploy tool, merges the repo's keys into it instead of linking it. Terms used here are defined in the [architecture overview](../architecture/README.md#terms).

## Context

The repo has to put files into `~/.claude` and keep a few keys in `~/.claude/settings.json` up to date. Three other writers share that settings file: the Claude Code harness (it records enabled plugins and permissions), the `/plugin` command, and the owner editing by hand. The repo must therefore own only its own keys, merge them atomically, and never overwrite the whole file.

The installer from the previous project, `install_v2.py`, has safety properties that must survive. It aborts before changing anything when preflight checks fail, and it backs up before writing. It merges settings atomically, records sha256 manifests of what it wrote, and has a restore that never destroys data. These come from the installer requirement in the v2 spec (FR-23). The v2 spec is the approved spec for the layered CLAUDE.md, now kept in `docs/history/claude-md-v2/`.

Two earlier decisions narrow the options. The v2 spec rejected a personal plugin as "packaging overhead for one user". And the lifecycle skill, `github-issue-lifecycle`, which runs the GitHub side of each issue, must live at `~/.claude/skills/github-issue-lifecycle/` because its own spec says so (FR-1).

On 2026-10-16 the owner decided that deployed files should be symlinks. Anything already in a target's place is to be warned about and backed up before it is replaced. The owner set the scope as "everywhere it makes sense", so deploys stay both easy and simple.

## Options

| Option | How `settings.json` is handled | For | Against |
|---|---|---|---|
| A1. Symlinks into the working clone (`~/dev/claude-config`), via GNU stow or a small linker | It can't be linked: the harness rewrites it, and the installer's preflight already refuses a symlinked settings file. A separate merge is needed. | Simplest. Live edits show up in `git status` immediately. | Checking out a branch, or saving a half-finished edit in the clone, changes the live config in the middle of a session (threat T5 in the architecture overview). Stow also needs repo file names to match live names, but the global file is stored as `CLAUDE.global.md` ([ADR-0002](0002-repo-layout.md)). |
| A2. Symlinks into immutable release worktrees, switched atomically | Merged by `cfg`, as in B | The live config changes only when a commit is deployed. Rollback is one symlink swap. Edits made through a link land in a git worktree, where `git status` shows them as drift. No copies to keep in sync. Matches the owner's 2026-10-16 decision. | Depends on Claude Code following symlinks for skills, CLAUDE.md and hooks, which plan issue 14 checks. Tools that save by writing a temporary file and renaming it can replace a link with a plain file, so `cfg status` must detect that. |
| B. A copy-based installer grown from `install_v2.py` | Parse the file, change only the owned keys, write it atomically | The existing installer tests carry over almost unchanged. No dependence on symlink behaviour. | Contradicts the owner's decision. Rolling back means copying files back. |
| C. chezmoi | A `modify_` script that merges JSON | Mature templating, per-host data, a built-in diff | A new binary on both machines, and the safety properties would have to be rebuilt inside chezmoi scripts. It is built for managing a whole home directory, which is more than one person with two machines needs. |
| D. A personal Claude Code plugin | The installer still merges `enabledPlugins` and the marketplace entry | Hook paths become portable through `${CLAUDE_PLUGIN_ROOT}` | Plugin skills get a namespace prefix, which breaks the lifecycle skill's required location, the skill names in CLAUDE.md, and the trigger evals. The harness copies plugins into its own cache, so every update needs a version bump. It also contradicts the v2 spec's rejected alternatives. |

## Decision

Option A2. `cfg` is a Python command-line tool in `tools/cfg/`, using only the standard library and grown from `install_v2.py`. It deploys by symlink and merges settings.

### Where releases live

Releases sit outside `~/.claude`, under `${XDG_DATA_HOME:-~/.local/share}/claude-config/`:

```text
releases/<sha>/      git worktree of the clone, detached at that commit (one per deployed commit)
current -> releases/<sha>
```

### What gets linked

Every link goes through `current`, so the links never change after they are first made:

| Live path | Link target |
|---|---|
| `~/.claude/CLAUDE.md` | `…/claude-config/current/config/CLAUDE.global.md` |
| `~/.claude/skill-overlays` | `…/current/config/skill-overlays` (a directory link) |
| `~/.claude/hooks/skill_overlay.py`, `merge_confirm.py` | `…/current/config/hooks/<file>` (file links, so other files in `hooks/` are left alone) |
| `~/.claude/skills/<name>`, for each skill in the host profile | `…/current/config/skills/<name>` (a directory link) |

Overlays are per-skill policy files that a hook injects when the matching skill loads. A host profile (`hosts/<name>.json`) lists which skills and components a machine gets ([ADR-0003](0003-machine-portability.md)). The hook commands in `settings.json` keep pointing at `~/.claude/hooks/<file>.py`, so they don't change, and the existing hook entries already match.

### How a deploy runs

1. Create the release worktree for the target commit with `git worktree add --detach`.
2. Run `cfg check` and a smoke test of the hooks against the new release.
3. Swap `current` atomically: create a temporary symlink, then `os.replace` it over `current`.
4. Merge the settings keys.
5. Write the deploy state file.

The previous release stays on disk, which makes rollback another swap. `cfg` keeps the last five releases, plus any release with uncommitted edits; those are never pruned.

### Replacing files that are already there

This rule applies during the first deploy on a machine that already has the config installed ([ADR-0004](0004-drift-and-adoption.md)). It also applies whenever a target path holds a regular file or directory instead of the expected link:

1. Warn first. List every such path, say whether its sha256 matches the repo's version, and ask before continuing. `--yes` skips the question.
2. If the content differs from the repo, stop. The path has to go through `cfg capture` first, or be passed explicitly with `--discard-live <path>`, so live edits are never lost by accident.
3. Back up before deleting. Move each path into `~/.claude/backups/claude-config/<timestamp>/`, keeping its relative path, and record its sha256 in a manifest. The move is a rename on the same filesystem, so it is atomic.
4. Create the link. For a file, a temporary link is renamed over it, which is atomic. A directory is moved into the backup first and then linked, which leaves a gap of a few milliseconds.
5. Write `restore.sh` into the backup directory. It moves the originals back and removes the links, but only where each link still points where `cfg` put it, with the same hash checks as the v2 installer.

### Merging `settings.json`

`settings.json` is never linked. `config/settings.fragment.json` lists every key the repo owns:

```json
{
  "enabledPlugins": { "engineering-skills@claude-code-skills": true, "…": true },
  "extraKnownMarketplaces": { "claude-code-skills": { "source": { "source": "github", "repo": "alirezarezvani/claude-skills" } } },
  "hooks": {
    "PostToolUse":         [{ "matcher": "Skill", "hooks": [{ "type": "command", "command": "python3 -I {claude_home}/hooks/skill_overlay.py" }] }],
    "UserPromptExpansion": [{                     "hooks": [{ "type": "command", "command": "python3 -I {claude_home}/hooks/skill_overlay.py" }] }],
    "PreToolUse":          [{ "matcher": "Bash",  "hooks": [{ "type": "command", "command": "python3 -I {claude_home}/hooks/merge_confirm.py" }] }]
  }
}
```

The repo owns individual entries: each `enabledPlugins.<id>`, each `extraKnownMarketplaces.<name>`, and each hook handler, identified by its event, matcher and rendered command. `cfg` writes nothing else. It sets the owned values and makes sure exactly one matching hook handler exists. `install_v2.py` appended handlers unconditionally, so running it twice would have duplicated them. An owned item that has left the fragment is removed only if its live value still equals what was last deployed.

`cfg` writes the file only when the result differs in meaning from what is live. It writes a temporary file in the same directory, flushes it to disk, and checks the live file's hash again. If the harness wrote in between, it aborts. Otherwise it replaces the file with `os.replace`. Preflight refuses a settings file that is a symlink, doesn't parse, or isn't a JSON object. CI validates the fragment against `schemas/fragment.schema.json` and against a vendored snapshot of the Claude Code settings schema.

### Safety properties kept from the v2 installer

Each of these keeps a test:

- preflight runs before any change;
- files are backed up before they are deleted or replaced;
- any error after the first change exits non-zero and names `restore.sh`;
- the settings merge is atomic and keeps every key the repo doesn't own;
- sha256 manifests record everything moved aside;
- restore never destroys data: it is hash-checked and touches only the paths in its manifest and its own settings entries.

### Checking symlink behaviour first

Plan issue 14 runs a time-boxed check against the installed Claude Code before `cfg` is built. It uses temporary config directories, the same way the v2 project's mechanism check verified hook behaviour (v2 spec FR-18a). It verifies five things:

1. Skills in symlinked directories are discovered, judged from the skill list in the `system`/`init` event, in both the main session and a subagent.
2. A symlinked `~/.claude/CLAUDE.md` loads, proved with a canary line.
3. Hooks run through symlinked scripts, and the overlay hook can read a symlinked `skill-overlays/`.
4. Claude's Edit and Write tools either write through a symlink or replace it with a plain file. The check records which.
5. claude.ai skill sync and `/plugin` leave the links alone.

If check 1, 2 or 3 fails, that component is copied instead, with the same backup rules as option B, and the result is recorded in this ADR. The result of check 4 decides how `cfg status` reports drift ([ADR-0004](0004-drift-and-adoption.md)).

#### Results (2026-10-18, Claude Code 2.1.299)

Every component works through a link, so nothing is copied and the link table above stands. The check is `eval/symlink_check.py`. It built this layout in temporary config directories and ran headless `claude -p` five times: once in the main session, three times with a subagent (two attempts through links, then one with plain files), and once for Edit and Write. Its evidence is in [`docs/evidence/symlink-check.md`](../evidence/symlink-check.md), from commit `7cb5f7e`. An earlier run on `41f78e1` gave the same results. A run before that crashed on a parser bug, after five model runs, and recorded nothing.

| What was checked | Main session | Subagent |
|---|---|---|
| A skill in a linked directory is listed and loads (check 1) | yes | yes |
| A linked `CLAUDE.md` loads (check 2) | yes | yes |
| The overlay hook runs through its linked script and reads the linked `skill-overlays/` (check 3) | yes | yes |
| `SessionStart` hook context reaches the model | yes | no, with links or with plain files |

| Edit and Write (check 4) | Outcome |
|---|---|
| Edit on a file that is a link | refused |
| Write on a file that is a link | refused |
| Edit on a file inside a linked directory | written through to the target |

| Other tools (check 5) | Outcome |
|---|---|
| claude.ai skill sync during a headless run | it ran, created `skills/synced/`, and left every link alone |
| `claude plugin marketplace add`, `install`, `uninstall`, `marketplace remove` | every link and every settings key outside `enabledPlugins` and `extraKnownMarketplaces` unchanged |

What this means for `cfg` (#6) and the first deploy (#8):

- **Edit and Write never replace a link.** Claude Code refuses to write a path that is itself a symlink. Its error says "Write target is a symbolic link" and names the target path. So when Claude edits `~/.claude/CLAUDE.md`, the edit lands only if Claude then writes to the target path inside the release worktree, which `git status` shows as drift. Edits to files inside a linked skill directory go straight through to the release worktree. Either way, `cfg status` finds Claude's edits as `git status` changes in the release worktree. It still has to look for a regular file where a link should be, because other editors save by renaming a new file over the old one.
- **Skills see the link path.** The "Base directory for this skill" shown to the model is the path under the config directory, not the release path. So the overlay hook's rewritten paths and any skill script paths stay under `~/.claude/skills/<name>/` and survive a release swap. The subagent's stream doesn't include that message, so it was only checked in the main session.
- **`SessionStart` doesn't reach subagents**, whether or not anything is linked. [ADR-0011](0011-tracker-agnostic-lifecycle.md) records what that means for #15.
- **Interactive checks move to #8.** The interactive `/plugin` menu, and claude.ai sync in a logged-in interactive session, are checked by hand during #8's first deploy. Run `python3 -I eval/symlink_check.py --inventory ~/.claude` before and after one interactive session, then compare the two outputs.

## Consequences

The first deploy on a machine that already has the config is not free of changes. Regular files become links after a warning and a backup, and any file whose content differs from the repo needs the owner's decision first. `settings.json` changes only where the repo's owned keys differ from the live ones.

Edits made in the live config land in the current release worktree, where `git status` shows them. Claude's Edit and Write tools refuse to write through a file link such as `~/.claude/CLAUDE.md` and point at the target path instead; edits inside a linked skill directory go through (see the results above). The working clone is unaffected.

Deleting or moving the working clone doesn't break the live config, because the release worktrees hold real files. Only `git` commands inside them stop working until the clone is back.

The tests from `install_v2.py` seed `tests/test_settings_merge.py` and the backup and restore tests. Its copy-specific tests are replaced by tests for linking and backup.

## Revisit when

- The symlink check (plan issue 14) shows that a component doesn't work through a link. Copy that component, as described above.
- Claude Code changes version. Run `python3 -I eval/symlink_check.py` again before a deploy that depends on these results.
- A third machine or a second person needs this config. A plugin (option D) then pays for its packaging cost.
- Claude Code adds a supported way to load user skills or memory from another directory, or a way to include settings from another file.
