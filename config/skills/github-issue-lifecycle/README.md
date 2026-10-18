# github-issue-lifecycle

A Claude Code skill that keeps a GitHub issue in step with the work done on it: pick up or create the issue, claim it, settle the spec approval mode, store the spec in the issue body, name the branch, check commit messages, open the PR that closes the issue, and clean up after the merge.

It is one piece of a modular pipeline. The global CLAUDE.md strings the pieces together:

```
github-issue-lifecycle (start) → spec-driven-workflow (spec) → senior-qa / tdd-guide (build)
  → adversarial-reviewer (review) → ship-gate (gate) → github-issue-lifecycle (ship, close out)
```

This README is for people. Claude reads `SKILL.md`.

## Description

- Selects the issue from a key (`#42`, `GH-42`, `owner/repo#42`, an issue URL) or searches and asks.
- Files follow-up issues without starting work on them.
- Records the approval mode as an `approval: *` label, trusting only labels the user's own login added.
- Keeps the spec in the issue body between `<!-- spec:start -->` / `<!-- spec:end -->`, with `draft` / `approved` labels.
- Names branches `{type}/{N}-{slug}` and checks Conventional Commits ending `(#N)`.
- Opens PRs with `Closes #N` and verifies GitHub linked them.
- Never merges without the user's authorization and a passing ship-gate, and never squashes.

## Features

| Piece | What it does |
|---|---|
| `SKILL.md` | The procedure Claude follows |
| `scripts/issue_lifecycle.py` | Deterministic helpers: `extract`, `replace`, `branch-name`, `commit-check`, `pr-body-check`, `diff-check`, `spec-status` (stdlib only, no network; the two prose checks also list tells from the sibling `plain-prose` skill) |
| `references/git-workflow.md` | Commit, branch, merge, hotfix and rollback conventions (adapted from claude-skills, MIT) |
| `tests/` | pytest suite for the script and the claim block |
| `evals/` | Trigger and behaviour evals (development only) |

## Installation

Kept in the claude-config repo at `config/skills/github-issue-lifecycle/` and deployed to `~/.claude/skills/github-issue-lifecycle/`. `plain-prose` and `writing-docs` should be installed beside it. Requirements: Python 3.10+, `git`, and `gh` logged in (`gh auth login`).

## Usage

Talk to Claude normally:

- "work on #42"
- "file a follow-up issue: the export drops the BOM"
- "hotfix: production login 500s"
- "open the PR for this and link the issue"

The script can also be run by hand:

```bash
python3 -I scripts/issue_lifecycle.py --help
python3 -I scripts/issue_lifecycle.py branch-name --type feat --issue 42 --title "Add CSV export"
python3 -I scripts/issue_lifecycle.py commit-check --message examples/sample-commit-message.txt
python3 -I scripts/issue_lifecycle.py pr-body-check --body examples/sample-pr-body.md --issue 42
python3 -I scripts/issue_lifecycle.py extract --body examples/sample-issue-body.md
python3 -I scripts/issue_lifecycle.py spec-status --spec SPEC.md
```

## Examples

`examples/` holds a sample issue body, commit message and PR body; `expected_outputs/` holds what the script prints for each.

## Testing

```bash
python3 -I -m pytest -q tests            # unit + integration, no network
python3 -I evals/run_behaviour_evals.py  # model-in-the-loop behaviour evals (costs tokens)
python3 -I evals/run_trigger_evals.py    # trigger accuracy with the real skill (costs tokens)
```

## Troubleshooting

| Symptom | Cause / fix |
|---|---|
| exit 2 | usage error (missing file, bad flag, unknown base ref) |
| exit 3 from `extract` | the issue has no spec block yet |
| exit 4 | broken markers (start without end, or end without start); fix the issue body by hand |
| `gh auth status` fails | run `gh auth login` |
| Both this and `issue-spec-driven-workflow` trigger | the old skill wasn't moved out; rerun the install step |
