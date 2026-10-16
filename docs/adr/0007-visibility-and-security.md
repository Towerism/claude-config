# ADR-0007: Visibility and security (a public repo, secret scanning in two layers, and a high review bar for hooks)

**Status:** Accepted by the owner on 2026-10-16 (proposed 2026-10-15). Visibility decided by the owner on 2026-10-16.

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

The owner's email address stays obfuscated, also decided on 2026-10-16. Git records an author and a committer email in every commit, and a fresh clone would use the owner's personal address. Plan issue 1 therefore sets the repo-local `user.email` to the owner's GitHub noreply address (`<id>+Towerism@users.noreply.github.com`) before the first commit. A pre-commit check refuses any commit whose identity isn't a noreply address, and the CI `hygiene` job fails on any such commit in a PR. GitHub's account setting "Block command line pushes that expose my email" would add a server-side guard; whether to turn it on is open question Q-1 in the [plan](../architecture/plan.md#open-questions).

Public repos on the Free plan support branch protection, so `main` gets a ruleset. It blocks direct pushes, force-pushes and deletion, requires a PR, and requires the CI jobs to pass. A job skipped by the path rules reports "skipped", which satisfies a required check. The owner approves the ruleset as part of plan issue 1.

Actions minutes on standard runners are free and unmetered for public repos, so the only CI budget left in ADR-0005 is run time.

Because a public repo can receive pull requests from forks, the workflow trigger stays `pull_request` and never `pull_request_target`. The workflow uses no secrets, so a fork's PR has nothing to steal.

### Scanning for secrets, email addresses and tokens: both layers

The pre-commit hook lives in `.githooks/pre-commit`. `cfg bootstrap` enables it with `git config core.hooksPath .githooks`. It runs four checks:

- `gitleaks protect --staged` with the repo's `.gitleaks.toml`, which adds an email-address rule and the token patterns from the v2 spec's lint (`ghp_`, `gho_`, `github_pat_`, `sk-`, `xox[bp]-`) to gitleaks' default rules;
- a filename deny-list that mirrors the owner's list of never-versioned paths;
- excluded terms. The list lives in a local, untracked file, `~/.config/claude-config/deny-terms`, one term per line. It is never committed, so the terms never appear in the repo. The hook refuses any staged file or commit message that contains one. CI can't run this check, because the list exists only on the owner's machines. The two import issues (2 and 3) therefore also check their payload against it and record that they did;
- a stdlib regex fallback for the email and token patterns when gitleaks is missing, which warns loudly. gitleaks was not installed on the owner's machine when this ADR was written.

A pre-push hook refuses pushes to `main`. In CI, gitleaks runs with the same config over the PR's commit range, together with the filename deny-list. The only allow-listed address is `noreply@anthropic.com`, in case it ever appears in a file; commit trailers aren't scanned as file content.

A few structural guards back these checks up. `cfg capture` copies only managed paths. Raw eval output is gitignored. The directories the evals create are placed under `/tmp`, with an assertion that they are outside the repo. `.gitignore` lists the never-versioned paths as a last line of defence.

### Hook review bar

A change to `config/hooks/*`, or to the `hooks` key of the settings fragment, must meet every requirement in this table:

| # | Requirement | Checked by |
|---|---|---|
| H1 | Standard library only; no network, no subprocess, no dynamic code (`eval`, `exec`, or `importlib` on user data) | A CI rule that inspects the hook's syntax tree |
| H2 | 100% branch coverage of the hook module | CI (`pytest-cov --cov-branch`, fail-under 100 for `config/hooks`) |
| H3 | Always exits 0 and never blocks, apart from the merge hook's deliberate `ask`. Never echoes tool input; tracebacks go to stderr only. | Unit tests in the style of the v2 spec's hook tests (AC-5 and AC-9) |
| H4 | Skill names are validated before any path is built, and only `skill-overlays/` and skill directories are read | Unit tests, including path-traversal cases |
| H5 | A real-harness eval record (the `hook-overlay` or `hook-merge` suite), with a 95th-percentile run time under 500 ms | The `eval-record` CI job |
| H6 | Invoked as `python3 -I <absolute path>` | The fragment schema plus a checker rule |
| H7 | The adversarial review of the diff covers hostile input in hook payloads, and the PR description says so | Reviewer |
| H8 | The deploy smoke test passes against the new release before `current` is swapped, and again through the live links afterwards. The PR's rollback section names `cfg rollback`. | `cfg deploy`, and the reviewer |

### Plugins

The repo installs no plugins itself. It enables them through settings keys it owns, and Claude Code installs them. Any change to the plugin set arrives as a PR that updates the plugin lock, `plugins.lock.json`, with findings from `dependency-auditor` and `skill-security-auditor` in the description. That keeps what gets enabled reviewable in a diff, even though the code itself comes from the marketplace. Adding a new plugin or marketplace is a `feat` change with its own issue; the v2 spec put new plugins out of its scope (OS-2), and that still holds.

## Consequences

The ruleset on `main` enforces PR-only merges with green CI. The hooks, `ship-gate` and the owner's merge confirmation still apply on top of it.

Each fresh clone needs one `cfg bootstrap` per machine to enable the git hooks. CI catches anything a missing hook would have let through, except the excluded-terms check.

## Revisit when

- A skill, eval record or history file turns out to be something the owner doesn't want public. Move it to a private companion repo, or make this repo private, and rewrite history only if it was already pushed.
- A hook needs a third-party dependency. That needs a new ADR, because it breaks requirement H1 and the `python3 -I` invocation.
