# claude-config: working on this repo

Work here is tracked in GitHub (`Towerism/claude-config`) with `github-issue-lifecycle`. The repo is public. The architecture doc (`docs/architecture/README.md`) and the ADRs (`docs/adr/`) are the source of truth; a departure needs a new or updated ADR.

## Setup, once per clone

- `git config user.email 2057426+Towerism@users.noreply.github.com` and `git config user.name "Martin Fracker, Jr."`
- `git config core.hooksPath .githooks`
- `python3 -m venv .venv && .venv/bin/pip install --require-hashes -r requirements-dev.txt`
- The excluded-terms list lives in `~/.config/claude-config/deny-terms`, outside the repo. Never read it into output, quote it, or copy it into the repo.

## Commands

- Tests: `.venv/bin/python -I -m pytest` (add `--cov` for branch coverage; `tools/git_hooks.py` stays at 100%)
- Skill tests, which the command above doesn't collect: `.venv/bin/python -I -m pytest config/skills/plain-prose/tests --cov=config/skills/plain-prose/scripts --cov-branch` (`prose_check.py` stays at 100%)
- Lint: `.venv/bin/ruff check .` and `.venv/bin/ruff format --check .`
- Types: `.venv/bin/mypy`
- Before every push: `.venv/bin/python -I tools/git_hooks.py check-range origin/main` (covers rebase, cherry-pick and `--no-verify` commits, which run no hooks)
- Config check, offline as CI runs it: `.venv/bin/python -I tools/check_config.py --host hosts/personal.json --plugin-index plugins.lock.json --path-rule report`. Drop `--plugin-index` to check against the live plugins. The path rule stays `report` until #4 removes the lifecycle skill's scratch paths; #4 switches it to `fail`.
- Plugin lock: `python3 -I tools/cfg/lock.py` rewrites `plugins.lock.json` from the installed plugins; `python3 -I tools/cfg/lock.py --check` only compares, and also reports vendored files whose upstream copy changed: a changed installed-plugin copy fails it (`vendored`), while a changed marketplace copy, as for `plain-prose`, is only listed (`upstream`). A lock change is its own `chore(deps)` PR (ADR-0005).
- Spec validator: `python3 -I tools/vendor/claude-skills/spec_validator.py --file <SPEC.md> --strict`. Never edit `tools/vendor/`; its hashes are in `VENDORED.json`.
- Eval records, checked without a model as CI will: `python3 -I tools/cfg/eval_records.py verify`. It lists the suites the branch needs (from `eval/gates.json`) and whether each record matches.
- Model evals, on the owner's machine, on a clean tree: `TMPDIR=/tmp python3 -I tools/cfg/eval.py` runs the needed suites and writes `eval/records/`. Commit the records and paste the printed summary into the PR. `--suite <name>` picks suites, including a skill's `<skill>-trigger` and `<skill>-behaviour`; `python3 -I eval/hook_harness.py overlay` (or `merge`) runs one hook check without writing a record. This stands in for `cfg eval` until #6.
- Dependencies: edit `requirements-dev.in`, then regenerate `requirements-dev.txt` with `pip-compile --generate-hashes --allow-unsafe --strip-extras`

## Gates (ADR-0006)

- Pipeline: lifecycle skill, spec, test plan (`senior-qa`; `ci-cd-pipeline-builder` for workflows and the scripts CI calls), `tdd-guide`, one adversarial pass on the spec and one on the PR diff, `ship-gate`.
- Quote pytest, ruff, mypy and `check-range` results before reporting done or opening a PR.
- Merge only after `gh pr checks <N>` is green. No checks exist until issue #10 adds CI; until then the local gates above are the bar.
- Merge commit or rebase, never squash. Never push to `main`; the pre-push hook and the ruleset refuse it.
- Never check out a fork's PR branch in a clone with hooks enabled: the hooks run the checked-out tree's code. Review fork PRs on GitHub, or in a clone with `core.hooksPath=/dev/null`.

## Where specs live (ADR-0002)

- A change to how claude-config works (`cfg`, CI, the checker, the eval harness, these hooks) or to how it coordinates the skills (the global CLAUDE.md, the Claude Code hooks, the overlays, the settings fragment): the spec goes in the issue body, between the lifecycle skill's spec markers. When such a change merges, the same PR updates `config/SPEC.md`.
- A change to how a skill works: the spec lives in `config/skills/<name>/SPEC.md`, with `REVIEW-NOTE.md` beside it, and the issue links to it. A skill without a `SPEC.md` gets one with its first change in this repo. This overrides the global rule to edit such skills directly.
- `docs/history/` is frozen: never edit it after import.
