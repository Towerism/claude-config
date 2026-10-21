# claude-config

claude-config is one person's Claude Code setup, kept in git. It makes Claude Code work through a development pipeline built from small skills. Each skill has one job: start the issue, write the spec, plan the tests, build test-first, review, or check the merge. A short global `CLAUDE.md` puts those skills in order and says who approves and merges. The owner's rules for each skill live in a separate overlay file, which a hook adds to the conversation when that skill loads. The skills themselves stay unedited, so one can be swapped without touching the others. [How the skills fit together](docs/architecture/skills.md) explains the design and the reasons for it.

The repo also holds `cfg`, the tool that deploys this configuration into `~/.claude`, and the tests, evals and CI that check every change first. The owner's personal machine has run it through `cfg` since 2026-10-19 and the work machine since 2026-10-20. The repo is public, so everything committed here is world-readable.

| Path | What it holds |
|---|---|
| `config/` | Everything that is deployed: the global CLAUDE.md (as `CLAUDE.global.md`), overlays, hooks, the settings keys the repo owns, and the skills |
| `hosts/` | One profile per machine, listing which skills, hooks and plugins it receives |
| `tools/cfg/` | The `cfg` command: plan, status, diff, deploy, rollback, capture, restore, check, lock and eval |
| `eval/` | Model evals, run on the owner's machine, and their committed records |
| `docs/` | The architecture docs, ADRs and runbook |

To deploy, handle drift, roll back or set up another machine, follow [docs/runbook.md](docs/runbook.md).

## Set up a clone

Run these once in each new clone. The first two lines keep the owner's email out of public commit metadata, and the third turns on the checks that run before every commit and push.

```bash
git config user.email 2057426+Towerism@users.noreply.github.com
git config user.name "Martin Fracker, Jr."
git config core.hooksPath .githooks

python3 -m venv .venv
.venv/bin/pip install --require-hashes -r requirements-dev.txt
```

Then check that everything passes:

```bash
.venv/bin/python -I -m pytest
.venv/bin/ruff check .
.venv/bin/ruff format --check .
.venv/bin/mypy
```

To keep private words out of the repo (client names, for example), list them one per line in `~/.config/claude-config/deny-terms`. That file is never committed. Without it the hooks still run, but they print a warning and skip that check.

## What the hooks check

The hooks in `.githooks/` call `tools/git_hooks.py`. Before a commit, they refuse:

- an author or committer email that isn't a GitHub noreply address;
- tokens such as `ghp_…` or `sk-…`, and email addresses other than the allowed noreply and `example.com` ones;
- files that must never be versioned, such as `.credentials.json`, `*.jsonl` or `projects/`;
- any word from your excluded-terms list, in a file, a file name or the commit message.

When `gitleaks` is installed, it runs as well. `pre-push` refuses any push to `main`, which changes only through pull requests. For other pushes it repeats the checks on every commit being pushed (its identity, its message and each file it adds or changes) and on each annotated tag. That catches commits made with `--no-verify`, `rebase` or `cherry-pick`, which run no commit hooks. To run the same checks without pushing, use `.venv/bin/python -I tools/git_hooks.py check-range origin/main`.

Three limits are worth knowing:

- `git push --no-verify` skips the push checks too. CI is the backstop: it runs gitleaks and the commit checks over every pull request's commits.
- When you write a commit message in an editor, git lists the changed and untracked file names on `#` lines, and the commit-msg hook scans those lines. A file whose name looks like an email address (high-resolution images named with `@2x` often do) or holds an excluded term will refuse the commit. Use `git commit -m`, `-F` or `--no-status` instead.
- The hooks run whatever code is checked out. Don't check out a pull request from a fork in a clone with hooks enabled. Review it on GitHub, or in a clone where `core.hooksPath` is set to `/dev/null`.

## Where the design lives

- [docs/architecture/skills.md](docs/architecture/skills.md): what each skill does, how the global CLAUDE.md, overlays and hooks combine them, and why the configuration is built that way.
- [docs/runbook.md](docs/runbook.md): what to type to deploy, handle drift, roll back, undo and set up a second machine, with the output to expect.
- [docs/architecture/README.md](docs/architecture/README.md): how the repo is laid out, how deploys and rollbacks work, and the threats it guards against.
- [docs/architecture/plan.md](docs/architecture/plan.md): the 17 build issues, all now closed, and the owner's resolved questions.
- [docs/adr/](docs/adr/): the architecture decision records. Any change that departs from one needs a new or updated ADR.
