# ADR-0005: Verification and CI (free GitHub Actions for deterministic checks; local model evals with hash-bound committed records)

**Status:** Accepted by the owner on 2026-10-16 (proposed 2026-10-15). This pipeline plan was written after loading the `engineering-advanced-skills:ci-cd-pipeline-builder` skill, reading its `pipeline-design-notes.md`, `deployment-gates.md` and `github-actions-templates.md`, and running its `stack_detector.py`. The owner's rules require loading the skill again before any workflow YAML is written (plan issue 10).

GitHub Actions runs every check that needs no model: tests, lint, the config checker, the spec validator, secret scanning and schema checks. The model evals run only on the owner's machine. Each run writes an eval record: a small committed file holding the result and the sha256 of every input. CI checks that the records match the files being merged, without running a model itself. Terms used here are defined in the [architecture overview](../architecture/README.md#terms).

## Context

CI must cost nothing. That rules out model evals, any Anthropic API key or Claude login, paid actions or runners, and scheduled cloud agents, and it means keeping run time short. The repo is public ([ADR-0007](0007-visibility-and-security.md)), so minutes on standard runners are free and unmetered; run time is the only constraint.

The model evals run locally, on the owner's subscription. A reviewer still needs a way to check that they ran against exactly the files being merged.

Plugin skill names and reference paths change from one plugin version to the next, and CI has no plugin cache to check them against.

Because the repo is public, branch protection is available on the Free plan, so the CI jobs can be required checks on `main`.

## Options

For evidence that the model evals ran:

1. A summary in the PR description only. Easy to forget, can't be verified, and lost when the PR is edited.
2. Committed raw transcripts. Large and noisy, and a risk of leaking secrets or personal data (threat T1 in the architecture overview).
3. A small committed result record per suite, bound to the sha256 hashes of its inputs and verified by CI, with the summary also pasted into the PR description.

For checking plugin references in CI:

1. Clone the marketplaces at pinned commits during CI. Depends on the network, runs slower, and pulls third-party code into CI.
2. Skip the name and path checks in CI and rely on local runs. Leaves a gap.
3. Commit `plugins.lock.json` (the plugin lock), an index generated from the installed plugins, and check against it offline in CI. Locally, compare the lock with `installed_plugins.json`.

## Decision: evals

Option 3. `eval/gates.json` maps changed paths (as globs) to the eval suites they require. CI works out which suites a PR needs from `git diff --name-only <merge-base>...HEAD`:

| Change touches | Required local suites |
|---|---|
| `config/CLAUDE.global.md` | `scenario`, the scenario eval: headless `claude -p` runs with the merge-base config as the old side and HEAD as the new side. It passes when nothing that passed before now fails and every scenario for a new rule passes (v2 spec AC-6). Also `rubric`: the CLAUDE.md quality rubric must score at least 80, with one re-run allowed (v2 spec AC-4). |
| `config/skill-overlays/**` | `scenario` |
| `eval/scenarios.json`, `eval/clauses.json`, `eval/run_eval.py` | `scenario`, because the harness or its criteria changed and the baseline has to be rerun |
| `config/hooks/skill_overlay.py`, or the PostToolUse and UserPromptExpansion entries in the fragment | `hook-overlay`: a canary overlay must reach the model in the main session and in a subagent, a control run without the hook must not see it, and the transcript must show no read of `skill-overlays/` (v2 spec AC-7). The hook's 95th-percentile run time must stay under 500 ms. |
| `config/hooks/merge_confirm.py`, or the PreToolUse entry | `hook-merge`: the run's `permission_denials` must contain the merge command and not the view command, with a control run for comparison (v2 spec AC-9) |
| `config/skills/<s>/SKILL.md`, frontmatter `description` changed | That skill's trigger suite, if it has one. The lifecycle skill has `run_trigger_evals.py`; `plain-prose` and `writing-docs` have `<skill>-trigger` ([ADR-0010](0010-prose-for-people.md)). |
| `config/skills/<s>/SKILL.md`, body changed | That skill's behaviour suite, if it has one. The lifecycle skill has `run_behaviour_evals.py`, including case 23; `plain-prose` and `writing-docs` have `<skill>-behaviour`. |
| `plugins.lock.json` | No model suite. Both sides of a scenario eval share the live plugins, so comparing old and new can't show a plugin change. Instead the PR needs notes from the `dependency-auditor` and `skill-security-auditor` skills, and the checker must pass against the new lock. |

Skills without a suite, currently linux-troubleshooting, are gated only by their pytest suites and the checker. CI tells a description change from a body change by parsing the frontmatter at the merge base and at HEAD.

Records live in `eval/records/<suite>.json`. Each run overwrites its suite's record, so git history keeps the earlier runs:

```json
{
  "suite": "scenario",
  "ranAt": "2026-10-20T14:02:11Z",
  "claudeVersion": "2.1.299",
  "models": { "arm": "default", "grader": "default" },
  "harness": { "eval/run_eval.py": "<sha256>" },
  "base": { "ref": "<merge-base sha>", "inputs": { "config/CLAUDE.global.md": "<sha256>", "config/skill-overlays/…": "<sha256>" } },
  "inputs": { "config/CLAUDE.global.md": "<sha256>", "config/skill-overlays/…": "<sha256>", "eval/scenarios.json": "<sha256>" },
  "result": { "pass": true, "regressions": [], "newRuleFailures": [], "rerunsUsed": 1,
              "cases": [{ "id": "S-01", "old": "fail", "new": "pass", "graderReason": "…≤200 chars…" }] }
}
```

`cfg eval` runs the suites the branch's diff requires and refuses to run on a dirty tree. It writes the records and prints a markdown summary for the PR body. Raw transcripts go to `eval/out/`, which git ignores. Each side of an eval runs in its own config directory holding a copied `.credentials.json` with mode 600. Those directories are created under `/tmp`, checked to be outside the repo, and removed in a `finally` block.

The `eval-record` CI job runs `tools/cfg/eval_records.py` with no model. For each required suite it checks five things:

1. the record exists;
2. every `inputs` hash equals the file at the PR head;
3. the `harness` hashes equal the harness at the head;
4. the `base.inputs` hashes equal the merge-base's files;
5. `result.pass` is true. Any later edit to an input file makes the record stale, and CI stays red until `cfg eval` runs again.

This doesn't prove that a model produced the record. It works on trust, and it catches the failures that really happen: forgetting to run the eval, running it on an older revision, or running it against an old base. The reviewer reads the per-case table in the record and in the PR description.

## Decision: plugin versions

Option 3. `cfg lock` writes `plugins.lock.json` from `installed_plugins.json` and the plugin cache. For each plugin it records the id, marketplace repo, version and git commit, and the names of all the plugin's skills. For each skill that CLAUDE.md or an overlay references, it also records the list of relative file paths.

In CI, `check_config.py --plugin-index plugins.lock.json` resolves skill names and overlay paths offline. Locally, `cfg check --live` and `cfg status` compare the lock with `installed_plugins.json`. When they differ, `cfg check` runs against the live plugin cache, and if that passes, `cfg lock` produces a lock-bump PR typed `chore(deps): …` (or `fix` if overlays had to change). The vendored `spec_validator.py` gets the same treatment: a hash mismatch with the installed copy is reported.

The repo records plugin versions and detects changes, but it doesn't pin what the harness installs. The owner decided on 2026-10-16 to keep marketplace auto-update on, so each update arrives as a detected lock change and a lock-bump PR.

## Decision: CI pipeline plan

`stack_detector.py` reports no stack signals today, because the repo is empty and the scratch implementation has no manifest. The commands below are traced to scripts that exist: `check_config.py`, `issue_lifecycle.py commit-check` and `diff-check`, `spec_validator.py --strict`, and three pytest suites run with `python3 -I -m pytest`. Plan issue 1 adds `pyproject.toml` and `requirements-dev.txt`, and the detector must report Python and pip before any YAML is written. ruff, mypy and jsonschema aren't installed on the owner's machine today; they arrive through the pinned `requirements-dev.txt`.

There is one workflow, `.github/workflows/ci.yml`, triggered by `pull_request` and by `push` to `main`. It runs with `permissions: contents: read`, uses `concurrency` to cancel superseded runs, and uses only `ubuntu-latest` runners.

| Job | Needs | Runs when | Commands (each exists or is created by a named plan issue) |
|---|---|---|---|
| `changes` | — | always | `git diff --name-only $BASE...$HEAD`, producing the outputs `code`, `docs_only` and `eval_suites`. Plain git, no third-party filter action. |
| `hygiene` | — | always | Fails if any commit in the range has an author or committer email that isn't a GitHub noreply address, which keeps the owner's email hidden (`Co-Authored-By` trailers aren't author fields). Runs gitleaks from the release binary, verified by sha256, with `--log-opts=$BASE..$HEAD` and the repo's `.gitleaks.toml`, which adds an email rule. Applies a filename deny-list (`.credentials.json`, `*.jsonl`, `history*`, `projects/` and so on). |
| `commits` | `changes` | PRs | `issue_lifecycle.py commit-check` on every commit subject in the range. `prose_check.py` (findings for a reader without context, readability and AI tells) on the commit messages and the PR title and body, report-only, written to the job summary ([ADR-0010](0010-prose-for-people.md)). `issue_lifecycle.py diff-check --base $BASE`, which depends on [ADR-0009](0009-spec-id-hygiene-for-eval-data.md). |
| `lint` | `changes` | `code` | `ruff check .`, `ruff format --check .`, `mypy tools config/hooks` |
| `test` | `lint` | `code` | `python -I -m pytest -q --cov=tools --cov=config/hooks --cov-branch` for the repo tests, plus each `config/skills/*/` suite. The hooks need 100% branch coverage. |
| `config` | `test` | `code` | `check_config.py --plugin-index plugins.lock.json --clauses eval/clauses.json` for each host profile; `spec_validator.py --strict` on every `config/skills/*/SPEC.md`; `jsonschema` checks of the fragment against both schemas; and an AST rule that limits the hooks to standard-library imports and bans `socket`, `urllib`, `http` and `subprocess` |
| `eval-record` | `changes` | `eval_suites` not empty | `python -I tools/cfg/eval_records.py verify --base $BASE` |

The pipeline-plan checklist below is required by the owner's overlay for the ci-cd-pipeline-builder skill, and every row stays:

| Item | Answer |
|---|---|
| Stages traced to detected signals, with commands confirmed | See the job table above. The detector shows no signals until plan issue 1 lands, so this is checked again before the YAML is written. |
| Gate order lint → test → build → deploy | Lint, then test, then config checks. There is no build or deploy stage, because nothing is built and deployment is a local `cfg deploy` run by the owner, never by CI. |
| Cache keys by package manager and lockfile | `actions/setup-python` with `cache: pip` and `cache-dependency-path: requirements-dev.txt`, which is hash-pinned. The gitleaks binary is cached by version. |
| Matrix only where compatibility needs it | One Python version: the floor in `.python-version`, 3.13, the work machine's version (3.13.5); the code is pure standard-library Python. Local runs on the personal machine's 3.14 cover the newer end. A 3.13/3.14 matrix is added only if a version-specific failure appears. |
| Secrets and env vars documented, never embedded | None are used. `permissions` makes `GITHUB_TOKEN` read-only. CI holds no Anthropic credential, by design. |
| Environment protection, protected branches, manual production approval | No environments, since there is no deploy job. A ruleset protects `main`: PRs required, CI jobs required, no force-push. The pre-push hook, the merge-confirmation hook and ship-gate back it up. Production approval is the owner running `cfg deploy`. |
| Rollback for every deploy job | There are no deploy jobs. Rollback for local deploys is described in [ADR-0006](0006-repo-workflow.md). |
| Pitfall 1: a Node pipeline copied into a Python repo | The template is Python-only, with no Node. This is also why semantic-release, a Node tool, isn't adopted ([ADR-0006](0006-repo-workflow.md)). |
| Pitfall 2: deploying before tests are stable | No deploy job |
| Pitfall 3: missing cache keys | Covered in the cache row above |
| Pitfall 4: expensive matrix builds on trivial branches | No matrix. PRs that change only docs run just `hygiene` and `commits`. |
| Pitfall 5: no branch protection around production deploys | Not applicable, since CI has no production deploy. The ruleset protects `main`. |
| Pitfall 6: secrets hard-coded in YAML | There are no secrets to leak |
| Validation before merge | The YAML must parse and match the workflow schema (`check-jsonschema --builtin-schema vendor.github-workflows`, added to the dev dependencies). `act` isn't installed, so the first PR proves the workflow. That PR also adds a deliberately failing fixture commit, reverted afterwards, to show that `eval-record` and `hygiene` turn red. |
| Review checks, not automated | Whether the eval record's content is plausible; review of hook diffs against the bar in [ADR-0007](0007-visibility-and-security.md); the audit notes on plugin bumps; and clause-inventory rows for new rules, since CI only checks rows that already exist. |

The only third-party actions are `actions/checkout` and `actions/setup-python`, pinned to full commit SHAs. gitleaks runs as a checksum-verified binary instead of through `gitleaks-action`, which avoids any licence question and keeps the attack surface smaller.

A full run takes about 2 minutes; a docs-only run takes about 20 seconds.

## Consequences

A PR that changes CLAUDE.md or an overlay costs one local scenario-eval run, plus another after any review fix that touches its inputs. That cost is deliberate, because the record must describe the exact files being merged.

The first scenario record under the new harness comes from plan issue 9. The results from the scratch implementation stay in `docs/history/` as evidence and aren't reused as records, because their inputs and harness differ.

`check_config.py` gains an offline mode that reads the plugin lock. Its live mode stays as it is.

## Revisit when

- The repo goes private. Free private repos get 2,000 minutes a month, of which this pipeline needs about 200, and no branch protection, so the merge step would again rely on `gh pr checks`.
- Anthropic offers a free eval path that is safe to run in CI and that the owner is willing to use. Records could then be produced in CI.
- Tampered or stale records become a real problem. Consider signing records with the owner's SSH key; git can verify SSH signatures without any external service.
