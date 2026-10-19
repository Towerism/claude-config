# ADR-0007: Visibility and security (a public repo, secret scanning in two layers, and a high review bar for hooks)

**Status:** Accepted by the owner on 2026-10-16 (proposed 2026-10-15). Visibility decided by the owner on 2026-10-16. Amended 2026-10-16 by #1 (adversarial approval): the email allow-list, the commit-msg and pre-merge-commit hooks, and `check-range`. Amended 2026-10-16 by #2 (approved by the owner): the email allow-list also accepts the RFC 2606 reserved top-level domains. Amended 2026-10-19 by #20 (spec approved by the owner): H4 names the plugin index the overlay hook reads, and the H1 and H6 checks are repo tests. Amended 2026-10-19 by #10 (spec approved by the owner): GitHub's merge committer is accepted beside a noreply author, and the ruleset's required checks wait for the owner's go-ahead.

The repo `Towerism/claude-config` is public. Secrets, the owner's email address and a private list of excluded terms are kept out by checks before every commit and again in CI. Hook scripts, which run on every tool call, can change only when they meet the eight requirements listed under the hook review bar below.

Terms such as live config, payload, `cfg`, eval record and plugin lock are defined in the [Terms section of the architecture doc](../architecture/README.md#terms).

## Context

`Towerism/claude-config` already existed on GitHub, public and empty, when this ADR was written. Some of the skills in the live config are personal, and this repo doesn't contain them at all. Eval records quote short grader comments taken from model output.

The hooks run with the owner's privileges. `skill_overlay.py` runs on every Skill call and every slash command the owner types. `merge_confirm.py` runs on every Bash call. Both receive the full tool input, and a Bash command can contain a secret the owner typed. Plugins also run code with the owner's privileges.

## Options

### Visibility

1. **Public.** Actions minutes are free and unmetered, and branch protection is available. Everything committed is world-readable.
2. **Private.** 2,000 free minutes a month, which is plenty (see [ADR-0005](0005-verification-and-ci.md)), but no branch protection on the Free plan.
3. **A public repo for the generic parts plus a private one for the rest.** Two repos and two CI setups for one person.

### Secret scanning

1. **CI only.** Too late, because the secret has already been pushed.
2. **Pre-commit only.** Bypassable with `--no-verify`, and every machine has to install the hook.
3. **Both, with the same rules in each.**

## Decision

### Visibility: public

The owner decided on 2026-10-16 that the repo stays public ("public github repo is fine"). Several things follow from that.

Everything committed is world-readable: the managed skills, CLAUDE.md, the review history, eval records with their grader quotes, and everything under `docs/history/`. That makes the scanning described below more important.

The owner's email address stays obfuscated, also decided on 2026-10-16. Git records an author and a committer email in every commit, and a fresh clone would use the owner's personal address. Plan issue 1 therefore sets the repo-local `user.email` to the owner's GitHub noreply address (`<id>+Towerism@users.noreply.github.com`) before the first commit. A pre-commit check refuses any commit whose identity isn't a noreply address, and the CI `hygiene` job fails on any such commit in a PR. One exception, added by #10: a merge made on GitHub is committed by GitHub's own `noreply` address at `github.com`, which says nothing about the owner. `check-range` and pre-push accept that address as a committer, but only when the commit's author is a GitHub noreply address; as an author it is still refused. GitHub's account setting "Block command line pushes that expose my email" would add a server-side guard; whether to turn it on is open question Q-1 in the [plan](../architecture/plan.md#open-questions).

Public repos on the Free plan support branch protection, so `main` gets a ruleset. The CI jobs from #10 (`changes`, `hygiene`, `commits`, `lint`, `test`, `config`, `eval-record`) become required checks, with the strict up-to-date policy, only after #10 merges and the owner says yes to that change. It blocks direct pushes, force-pushes and deletion, requires a PR, and requires the CI jobs to pass. A job skipped by the path rules reports "skipped", which satisfies a required check. The owner approves the ruleset as part of plan issue 1.

Actions minutes on standard runners are free and unmetered for public repos, so the only CI budget left in ADR-0005 is run time.

Because a public repo can receive pull requests from forks, the workflow trigger stays `pull_request` and never `pull_request_target`. The workflow uses no secrets, so a fork's PR has nothing to steal.

### Scanning for secrets, email addresses and tokens: both layers

The pre-commit hook lives in `.githooks/pre-commit`. `git config core.hooksPath .githooks` enables it, once per clone. That step is manual, as the [runbook](../runbook.md#set-up-a-clone) lists it; a `cfg bootstrap` command was planned but not built, and #50 tracks it (decided 2026-10-19, in #11). It runs four checks:

- `gitleaks protect --staged` with the repo's `.gitleaks.toml`, which adds an email-address rule and the token patterns from the v2 spec's lint (`ghp_`, `gho_`, `github_pat_`, `sk-`, `xox[bp]-`) to gitleaks' default rules;
- a filename deny-list that mirrors the owner's list of never-versioned paths;
- excluded terms. The list lives in a local, untracked file, `~/.config/claude-config/deny-terms`, one term per line. It is never committed, so the terms never appear in the repo. The hook refuses any staged file or commit message that contains one. CI can't run this check, because the list exists only on the owner's machines. The two import issues (2 and 3) therefore also check their payload against it and record that they did;
- a stdlib regex fallback for the email and token patterns when gitleaks is missing, which warns loudly. gitleaks was not installed on the owner's machine when this ADR was written.

A pre-push hook refuses pushes to `main`. In CI, gitleaks runs with the same config over the PR's commit range, together with the filename deny-list. The allow-listed addresses are `noreply@anthropic.com` (the `Co-Authored-By` trailer), any `@users.noreply.github.com` address (these ADRs quote the owner's own), the RFC 2606 documentation domains `example.com`, `example.org` and `example.net`, and any domain whose last label is one of the RFC 2606 reserved top-level domains `.test`, `.example`, `.invalid` and `.localhost`. No mailbox can exist under those either. They were added on 2026-10-16 by #2, because the imported lifecycle skill uses `@example.invalid` identities in its test repos and has to be imported byte for byte. A lookalike such as `example.invalid.evil.io` is still flagged, because only the last label counts.

Three more pieces carry these checks where pre-commit can't. A commit message doesn't exist yet when pre-commit runs, so a `commit-msg` hook applies the token, email and excluded-terms checks to the message file as written, comment lines and trailers included, skipping only the diff that `git commit -v` adds below its scissors line. `git merge` runs `pre-merge-commit` instead of pre-commit, so that hook runs the pre-commit checks. `cherry-pick`, `rebase` and `--no-verify` run no hooks at all, so `python3 -I tools/git_hooks.py check-range <base>` repeats every check over the tree at HEAD and over each commit in `<base>..HEAD`: its identity, its stored message and every file it adds or changes. pre-push runs the same per-commit checks on every pushed commit, so nothing reaches the remote unchecked unless the push itself uses `--no-verify`, and it checks each pushed annotated tag's tagger and message. CI's `hygiene` job is the backstop for that last case.

A few structural guards back these checks up. `cfg capture` copies only managed paths. Raw eval output is gitignored. The directories the evals create are placed under `/tmp`, with an assertion that they are outside the repo. `.gitignore` lists the never-versioned paths as a last line of defence.

### Hook review bar

A change to `config/hooks/*`, or to the `hooks` key of the settings fragment, must meet every requirement in this table:

| # | Requirement | Checked by |
|---|---|---|
| H1 | Standard library only; no network, no subprocess, no dynamic code (`eval`, `exec`, or `importlib` on user data) | A repo test that inspects the hook's syntax tree (`tests/test_hook_review_bar.py`), run in CI |
| H2 | 100% branch coverage of the hook module | CI (`pytest-cov --cov-branch`, fail-under 100 for `config/hooks`) |
| H3 | Always exits 0 and never blocks, apart from the merge hook's deliberate `ask`. Never echoes tool input; tracebacks go to stderr only. | Unit tests in the style of the v2 spec's hook tests (AC-5 and AC-9) |
| H4 | Skill names are validated before any path is built. Only `skill-overlays/` and `plugins/installed_plugins.json` are read; the hook also checks whether files exist in a skill's directory: `SKILL.md`, in a user skill's directory or under each install path the plugin index lists, and the files an overlay names | Unit tests, including path-traversal cases and a record of every file read |
| H5 | A real-harness eval record (the `hook-overlay` or `hook-merge` suite), with a 95th-percentile run time under 500 ms | The `eval-record` CI job |
| H6 | Invoked as `python3 -I <absolute path>` | A repo test on the fragment's hook commands (`tests/test_hook_review_bar.py`), plus the fragment schema |
| H7 | The adversarial review of the diff covers hostile input in hook payloads, and the PR description says so | Reviewer |
| H8 | The deploy smoke test passes against the new release before `current` is swapped, and again through the live links afterwards. The PR's rollback section names `cfg rollback`. | `cfg deploy`, and the reviewer |

### Plugins

The repo installs no plugins itself. It enables them through settings keys it owns, and Claude Code installs them. Any change to the plugin set arrives as a PR that updates the plugin lock, `plugins.lock.json`, with findings from `dependency-auditor` and `skill-security-auditor` in the description. That keeps what gets enabled reviewable in a diff, even though the code itself comes from the marketplace. Adding a new plugin or marketplace is a `feat` change with its own issue; the v2 spec put new plugins out of its scope (OS-2), and that still holds.

## Consequences

The ruleset on `main` enforces PR-only merges with green CI. The hooks, `ship-gate` and the owner's merge confirmation still apply on top of it.

Each fresh clone needs the manual setup steps in the [runbook](../runbook.md#set-up-a-clone) to enable the git hooks, until a `cfg bootstrap` command exists (#50). CI catches anything a missing hook would have let through, except the excluded-terms check.

## Revisit when

- A skill, eval record or history file turns out to be something the owner doesn't want public. Move it to a private companion repo, or make this repo private, and rewrite history only if it was already pushed.
- A hook needs a third-party dependency. That needs a new ADR, because it breaks requirement H1 and the `python3 -I` invocation.
