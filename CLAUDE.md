# claude-config: working on this repo

Work here is tracked in GitHub (`Towerism/claude-config`) with `github-issue-lifecycle`. The repo is public. The architecture doc (`docs/architecture/README.md`) and the ADRs (`docs/adr/`) are the source of truth; a departure needs a new or updated ADR.

## Setup, once per clone

- `git config user.email 2057426+Towerism@users.noreply.github.com` and `git config user.name "Martin Fracker, Jr."`
- `git config core.hooksPath .githooks`
- `python3 -m venv .venv && .venv/bin/pip install --require-hashes -r requirements-dev.txt`
- The excluded-terms list lives in `~/.config/claude-config/deny-terms`, outside the repo. Never read it into output, quote it, or copy it into the repo.

## Commands

- Tests: `.venv/bin/python -I -m pytest` (add `--cov` for branch coverage; `tools/git_hooks.py` stays at 100%)
- The three Claude Code hooks stay at 100% branch coverage (ADR-0007, H2): `.venv/bin/python -I -m pytest tests/test_hook_skill_overlay.py tests/test_hook_merge_confirm.py tests/test_hook_lifecycle_default.py tests/test_hook_output_unchanged.py --cov=config/hooks --cov-branch --cov-fail-under=100`
- Skill tests, which the command above doesn't collect: `.venv/bin/python -I -m pytest config/skills/plain-prose/tests --cov=config/skills/plain-prose/scripts --cov-branch` (`prose_check.py` stays at 100%); likewise `.venv/bin/python -I -m pytest config/skills/writing-docs/tests --cov=config/skills/writing-docs/scripts --cov-branch` (`doc_lint.py` stays at 100%); and for the lifecycle skill, which runs its script as a subprocess, `LIFECYCLE_COVERAGE_DIR=$(mktemp -d) .venv/bin/python -m pytest config/skills/github-issue-lifecycle/tests --cov=config/skills/github-issue-lifecycle/evals --cov-branch`, then `coverage combine` and `coverage report` in that directory for the script
- Lint: `.venv/bin/ruff check .` and `.venv/bin/ruff format --check .`
- Types: `.venv/bin/mypy`
- Before every push: `.venv/bin/python -I tools/git_hooks.py check-range origin/main` (covers rebase, cherry-pick and `--no-verify` commits, which run no hooks)
- Config check, offline as CI runs it: `.venv/bin/python -I tools/check_config.py --host hosts/personal.json --plugin-index plugins.lock.json --path-rule fail`. Drop `--plugin-index` to check against the live plugins. The path rule fails on any home-directory or scratch path under `tools/`, `config/` or `eval/`; it also scans git-ignored files there, so delete stray `.coverage` files or eval output first (#25).
- Plugin lock: `python3 -I tools/cfg/lock.py` rewrites `plugins.lock.json` from the installed plugins; `python3 -I tools/cfg/lock.py --check` only compares, and also reports vendored files whose upstream copy changed: a changed installed-plugin copy fails it (`vendored`), while a changed marketplace copy, as for `plain-prose`, is only listed (`upstream`). A lock change is its own `chore(deps)` PR (ADR-0005).
- Spec validator: `python3 -I tools/vendor/claude-skills/spec_validator.py --file <SPEC.md> --strict`. Never edit `tools/vendor/`; its hashes are in `VENDORED.json`.
- The `cfg` command (#6): `python3 -I tools/cfg/cli.py plan --host hosts/personal.json` shows what a deploy would do to `~/.claude`; `status` compares the live config with the last deploy; `diff [path]` shows the differences. All three only read, never write, and take `--json`. Exit 0 means nothing to report, 1 means something to report, and 2 means a usage error. `check`, `lock` and `eval` pass their arguments to the tools below. Deploy state lives in `~/.local/share/claude-config/`.
- Procedures for the live config, with the output to expect, are in `docs/runbook.md`; follow it rather than improvising. Its practice section runs everything on a `--target` trial directory.
- Changing the live config (#8): `python3 -I tools/cfg/cli.py deploy --host hosts/personal.json` installs `origin/main` as last fetched (or `--ref`). It stops with a diff on every conflict until it is captured or passed with `--discard-live <path>`, and without `--yes` it only prints the change list, so run it once without `--yes` first. `rollback` switches back to the previous release and leaves `settings.json` alone. `capture [path] --into <feature worktree>` writes live changes into a branch and never commits. `restore [backup]` runs a backup's own `restore.py`. `--target DIR` works on a temporary config directory instead of `~/.claude`. Exit 3 means a deploy failed after changing the live config; the message names the `restore.sh` that undoes it.
- CI's helper (#10): `python3 -I tools/ci.py base|changes|commits|specs` is what the workflow runs; it reads the event from `GITHUB_EVENT_PATH`. CI holds 100% branch coverage on every file that had it when CI began (all but `tools/check_config.py` and `tools/cfg/lock.py`); the `test` job's last coverage step shows the exact command.
- Eval records, checked without a model as CI does: `python3 -I tools/cfg/eval_records.py verify`. It lists the suites the branch needs (from `eval/gates.json`) and whether each record matches.
- Model evals, on the owner's machine, on a clean tree: `TMPDIR=/tmp python3 -I tools/cfg/eval.py` runs the needed suites and writes `eval/records/`. Commit the records and paste the printed summary into the PR. `--suite <name>` picks suites, including a skill's `<skill>-trigger` and `<skill>-behaviour` (`writing-docs`' suites run with `plain-prose` installed beside it, through `eval/doc_evals.py`); `python3 -I eval/hook_harness.py overlay` (or `merge`) runs one hook check without writing a record. `python3 -I tools/cfg/cli.py eval` runs the same thing.
- Symlink check (ADR-0001), on the owner's machine, on a clean tree: `TMPDIR=/tmp python3 -I eval/symlink_check.py` rebuilds the deploy's links in temp config directories, runs `claude -p` three to seven times, and rewrites `docs/evidence/symlink-check.{json,md}`. Run it again after a Claude Code update. Exit 2 also covers recorded results that aren't `pass`, such as the Edit refusals, so read the table. `--inventory DIR` only prints DIR's managed paths.
- Mermaid diagrams (#81), on the owner's machine only, never in CI, because it downloads Node packages and a Chromium build that aren't hash-pinned: `mkdir -p /tmp/mermaid-check && npx -y @mermaid-js/mermaid-cli@11.4.2 -q -i <file.md> -o /tmp/mermaid-check/out.md` for each `.md` file with a mermaid block outside `docs/history/` and `config/vendored-skills/`. Exit 1 means a diagram doesn't parse. Quote every flowchart label (`A["…"]`, `-->|"…"|`) and keep `;` and `#` out of sequence-diagram messages. GitHub's renderer decides, so also view the pushed file there.
- Dependencies: edit `requirements-dev.in`, then regenerate `requirements-dev.txt` with `pip-compile --generate-hashes --allow-unsafe --strip-extras`

## Gates (ADR-0006)

- Pipeline: lifecycle skill, spec, test plan (`senior-qa`; `ci-cd-pipeline-builder` for workflows and the scripts CI calls), `tdd-guide`, one adversarial pass on the spec and one on the PR diff, `ship-gate`.
- Quote pytest, ruff, mypy and `check-range` results before reporting done or opening a PR.
- Merge only after `gh pr checks <N>` is green. CI (`.github/workflows/ci.yml`, #10) runs the gates above that need no model, plus gitleaks over the PR's commits; the local gates still come first, because CI only reports after a push.
- Merge commit or rebase, never squash. Never push to `main`; the pre-push hook and the ruleset refuse it.
- Never check out a fork's PR branch in a clone with hooks enabled: the hooks run the checked-out tree's code. Review fork PRs on GitHub, or in a clone with `core.hooksPath=/dev/null`.

## Where specs live (ADR-0002)

- A change to how claude-config works (`cfg`, CI, the checker, the eval harness, these hooks) or to how it coordinates the skills (the global CLAUDE.md, the Claude Code hooks, the overlays, the settings fragment): the spec goes in the issue body, between the lifecycle skill's spec markers. When such a change merges, the same PR updates `config/SPEC.md`.
- A change to how a skill works: the spec lives in `config/skills/<name>/SPEC.md`, with `REVIEW-NOTE.md` beside it, and the issue links to it. A skill without a `SPEC.md` gets one with its first change in this repo. This overrides the global rule to edit such skills directly.
- Vendored skills (ADR-0002's amendments for #62 and #75) live in `config/vendored-skills/<name>/`; their `VENDORED.json` lists their `SKILL.md`, and they are upstream copies. `config/skills/` holds only the skills maintained here. Copying a newer upstream version, or adapting a file only to pass the repo's checks, isn't a first change; mark the file `adapted` with a `note` and `localSha256`. Never lint or reformat them.
- `docs/history/` is frozen: never edit it after import.
