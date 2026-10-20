# claude-config runbook

This runbook is for the owner, or anyone setting up a machine for them. It covers running claude-config day to day: setting up a clone, deploying, handling drift, rolling back and undoing, and adding a second machine. Most of it changes your live Claude Code config in `~/.claude`; each section says when it does. To learn the commands without touching anything live, start with [Practice on a trial directory](#practice-on-a-trial-directory).

**Last verified:** 2026-10-19, against commit `80285b7` and Claude Code 2.1.299. Every command that changes something was run on a `--target` trial directory, not the live config, except where a section says otherwise.

Why things work this way is in the [architecture overview](architecture/README.md) and the ADRs it links. This page only says what to type and what to expect.

## Before you start

`cfg` is this repo's deploy tool. It isn't on your `PATH`: every command below runs from the root of your clone as `python3 -I tools/cfg/cli.py <command>`. The code that runs is your clone's working tree. What gets deployed is a commit: `origin/main` as last fetched, or the one you pass with `--ref`. A `git pull` updates both.

A machine needs git, the GitHub CLI `gh` logged in, Python 3.13 (the version in `.python-version`), and the `claude` CLI logged in.

`cfg` manages exactly these, and nothing else:

| What | Where |
|---|---|
| The paths the host profile lists: `CLAUDE.md`, `skill-overlays/`, the hook scripts and the repo's skills | `~/.claude/`, as links into the deployed release |
| The owned keys in `settings.json`: the plugins it enables, one marketplace and the three hook entries | `~/.claude/settings.json`, merged, never replaced |
| Deploy state and the release worktrees | `~/.local/share/claude-config/` |
| A backup for each deploy that moved or changed something | `~/.claude/backups/claude-config/<timestamp>/` |

Skills that aren't in the host profile, such as `synced/` or a skill you added by hand, are never read, copied or touched.

Every command takes `--json` for machine-readable output and `--target <dir>` to work on a temporary config directory instead of `~/.claude`. With `--target`, deploy state goes in `<dir>-cfg-data`.

Exit codes:

- 0: done, or nothing to report.
- 1: something to report, or the deploy stopped before changing anything.
- 2: a usage error, or refused before changing anything.
- 3: a deploy or rollback failed after it started changing the live config. See [If a deploy exits 3](#if-a-deploy-exits-3).

## Set up a clone

Do this once for each new clone. There is no `cfg bootstrap` command yet (#50), so the four steps are manual. They're the same as in the repo's `CLAUDE.md`.

1. Set the commit identity this repo requires. Its pre-commit hook refuses any other address.

   ```sh
   git config user.email 2057426+Towerism@users.noreply.github.com
   git config user.name "Martin Fracker, Jr."
   ```

2. Turn on the repo's git hooks:

   ```sh
   git config core.hooksPath .githooks
   ```

3. Create the development environment:

   ```sh
   python3 -m venv .venv && .venv/bin/pip install --require-hashes -r requirements-dev.txt
   ```

4. Create the excluded-terms list at `~/.config/claude-config/deny-terms`, one term per line. The pre-commit hook refuses any staged file that contains a term from it. The list stays outside the repo: never print it, quote it or commit it.

To check: `git config core.hooksPath` prints `.githooks`, and `.venv/bin/python -I -m pytest -q` passes.

## Deploy

### An ordinary deploy, after a change merges

Run this after any PR merges to `main`. Until you do, `status` reports `behind`, which is expected.

If the change adds a plugin to your host profile, install it before deploying. #17 added `skill-creator@claude-plugins-official`, so before the first deploy that includes it, run `claude plugin install skill-creator@claude-plugins-official` (on the personal machine the owner did this on 2026-10-20). Without it, `status` warns `skill-creator@claude-plugins-official: in the lock but not installed`.

1. Update your clone. If `git pull` refuses, see [Updating your checkout](#updating-your-checkout).

   ```sh
   git pull --ff-only
   ```

2. See what would change. `plan` exits 1 even when nothing differs (#48), so read its output rather than its exit code.

   ```sh
   python3 -I tools/cfg/cli.py plan --host hosts/personal.json
   ```

3. Run the deploy without `--yes`. On a terminal it shows the change list and asks `continue? [y/N]` (this prompt comes from #8's tests; the trials for this runbook ran without a terminal). Without a terminal it prints the list, ends with `nothing was changed; rerun with --yes to go ahead`, and exits 1.

   ```sh
   python3 -I tools/cfg/cli.py deploy --host hosts/personal.json
   ```

   It stops instead (`deploy <sha>: stopped`, exit 1) when something live differs from what it would install. Each conflict comes with a diff and the line `resolve with: cfg capture <path>, or cfg deploy ... --discard-live <path>`. See [Drift](#drift).

4. Answer `y`, or rerun with `--yes`. Success ends like this:

   ```text
   deploy 80285b706aac: deployed
     switch current from 09f1facab0011e4f4446dec6278a2b8043b30ae7 to 80285b706aac22162946a8cbe7eb201e2971d03d
   ```

   When nothing has changed since the last deploy, it says `deploy <sha>: already-deployed` and exits 0. A deploy that moves a file aside or changes a setting also prints `backup:` and `undo:` lines.

5. Check the result:

   ```sh
   python3 -I tools/cfg/cli.py status
   ```

   Expect `status: clean (release <sha>)` and exit 0. Anything else is described in [Drift](#drift).

### Moving off the claude-code-skills plugins (#62)

Do this once on each machine, with the first deploy that includes #62's PR 1. That deploy stops enabling `engineering-skills@claude-code-skills` and `engineering-advanced-skills@claude-code-skills` and links the 17 skills copied into `config/skills/` in their place. The plugins stay installed until you uninstall them, and until then a session may list both copies.

1. Deploy as in [An ordinary deploy](#an-ordinary-deploy-after-a-change-merges): run it without `--yes`, read the change list, then run it with `--yes`. The list adds 17 `skills/<name>` links and changes `settings.json`, whose `enabledPlugins` loses the two plugins.

   ```sh
   python3 -I tools/cfg/cli.py deploy --host hosts/personal.json
   python3 -I tools/cfg/cli.py deploy --host hosts/personal.json --yes
   ```

   On the work machine, use `hosts/work.json`.

2. Before uninstalling anything, check the links and the overlays. Each skill must have a `SKILL.md` behind its link:

   ```sh
   for s in adversarial-reviewer agent-workflow-designer api-design-reviewer api-test-suite-builder ci-cd-pipeline-builder database-schema-designer dependency-auditor migration-architect runbook-generator senior-architect senior-prompt-engineer senior-qa ship-gate skill-security-auditor skill-tester spec-driven-workflow tdd-guide; do test -f ~/.claude/skills/$s/SKILL.md || echo "missing $s"; done
   ```

   Expect no output. Then run the live overlay hook once for each overlay:

   ```sh
   for s in adversarial-reviewer api-test-suite-builder ci-cd-pipeline-builder runbook-generator senior-architect senior-qa ship-gate spec-driven-workflow tdd-guide; do printf '{"hook_event_name":"PostToolUse","tool_name":"Skill","tool_input":{"skill":"%s"}}' "$s" | env -u CLAUDE_HOME python3 -I ~/.claude/hooks/skill_overlay.py; echo; done
   ```

   Expect 9 JSON lines. Each `additionalContext` starts `Skill overlay for <name>:` and names paths under `~/.claude/skills/<name>/`. If a line says `could not be loaded`, or a line inside it starts `missing:`, stop: a link points at the wrong place. Run `python3 -I tools/cfg/cli.py rollback` and report it on #62.

3. Uninstall the two plugins:

   ```sh
   claude plugin uninstall engineering-skills@claude-code-skills
   claude plugin uninstall engineering-advanced-skills@claude-code-skills
   ```

   If `claude plugin uninstall` isn't available, open an interactive `claude` session, type `/plugin`, and uninstall both from the menu.

4. Check the result:

   ```sh
   claude plugin list
   python3 -I tools/cfg/cli.py status
   python3 -I tools/cfg/lock.py --check
   ```

   `claude plugin list` must not show either plugin. Until #62's PR 2 updates `plugins.lock.json`, `status` prints `warning plugin-lock`, and `lock.py --check` exits 1 and names the two plugins as in the lock but not installed. After PR 2 merges and you deploy it, both come back clean. Neither reports an `upstream` line for the vendored skills while the marketplace clone is at the recorded commit.

5. To undo after step 3, reinstall the plugins first, then roll back. A rollback alone restores the old overlays, which need the plugins:

   ```sh
   claude plugin install engineering-skills@claude-code-skills
   claude plugin install engineering-advanced-skills@claude-code-skills
   python3 -I tools/cfg/cli.py rollback
   ```

   `rollback` leaves `settings.json` alone, so also re-enable the two plugins: run `/plugin` in an interactive session, or add them back to `enabledPlugins` in `~/.claude/settings.json`.

### The first deploy on a machine that already has a config

The first deploy compares every managed path and owned setting with the repo, and stops on each difference until you decide about it. Nothing on disk changes until no conflicts remain. This is how the owner's personal machine was deployed on 2026-10-19 (PR #49).

1. See the differences:

   ```sh
   python3 -I tools/cfg/cli.py plan --host hosts/personal.json
   ```

   Each managed path shows `missing`, `match` or `differs`, and the settings show `same`, `missing-live` or a difference. On the personal machine it looked like this:

   ```text
     CLAUDE.md                                differs
     skill-overlays                           differs
     hooks/skill_overlay.py                   match
     hooks/merge_confirm.py                   match
     skills/github-issue-lifecycle            differs
     ...
   settings: all seven owned items same
   a deploy would stop until each difference is captured or discarded (ADR-0004)
   ```

2. Decide each `differs` path. To keep your live version, capture it into a branch (see [Capturing a live change](#capturing-a-live-change)), merge that branch, pull, and start again. Before the first deploy there's no deploy state yet, so `capture` needs the host profile too: add `--host hosts/personal.json`, or it refuses with `nothing is deployed yet, so name the host profile with --host`. To let the repo's version win, name the path with `--discard-live`; your live copy goes into the backup either way. A settings difference is decided the same way, with `settings.json` as the path.

3. Run the deploy with your choices and without `--yes`, and read the change list:

   ```sh
   python3 -I tools/cfg/cli.py deploy --host hosts/personal.json --discard-live <path>
   ```

   Repeat `--discard-live <path>` once for each path you chose to discard. The list shows `back up and link <path> (differs)` or `(match)` for each path and ends with `switch current from nothing to <sha>`.

4. Rerun the same command with `--yes`. The output ends with the backup's location:

   ```text
   backup: /home/<owner>/.claude/backups/claude-config/20261019T160515Z
   undo: /home/<owner>/.claude/backups/claude-config/20261019T160515Z/restore.sh
   ```

   Write that path down. That backup is the only way back to the files you had before.

5. Check that `status` says `clean`:

   ```sh
   python3 -I tools/cfg/cli.py status
   ```

### The first deploy on an empty machine

The flow is the same, but nothing stops: every path is `missing` and the settings are `missing-live`. Install the plugins first (see [Setting up a second machine](#setting-up-a-second-machine)). Before switching, the deploy runs each hook on sample input, and without the plugins that test fails with a traceback from `skill_overlay.py` and changes nothing (exit 2).

## Drift

Drift is any difference between the live config and the deployed release. `status` reports it and `diff` shows it.

```sh
python3 -I tools/cfg/cli.py status
python3 -I tools/cfg/cli.py diff <path>
```

`diff` without a path shows every difference, and `diff settings.json` shows the owned settings keys.

| `status` line | What happened | What to do |
|---|---|---|
| `status: clean (release <sha>)` | Nothing | Nothing |
| `behind: origin/main is at <sha>, ahead of the deploy` | A change merged since the last deploy | [An ordinary deploy](#an-ordinary-deploy-after-a-change-merges) |
| `drift <file>: changed in the release` | Something edited a file through a link, so the edit is in the release worktree | Capture it, or deploy with `--discard-live <path>` |
| `unlinked <path>: ... is a plain file or directory` | A tool replaced a link with a plain file | Capture it, or deploy with `--discard-live <path>` |
| `broken <path>: ...` | A link is missing or points somewhere else | `deploy` relinks it |
| a settings difference | An owned key in `settings.json` changed | Capture `settings.json`, or deploy with `--discard-live settings.json` |

`status` may also print `warning` lines, for example `warning plugin-lock` when the installed plugins differ from `plugins.lock.json`. The skills claude.ai syncs into `skills/synced/` aren't compared: each account syncs its own. Warnings don't stop a deploy.

Claude's Edit and Write tools refuse to write through a linked file such as `~/.claude/CLAUDE.md` ("Write target is a symbolic link"). Claude then edits the file in the release worktree instead, which shows up here as drift. That's expected: capture the edit if you want to keep it.

### Capturing a live change

`capture` writes live changes into a worktree on a feature branch. It never commits and refuses `main`. Make the worktree with git, because this runbook may be needed when Claude Code isn't working:

```sh
git worktree add -b <branch> <worktree> origin/main
python3 -I tools/cfg/cli.py capture --into <worktree> <path>
```

Expected output:

```text
captured into /tmp/cfg-capture-E34rO9 (nothing committed):
  wrote   config/CLAUDE.global.md
eval suites this change needs: rubric, scenario
next: commit on this branch through the lifecycle flow. Once it merges, cfg deploy treats these paths as resolved; to deploy before then, pass --discard-live <path>.
```

Commit on that branch and open a PR through the usual issue flow, including the eval suites it names. Once it merges, pull and deploy.

`capture` copies a symlink's text as it is. Before committing, check that no captured link points into your home directory, because the repo is public.

### Discarding a live change

```sh
python3 -I tools/cfg/cli.py deploy --host hosts/personal.json --discard-live <path> --yes
```

The repo's version wins. A discarded plain file goes into the backup. A discarded edit in the release stays where it was: the deploy builds a new release, named `<sha>-2` when one for that commit exists, and leaves the edited one on disk. See [Releases and pruning](#releases-and-pruning) to remove it later.

## Rolling back

Pick the row that matches what's wrong. The reasons are in [ADR-0006](adr/0006-repo-workflow.md).

| Trigger | Do this |
|---|---|
| A merged change is wrong, but Claude Code still works | `git revert <sha>` on a `fix/` branch, open a PR, merge it, then [deploy](#an-ordinary-deploy-after-a-change-merges) |
| A deploy broke Claude Code itself, for example a hook that fails on every call | `cfg rollback` (below), then the revert PR |
| `cfg` itself won't run | Point `current` back by hand (below), then the revert PR |
| You need a known-good commit now, without waiting for a PR | `python3 -I tools/cfg/cli.py deploy --host hosts/personal.json --ref <good-sha> --yes` |
| You want plain files back, with no links | [Undoing with restore.sh](#undoing-with-restoresh) |

### cfg rollback

```sh
python3 -I tools/cfg/cli.py rollback
```

It switches `current` back to the previous release and fixes `cfg`'s own links. Expected output:

```text
rolled back from 80285b706aac22162946a8cbe7eb201e2971d03d-2 to 09f1facab0011e4f4446dec6278a2b8043b30ae7
to return: cfg deploy
```

It never touches `settings.json`. When the owned settings differ between the two releases, it prints the `deploy --ref` command that rolls them back too. Afterwards `status` reports `behind` until `main` and the deploy agree again.

### When cfg won't run

Point `current` at an earlier release by hand. The deploy history lists the releases oldest first; the last line is the current one, and the line before it is the one to go back to:

```sh
python3 -I -c 'import json, os; print(*json.load(open(os.path.expanduser("~/.local/share/claude-config/state.json")))["history"], sep="\n")'
ln -sfn releases/<sha> ~/.local/share/claude-config/current
```

`status` then reports `broken current: current doesn't point at release <sha>` until the next `deploy`, because the deploy state still names the newer release. Run a deploy once `cfg` works again.

## Undoing with restore.sh

Each backup holds its own `restore.sh`, which needs nothing but Python. It removes the links that deploy made, puts the files it moved back after checking their hashes, and reverts only the owned settings it changed. Running it twice changes nothing more. It leaves alone anything that changed after the deploy, moves it into a snapshot inside the backup, and reports it:

```text
left alone  CLAUDE.md: it was no longer cfg's link; moved into the snapshot
snapshot: .../backups/claude-config/20261019T173302Z/restore-20261019T173353Z
```

It exits 0 when everything went back. Exit 1 means it left something alone, so read the `left alone` lines and look in the snapshot. Exit 2 means it refused, for example because it couldn't read the manifest.

Not every deploy writes a backup. A deploy writes one when it creates or removes a link or writes `settings.json`, which the first deploy always does. An ordinary deploy that only switches `current` to a new release doesn't. The personal machine had one backup after its first two deploys.

To go back to plain files, run every backup's `restore.sh`, newest first, and the first deploy's last. If a first deploy failed and was run again, the earliest backup holds your original files, so it still runs last.

```sh
ls -1r ~/.claude/backups/claude-config/
~/.claude/backups/claude-config/<backup>/restore.sh
```

Running the first deploy's `restore.sh` also moves the deploy state aside, so `status` then says `not-deployed`. It leaves `settings.json` in place, with `cfg`'s keys taken out.

`cfg restore` runs the newest backup's `restore.py` for you, or the one you name. Once the deploy state is gone, it needs the backup's path:

```sh
python3 -I tools/cfg/cli.py restore <backup>
```

## If a deploy exits 3

Exit 3 means a deploy or rollback failed after it had started changing the live config. The message names the `restore.sh` that undoes this deploy. Run that `restore.sh` first, then `status`, then `cfg rollback` if `current` points at the failed release. Open an issue with the full output before deploying again.

## Releases and pruning

Each deploy makes or reuses a release worktree under `~/.local/share/claude-config/releases/`, named by commit (`<sha>`, or `<sha>-2` and so on for later releases of the same commit). After each deploy, `cfg` removes old releases but keeps the five newest, the current one, and any release with edits in it.

To remove a release with edits by hand, first capture or discard its edit, and make sure it isn't `current`. Then:

```sh
git -C ~/.local/share/claude-config/releases/<sha> status --short
git worktree remove --force ~/.local/share/claude-config/releases/<sha>
```

Run the second command from your clone. Without `--force`, git refuses a worktree with changes, which is the safety you're overriding. The next deploy may build a fresh release under the freed name and switch to it; that's expected.

## Checking that sync and /plugin leave links alone

Do this after a Claude Code update, or when a link looks wrong. claude.ai skill sync and the `/plugin` menu can only be checked in an interactive session.

1. Save an inventory of the managed paths:

   ```sh
   python3 -I eval/symlink_check.py --inventory ~/.claude > /tmp/inventory-before.json
   ```

2. In another terminal, run `cd /tmp && claude`, wait about a minute for sync, type `/plugin` and close the menu with Esc without changing anything, then type `/exit`.

3. Take a second inventory and compare. `diff` prints nothing when no link changed:

   ```sh
   python3 -I eval/symlink_check.py --inventory ~/.claude > /tmp/inventory-after.json
   diff /tmp/inventory-before.json /tmp/inventory-after.json
   ```

`--inventory` only reads and exits 0. A full run of `eval/symlink_check.py` without `--inventory` exits 2 on every run today (#44), so read its results table instead of the exit code.

## Setting up a second machine

For the work machine, follow [Setting up the work machine](#setting-up-the-work-machine) instead. For any other machine:

1. Clone the repo and [set up the clone](#set-up-a-clone).
2. Pick a host profile from `hosts/`, or add one in a PR. A profile lists which paths, skills and plugins the machine gets. The config folder's path must not contain spaces or shell characters; `cfg` refuses such a `claude_home`.
3. Install the plugins the profile lists, before the first deploy. Audit third-party ones with the `skill-security-auditor` skill first, as the global CLAUDE.md requires. For the personal profile:

   ```sh
   claude plugin marketplace add alirezarezvani/claude-skills
   claude plugin install claude-md-management@claude-plugins-official
   claude plugin install skill-creator@claude-plugins-official
   ```

   `claude-plugins-official` is usually configured already; `claude plugin marketplace list` shows it. If it's missing, add `anthropics/claude-plugins-official` the same way. The `claude-code-skills` marketplace (`alirezarezvani/claude-skills`) is added for its clone only, which `lock.py --check` compares the vendored skills with; install no plugin from it (#62).

4. `claude plugin install` takes the marketplace's current version, not the one in `plugins.lock.json`. Compare them, and review any difference before deploying:

   ```sh
   python3 -I tools/cfg/lock.py --check
   ```

   Expect `{"ok": true, ...}`.

5. Run a trial deploy first. Follow [Practice on a trial directory](#practice-on-a-trial-directory) with your profile.
6. Run the real first deploy: [on a machine that already has a config](#the-first-deploy-on-a-machine-that-already-has-a-config), or [on an empty machine](#the-first-deploy-on-an-empty-machine).

These steps haven't been run on a real second machine yet.

## Setting up the work machine

The work machine gets the same skills, hooks and plugins as the personal machine, from `hosts/work.json`. Only its default lifecycle differs: `jira-ticket-lifecycle` instead of `github-issue-lifecycle` ([ADR-0011](adr/0011-tracker-agnostic-lifecycle.md)). The plan for this setup, and the order of its checks, is the spec on [#17](https://github.com/Towerism/claude-config/issues/17).

This repo is public. Nothing from the work machine's Jira project goes into it, an issue or a PR: no project key, site, ticket key, person's name, workflow state, work email or work user name. When you paste output from this machine anywhere on GitHub, leave out what `jira me` prints, and write home paths as `~`.

Steps 1 to 5 change files only you control. Step 6 changes the live `~/.claude`, step 9 writes to Jira, and step 10 deletes a directory; each waits for your go-ahead.

1. Clone the repo and [set up the clone](#set-up-a-clone). In the excluded-terms list, `~/.config/claude-config/deny-terms`, put one per line: the team's Jira project key, the Jira site's host name, the team's and the company's names, the work email domain, and this machine's user name and host name. The repo's git hooks then refuse any commit that contains one.

2. Give non-interactive shells the Jira token. Add a line `export JIRA_API_TOKEN=<token>` to `~/.zshenv` yourself; Claude never reads or writes it. The `jira` CLI ([ankitpokhrel/jira-cli](https://github.com/ankitpokhrel/jira-cli)) must be installed and logged in with `jira init`. To check both:

   ```sh
   zsh -c 'test -n "$JIRA_API_TOKEN" && echo token set'
   jira me > /dev/null && echo jira logged in
   ```

   Expect `token set` and `jira logged in`.

3. Write the Jira settings in `~/.config/claude-config/local.json`: a `jira` object with the keys in the skill's [settings reference](../config/skills/jira-ticket-lifecycle/references/settings.md). Leave out `defaultLifecycle`, even though that page's example has it: `hosts/work.json` already sets it, and step 7 checks that the default comes from there. To check the settings without printing them:

   ```sh
   python3 -I config/skills/jira-ticket-lifecycle/scripts/ticket_lifecycle.py settings > /dev/null; echo "exit $?"
   ```

   Expect `exit 0`. `exit 6` means a required key is missing; run it without `> /dev/null` to see which.

4. Install the plugins and compare them with the lock, as in steps 3 and 4 of [Setting up a second machine](#setting-up-a-second-machine). If `lock.py --check` reports a newer version, review it; a lock update is its own `chore(deps)` PR, and the deploy waits for it.

5. Run a trial deploy with `--host hosts/work.json`, following [Practice on a trial directory](#practice-on-a-trial-directory).

6. Run the first deploy, without `--yes` first:

   ```sh
   python3 -I tools/cfg/cli.py plan --host hosts/work.json
   python3 -I tools/cfg/cli.py deploy --host hosts/work.json
   ```

   This machine already has a config, so follow [The first deploy on a machine that already has a config](#the-first-deploy-on-a-machine-that-already-has-a-config) and decide each `differs` path by capture or `--discard-live`. That section's examples use `hosts/personal.json`; on this machine, use `hosts/work.json` in every `plan`, `deploy` and `capture` command, or the deploy records the personal profile and the default lifecycle becomes GitHub. Read the change list, then rerun the same command with `--yes`. The old Jira skill in `~/.claude/skills/` isn't in the profile, so the deploy leaves it alone. Then check:

   ```sh
   python3 -I tools/cfg/cli.py status
   ```

   Expect `clean` and exit 0.

7. Check the default lifecycle line:

   ```sh
   echo '{"hook_event_name": "SessionStart"}' | python3 -I ~/.claude/hooks/lifecycle_default.py
   ```

   Expect:

   ```json
   {"hookSpecificOutput": {"hookEventName": "SessionStart", "additionalContext": "Default lifecycle skill on this machine: `jira-ticket-lifecycle` (set in `hosts/work.json`). Use it to start and close out code changes unless the repo's CLAUDE.md names its tracker; the repo's choice wins."}}
   ```

   If it names `local.json` instead, remove `defaultLifecycle` from that file.

8. Before anything writes to Jira, have Claude check the `jira` flags the skill uses. Claude compares every command in the skill's [jira-commands reference](../config/skills/jira-ticket-lifecycle/references/jira-commands.md) with the installed CLI's `--help` for that subcommand, and notes `jira version`. If a flag is missing, the fix is its own PR under #17, test first; once it merges, deploy again (step 6) and repeat this step.

9. Check that the spec markers survive a real round trip, on the first real ticket you start with the new skill. Name the skill in your prompt, for example "use jira-ticket-lifecycle to start <key>", because the old skill is still installed and could answer instead. Claude asks before each Jira write: claiming the ticket, then storing the spec in its description. It then reads the description back. The check passes when `spec:start` and `spec:end` come back as paragraphs of their own, with the spec between them.

10. Remove the old Jira skill once steps 6 to 9 pass. First compare it with the copy kept in `~/.old-claude/work-skills/`, which stays:

    ```sh
    ls ~/.old-claude/work-skills/
    diff -r ~/.claude/skills/<old-skill> ~/.old-claude/work-skills/<old-skill> && echo same
    ```

    Expect `same`. If `diff` shows differences, decide first whether to refresh the kept copy. Then remove `~/.claude/skills/<old-skill>`, and check again: `status` says `clean`, and a new `claude` session states `jira-ticket-lifecycle` as its default.

11. Run the excluded-terms scan over the whole repo. `157c698` is the repo's first commit, so this checks every later commit, its message, and every tracked file:

    ```sh
    python3 -I tools/git_hooks.py check-range 157c698
    ```

    The scan passes when it prints no `deny-term` line, and its last line, `check-range: terms checked: N`, shows N above 0. If it finds a term, stop and tell the owner which paths and commits it names; never repeat the term on GitHub. Other findings on old commits, such as `identity`, are reported separately.

12. Record the results in a last PR that closes #17: this runbook's "Last verified" line and the note below "Setting up a second machine", the CLI version and result in the jira-commands reference, and the items #16 left for this machine in that skill's `REVIEW-NOTE.md`. Commit it on this machine, so the git hooks check it against this machine's excluded-terms list. Pushing needs `gh` and git logged in as the repo owner's GitHub account, as [Before you start](#before-you-start) says; if this machine's `gh` uses a work account, stop and ask before switching it.

## Changing this machine's default lifecycle

Each session and subagent starts with one line saying which lifecycle skill to use when the repo's own CLAUDE.md names no tracker. The `lifecycle_default.py` hook prints it, from a deploy that includes #15 onward. To see the line this machine gets:

```sh
echo '{"hook_event_name": "SessionStart"}' | python3 -I ~/.claude/hooks/lifecycle_default.py
```

On the personal machine, expect:

```json
{"hookSpecificOutput": {"hookEventName": "SessionStart", "additionalContext": "Default lifecycle skill on this machine: `github-issue-lifecycle` (set in `hosts/personal.json`). Use it to start and close out code changes unless the repo's CLAUDE.md names its tracker; the repo's choice wins."}}
```

The default comes from `defaultLifecycle` in the host profile. To change it for every machine on that profile, change the profile in a PR. To change it on this machine only, write a local file, which `cfg` never writes or syncs:

```sh
mkdir -p ~/.config/claude-config
printf '%s\n' '{"defaultLifecycle": "jira-ticket-lifecycle"}' > ~/.config/claude-config/local.json
```

If `XDG_CONFIG_HOME` is set to an absolute path, the file goes in `$XDG_CONFIG_HOME/claude-config/` instead. Run the command above again: the line now says `(set in ~/.config/claude-config/local.json, which overrides the host profile)`. If the file can't be used, the line keeps the profile's default and ends with `~/.config/claude-config/local.json was ignored:` and the reason, such as `it isn't valid JSON`. To go back to the profile's default, delete the file or its `defaultLifecycle` key.

The hook doesn't check that the named skill is installed. A new session picks up a change; a running one keeps the line it started with.

## Updating your checkout

`git pull` refuses when your clone has untracked files that the repo now tracks. On 2026-10-19 the owner's checkout had an untracked `docs/` folder, left over from before the docs were committed. Move such files aside, never delete them, then pull:

```sh
git status --short
mv docs /tmp/old-untracked-docs
git pull --ff-only
```

Compare the moved files with the repo's versions before throwing them away.

## What was removed after the first deploy

After the personal machine's first deploy passed on 2026-10-19, the owner approved deleting two leftovers of the v1 config: the retired skill in `~/.old-claude/skills/issue-spec-driven-workflow/` and the v2 install backup in `~/.claude/backups/claude-md-v2-20261015T230435/` (recorded in the plan's [open question on deleting them, Q-2](architecture/plan.md#open-questions)). `~/.old-claude/work-skills/` is kept.

Don't delete the first deploy's own backup, `~/.claude/backups/claude-config/20261019T160515Z/`. It's the only way back to the files from before the first deploy.

## Known quirks

- `plan` exits 1 even when no path differs (#48). Read its output.
- A full `eval/symlink_check.py` run exits 2 every time today (#44). Read its table.
- Claude can't edit a linked file such as `~/.claude/CLAUDE.md` in place; its edits land in the release and show as drift ([ADR-0001](adr/0001-deployment-model.md)).

## Practice on a trial directory

This walks through the first deploy, drift, rollback, undo and cleanup on a temporary directory. Every `cfg` command here carries `--target`, so your live `~/.claude` is never touched. Run it from your clone's root, in one terminal, from top to bottom. It takes a few minutes.

### Set up the trial

```sh
TRIAL=$(mktemp -d)
mkdir "$TRIAL/home"
ln -s ~/.claude/plugins "$TRIAL/home/plugins"
echo "an old CLAUDE.md" > "$TRIAL/home/CLAUDE.md"
```

The `plugins` link lets the deploy's hook test find your installed plugins. Nothing in this walkthrough writes through it, and the cleanup removes only the link.

### First deploy with a conflict

```sh
python3 -I tools/cfg/cli.py plan --host hosts/personal.json --target "$TRIAL/home"
python3 -I tools/cfg/cli.py deploy --host hosts/personal.json --target "$TRIAL/home"
```

`plan` shows `CLAUDE.md differs` and every other path `missing`. The deploy stops on `CLAUDE.md` with a diff and exits 1. Discard the old file, look at the change list, then go ahead:

```sh
python3 -I tools/cfg/cli.py deploy --host hosts/personal.json --target "$TRIAL/home" --discard-live CLAUDE.md
python3 -I tools/cfg/cli.py deploy --host hosts/personal.json --target "$TRIAL/home" --discard-live CLAUDE.md --yes
python3 -I tools/cfg/cli.py status --target "$TRIAL/home"
```

Expect `deployed`, a `backup:` line, and then `status: clean` with one `warning plugin-lock` line.

### Drift

Edit the release through the link, the way a tool would:

```sh
echo "a live edit" >> "$TRIAL/home/CLAUDE.md"
python3 -I tools/cfg/cli.py status --target "$TRIAL/home"
python3 -I tools/cfg/cli.py diff --target "$TRIAL/home" CLAUDE.md
python3 -I tools/cfg/cli.py deploy --host hosts/personal.json --target "$TRIAL/home" --discard-live CLAUDE.md --yes
python3 -I tools/cfg/cli.py status --target "$TRIAL/home"
```

`status` shows `drift config/CLAUDE.global.md: changed in the release`, `diff` shows the added line, and the deploy switches to a new release ending in `-2`. `status` is clean again.

### Roll back

Deploy an older commit, come back to `main`, then roll back:

```sh
python3 -I tools/cfg/cli.py deploy --host hosts/personal.json --target "$TRIAL/home" --ref 09f1fac --yes
python3 -I tools/cfg/cli.py deploy --host hosts/personal.json --target "$TRIAL/home" --yes
python3 -I tools/cfg/cli.py rollback --target "$TRIAL/home"
python3 -I tools/cfg/cli.py status --target "$TRIAL/home"
```

The rollback prints `rolled back from <release> to 09f1fac…`, and `status` reports `behind`. Deploy once more to get back to `clean`:

```sh
python3 -I tools/cfg/cli.py deploy --host hosts/personal.json --target "$TRIAL/home" --yes
```

### Undo with restore.sh

Run every backup's `restore.sh`, newest first:

```sh
for b in $(ls -1r "$TRIAL/home/backups/claude-config"); do "$TRIAL/home/backups/claude-config/$b/restore.sh"; done
python3 -I tools/cfg/cli.py status --target "$TRIAL/home"
cat "$TRIAL/home/CLAUDE.md"
```

The restore prints `restored` lines and puts back `an old CLAUDE.md`. `status` says `not-deployed`.

### Clean up

This removes the trial directory, its deploy state and the release worktrees git registered for it. The `case` line refuses anything that isn't a directory `mktemp` made.

```sh
case "$TRIAL" in "${TMPDIR:-/tmp}"/tmp.*) rm -rf -- "$TRIAL" && git worktree prune && git worktree list ;; *) echo "not removing $TRIAL" ;; esac
```

`git worktree list` should show only your clone, its other worktrees and your live releases under `~/.local/share/claude-config/releases/`.

## When this runbook goes stale

Check this runbook again, and update its "Last verified" line, when any of these change: `tools/cfg/cli.py`, `tools/cfg/deploy.py`, `tools/cfg/restore.py`, `hosts/*.json`, `config/settings.fragment.json`, [ADR-0001](adr/0001-deployment-model.md), [ADR-0004](adr/0004-drift-and-adoption.md) or [ADR-0006](adr/0006-repo-workflow.md).

There's no escalation path: claude-config has one owner.
