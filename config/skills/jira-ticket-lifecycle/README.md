# jira-ticket-lifecycle

A Claude Code skill that runs the Jira side of a code change: it picks up or creates the ticket, claims it, records how the spec gets approved, publishes the spec into the ticket, opens the draft PR on GitHub and links it, and moves the ticket to review and then to done. It is the Jira counterpart of `github-issue-lifecycle`, and it holds no value from any Jira project: the project key, site and workflow states are settings on the machine that uses it.

Claude loads `SKILL.md`; this file is for people. The design and its reasons are in [SPEC.md](SPEC.md) and [ADR-0011](../../../docs/adr/0011-tracker-agnostic-lifecycle.md).

## Set up a machine

1. Install the jira CLI (`ankitpokhrel/jira-cli`) and run `jira init`.
2. Export `JIRA_API_TOKEN` for non-interactive shells, for example in `~/.zshenv`.
3. Log in to GitHub with `gh auth login`.
4. Add a `jira` section to `~/.config/claude-config/local.json`, as [references/settings.md](references/settings.md) shows.
5. Check it: `python3 -I scripts/ticket_lifecycle.py settings` prints the resolved settings, and `python3 -I scripts/ticket_lifecycle.py auth` checks the CLIs and the token.

## What's here

| Path | What it is |
|---|---|
| `SKILL.md` | The instructions Claude follows. |
| `SPEC.md`, `REVIEW-NOTE.md` | The approved spec and its review notes. Change the spec first, under its approval mode. |
| `scripts/ticket_lifecycle.py` | The one script the skill runs, for every Jira step and every text check. Stdlib only. |
| `references/` | The commands the script runs, and the settings. |
| `assets/pr-body-template.md` | The PR body Claude fills in. |
| `examples/`, `expected_outputs/` | Sample inputs with invented values, and the script's output for them. |
| `tests/` | Tests of the script against a fake jira and gh CLI and a local REST server. |
| `evals/` | The trigger and behaviour evals, their fake CLI and their runners. |

## Run the tests

From the repo root, with the dev environment set up:

```bash
.venv/bin/python -I -m pytest config/skills/jira-ticket-lifecycle/tests
```

For branch coverage of the script, which runs as a subprocess:

```bash
d=$(mktemp -d); LIFECYCLE_COVERAGE_DIR=$d .venv/bin/python -m pytest config/skills/jira-ticket-lifecycle/tests
cd "$d" && coverage combine && coverage report -m --include='*ticket_lifecycle.py'
```

The model evals run with `TMPDIR=/tmp python3 -I tools/cfg/eval.py --suite jira-ticket-lifecycle-trigger,jira-ticket-lifecycle-behaviour` on a clean tree.
