# ADR-0005: Verification and CI (free GitHub Actions for deterministic checks; local model evals with hash-bound committed records)

**Status:** Accepted by the owner on 2026-10-16 (proposed 2026-10-15). This pipeline plan was written after loading the `engineering-advanced-skills:ci-cd-pipeline-builder` skill, reading its `pipeline-design-notes.md`, `deployment-gates.md` and `github-actions-templates.md`, and running its `stack_detector.py`. The owner's rules require loading the skill again before any workflow YAML is written (plan issue 10). Amended 2026-10-16 by #2 (approved by the owner): `config/` is excluded from ruff until #20. Amended 2026-10-16 by #5 (spec approved by the owner): the plugin lock also lists the names of the synced `anthropic-skills:` skills, which aren't plugins, so offline checks can resolve them. Amended 2026-10-16 by #9 (spec approved by the owner): a change to `config/hooks/skill_overlay.py` also requires `scenario`, and until #6 builds the `cfg` command, `cfg eval` runs as `python3 -I tools/cfg/eval.py`. Amended 2026-10-16 by #12 (spec approved by the owner): a change to `plain-prose`'s scripts, references or behaviour cases also requires `plain-prose-behaviour`, and ruff now covers skills built in this repo, so only the hooks, the lifecycle skill and vendored files stay excluded. Amended 2026-10-17 by #13 (spec approved by the owner): a change to `writing-docs`' scripts, references or behaviour cases requires `writing-docs-behaviour`, and its trigger cases or `plain-prose`'s description require `writing-docs-trigger`; both run through `eval/doc_evals.py`. Amended 2026-10-19 by #20 (spec approved by the owner): the hooks are linted, formatted, type-checked and measured for coverage like the rest of the repo, and the hook review bar's H1 and H6 rules run as repo tests. Amended 2026-10-19 by #10 (spec approved by the owner): the workflow as built, with the departures listed under "As built" below. Amended 2026-10-19 by #15 (spec approved by the owner): the `hook-session-start` suite for the default-lifecycle hook, and that hook joins the `scenario` suite's paths. Amended 2026-10-20 by #17 (spec approved by the owner): the plugin lock no longer lists synced skills, which reverses #5's amendment, and #17's PR 1b is a one-time exception to the rule that a lock change is its own PR; see [Amendment for #17](#amendment-for-17-2026-10-20). Amended 2026-10-20 by #62 (spec approved by the owner): vendored skills replace the two `claude-code-skills` plugins, and the eval harness takes repo skills from the tree under test; see [Amendment for #62](#amendment-for-62-2026-10-20). Amended 2026-10-20 by #75 (spec approved by the owner): the vendored skills live in `config/vendored-skills/`, and the checks read both skill trees; see [Amendment for #75](#amendment-for-75-2026-10-20). Amended 2026-10-20 by #77 (spec approved by the owner): each suite runs in a smoke tier or a full tier, which the diff decides, and all suites run at once under one limit on calls; see [Amendment for #77](#amendment-for-77-2026-10-20).

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
| `config/hooks/skill_overlay.py` | `scenario` as well as `hook-overlay` (below), because the scenario arms get their overlay text from this hook |
| `eval/scenarios.json`, `eval/clauses.json`, `eval/run_eval.py` | `scenario`, because the harness or its criteria changed and the baseline has to be rerun |
| `config/hooks/skill_overlay.py`, or the PostToolUse and UserPromptExpansion entries in the fragment | `hook-overlay`: a canary overlay must reach the model in the main session and in a subagent, a control run without the hook must not see it, and the transcript must show no read of `skill-overlays/` (v2 spec AC-7). The hook's 95th-percentile run time must stay under 500 ms. |
| `config/hooks/merge_confirm.py`, or the PreToolUse entry | `hook-merge`: the run's `permission_denials` must contain the merge command and not the view command, with a control run for comparison (v2 spec AC-9) |
| `config/hooks/lifecycle_default.py`, the SessionStart or SubagentStart entry, or `eval/session_harness.py`, `eval/hook_harness.py` or `eval/run_eval.py` | `hook-session-start`: in a temporary layout shaped like a deployment, whose host profile names a random canary skill, a run with no tools must quote the canary, a subagent's own reply must quote it while the prompt it was given doesn't, and a control run without the hook must not see it ([ADR-0011](0011-tracker-agnostic-lifecycle.md), #15). The hook's 95th-percentile run time must stay under 500 ms. The hook also requires `scenario`, because every scenario starts with the line it prints. |
| `config/skills/<s>/SKILL.md`, frontmatter `description` changed | That skill's trigger suite, if it has one. The lifecycle skill has `run_trigger_evals.py`; `plain-prose` and `writing-docs` have `<skill>-trigger` ([ADR-0010](0010-prose-for-people.md)). Since #62, any change to any repo skill's `SKILL.md` also requires `plain-prose-trigger` and `writing-docs-trigger`, because their sessions list every repo skill; since #75 that includes `config/vendored-skills/<s>/SKILL.md`. |
| `config/skills/<s>/SKILL.md`, body changed | That skill's behaviour suite, if it has one. The lifecycle skill has `run_behaviour_evals.py`, including case 23; `plain-prose` and `writing-docs` have `<skill>-behaviour`. |
| `config/skills/github-issue-lifecycle/scripts/**`, `references/**`, `assets/**`, or its `evals/evals.json`, `evals/run_behaviour_evals.py` or `evals/fake_gh.py` | `lifecycle-behaviour` as well, because the model runs the script and reads those files during the cases, and the fake `gh` answers its calls (#4). `evals/run_behaviour_evals.py` also requires `lifecycle-trigger`, because the trigger runner builds its config with it, as do `evals/trigger-evals.json` and `evals/run_trigger_evals.py`. Both runners decide on totals with no reruns (#4). |
| `config/skills/jira-ticket-lifecycle/scripts/**`, `references/**`, `assets/**`, or its `evals/evals.json`, `evals/run_behaviour_evals.py` or `evals/fake_cli.py` | `jira-ticket-lifecycle-behaviour` as well, because the model runs the script and reads those files during the cases, and the fake CLI answers its `jira`, `gh` and REST calls (#16). Its `SKILL.md` description, `evals/trigger-evals.json` and both runners require `jira-ticket-lifecycle-trigger`, which runs with `github-issue-lifecycle` installed beside it, because the two lifecycle skills compete on both machines. Both runners decide on totals with no reruns, as the GitHub ones do. |
| `config/skills/plain-prose/scripts/**`, `config/skills/plain-prose/references/**`, or its `evals/behaviour-evals.json` | `plain-prose-behaviour` as well, because the checker and the reference lists shape what the skill makes Claude write. Its `evals/trigger-evals.json` requires `plain-prose-trigger`. Both run through `eval/skill_evals.py`. |
| `config/skills/writing-docs/scripts/**`, `config/skills/writing-docs/references/**`, or its `evals/behaviour-evals.json` | `writing-docs-behaviour` as well, for the same reason. Its `evals/trigger-evals.json`, and `plain-prose`'s `description`, require `writing-docs-trigger`, because the two skills compete in every trigger run. Both run through `eval/doc_evals.py`, which installs `plain-prose` beside `writing-docs` and compares the skill with `plain-prose` alone. |
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

Since #20 (2026-10-19), `pyproject.toml` lints, formats and type-checks the hooks like the rest of the repo, and coverage measures them; only vendored upstream files and frozen copies stay excluded. #2 had imported the hooks byte for byte for adoption, which #40 dropped. Skills built in this repo, starting with `plain-prose` (#12), are linted and type-checked; the upstream files they vendor are not. The lifecycle skill is linted and formatted, without `mypy`, as the owner chose in #4. The hook review bar's syntax-tree rule (H1 in [ADR-0007](0007-visibility-and-security.md)) and its command rule (H6) are repo tests in `tests/test_hook_review_bar.py`, so the `test` job runs them; they are the `config` job's AST rule above. The rest of this design is unchanged.

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

## As built (#10, 2026-10-19)

`.github/workflows/ci.yml` follows the job table above, with these differences, each approved in #10's spec:

- **The base commit and the docs-only decision come from `tools/ci.py`**, a tested standard-library helper, not from YAML. On a PR the range starts at the merge base and the job checks out the PR's head commit, not GitHub's synthetic merge commit, whose committer would fail the identity check. On a push it starts at the previous tip of `main`, and the code jobs always run.
- **`eval-record` always runs.** `eval_records.py verify` already decides which suites a change needs and takes under a second, so the `changes` job doesn't repeat that logic as an `eval_suites` output.
- **gitleaks isn't cached.** Only `actions/checkout` and `actions/setup-python` are allowed, and the tarball is small; it's downloaded and checked against its sha256 on every run.
- **The settings schema is one schema, not two.** `schemas/claude-code-settings.json` is SchemaStore's, vendored with its licence; `tests/test_settings_schema.py` checks the rendered fragment against it. The fragment's own limits, its owned keys and the hook command form, are already enforced by `tools/cfg/render.py` and the H6 test, so no second schema was written.
- **Coverage is held at 100% branch on every file that had it** when CI began, which is all but `tools/check_config.py` and `tools/cfg/lock.py`. The repo suite and the two prose skills' suites run separately and append to one coverage file, because two of them have a `test_vendored.py`.
- **`hygiene` reuses `tools/git_hooks.py check-range`** for identities, tokens, email addresses and denied paths, then runs gitleaks over the range. `check-range` accepts GitHub's own committer address beside a noreply author, so merges made on GitHub pass ([ADR-0007](0007-visibility-and-security.md)).
- **The spec check passes the validator's exit 1.** It exits 1 on warnings alone, and every spec here carries the HTTP-endpoint warning, which doesn't apply; only exit 2, a score under 80, fails.

## Consequences

A PR that changes CLAUDE.md or an overlay costs one local scenario-eval run, plus another after any review fix that touches its inputs. That cost is deliberate, because the record must describe the exact files being merged.

The first scenario record under the new harness comes from plan issue 9. The results from the scratch implementation stay in `docs/history/` as evidence and aren't reused as records, because their inputs and harness differ.

`check_config.py` gains an offline mode that reads the plugin lock. Its live mode stays as it is.

## Revisit when

- The repo goes private. Free private repos get 2,000 minutes a month, of which this pipeline needs about 200, and no branch protection, so the merge step would again rely on `gh pr checks`.
- Anthropic offers a free eval path that is safe to run in CI and that the owner is willing to use. Records could then be produced in CI.
- Tampered or stale records become a real problem. Consider signing records with the owner's SSH key; git can verify SSH signatures without any external service.

## Amendment for #17 (2026-10-20)

**Synced skills are per machine.** claude.ai syncs each account's skills into `~/.claude/skills/synced/`, and the work machine uses a different account from the personal one. #5 had the plugin lock list the personal account's synced skills, so offline checks could resolve `anthropic-skills:` names. On the work machine that list could never match: `lock.py --check` failed, `cfg status` warned for good, and the offline check accepted names that machine might not have. From #17:

- `plugins.lock.json` records no synced skills, and its format is `lockVersion` 2. `check_config.py` reads only version 2.
- `lock.py --check` and `cfg status` compare plugins only. `cfg status` still warns when a synced skill shares a repo skill's name, which it reads from that machine alone.
- `check_config.py` reports any `anthropic-skills:` name in the CLAUDE.md or an overlay as a finding (`syncedSkills`), in live, offline and host mode, even when the skill is synced on that machine. CI's `config` job and the deploy's preflight therefore refuse such a config. A skill both machines need comes from a plugin or a repo skill instead.
- skill-creator, the one synced skill the CLAUDE.md named, now comes from the plugin `skill-creator@claude-plugins-official`, which both host profiles list.

**A one-time exception to "a lock change is its own PR".** #17's PR 1b adds the skill-creator plugin to the lock and changes the lock's format in the same PR. The owner chose that on 2026-10-20 ("D-4(1), D-5(2)"), because both changes rewrite the same file and land together with the checker change that reads the new format. The lock change still sits in its own `chore(deps)` commit, and the PR carries the dependency and security audit notes this ADR asks for. The rule stands as it is for every other lock change.

## Amendment for #62 (2026-10-20)

**Vendored skills replace two plugins.** The owner removed `engineering-skills@claude-code-skills` and `engineering-advanced-skills@claude-code-skills` and kept 17 of their skills as repo skills ([ADR-0002's amendment](0002-repo-layout.md#amendment-for-62-2026-10-20)). For the checks this ADR sets up:

- The checker resolves those skills' names, in the CLAUDE.md and as overlay names, as repo skills that the host profile lists. Once the plugins are uninstalled, the plugin lock drops them and their `files` lists, and keeps the two `claude-plugins-official` plugins.
- Each vendored skill's `VENDORED.json` uses the marketplace form that `plain-prose`'s does. `lock.py --check` compares each recorded `sha256` with the file in the `claude-code-skills` marketplace clone and reports a difference as `upstream`, without failing. The marketplace stays in the settings fragment's `extraKnownMarketplaces` for that reason, as the owner chose on 2026-10-20; a known marketplace adds no skills to a session.
- `tools/vendor/claude-skills/VENDORED.json` moves to the same form, because the plugin copy it was compared with is gone. The file is the one `config/vendored-skills/spec-driven-workflow/scripts/` ships (in `config/skills/` until #75), and a test keeps the two copies identical.
- Tests check the repo copies: unadapted files against `sha256`, adapted ones against `localSha256`.

**The eval harness takes repo skills from the tree under test.** A branch's model suites run before the branch is deployed. So `overlay_text` links the tree's `config/skills/` into the overlay hook's home, and the `hook-overlay` suite copies its canary skill, `senior-qa`, from the tree into its session. `make_config_dir` sets the two removed plugins to `false` in the settings it copies, until both machines have uninstalled them; #63 removes that. The skill and doc trigger suites therefore no longer run with the plugins' 72 skills beside the skill they test, so their records from #62 on aren't comparable with earlier ones.

**The trigger suites list what a deployed session lists.** With the plugins switched off, the `plain-prose` and `writing-docs` trigger sessions would have listed only the skills under test. The owner asked that they "match a real session", so from #62 both copy every skill under the tree's `config/skills/` into their session; the behaviour suites still install only the skills they compare. `writing-docs-trigger` gives ordinary queries three turns instead of two, because a run may load a competitor such as `senior-architect` first, and both suites count a run as turn-limited when it hits the cap without loading the skill under test. Any repo skill's `SKILL.md` now requires both trigger suites (`eval/gates.json`). With those competitors listed, both suites fell below 85% recall, and the owner decided on 2026-10-20 to "lower the gate to .70 and file a ticket to fix this back up to .85": the gate is 0.70 until #67 raises both skills' recall and restores it. The two skills' `SPEC.md` files still describe the old setup; #67 lists each difference.

**The removal arrives as two PRs.** PR 1 stops enabling the plugins and carries the auditors' findings. A plugin leaves the lock only after it's uninstalled, which follows PR 1's deploy, so PR 2 is the `chore(deps)` lock change and links PR 1's findings. [ADR-0007's amendment](0007-visibility-and-security.md#amendment-for-62-2026-10-20) allows this.

## Amendment for #75 (2026-10-20)

**The checks read both skill trees.** The vendored skills moved to `config/vendored-skills/` ([ADR-0002's amendment](0002-repo-layout.md#amendment-for-75-2026-10-20)). Nothing about what is checked changes, only where:

- `lock.py --check` reads the `VENDORED.json` records under `config/vendored-skills/` as well as `config/skills/` and `tools/vendor/`.
- The eval harness lists the skills of both trees: the overlay arms link them all, the trigger suites copy them all, and the `hook-overlay` suite takes `senior-qa` from `config/vendored-skills/`. Both trigger suites' paths in `eval/gates.json` include `config/vendored-skills/*/SKILL.md`, so a vendored skill's `SKILL.md` still requires them.
- The deploy smoke test links each skill of both trees, so its overlay case finds `adversarial-reviewer`.

Moving the files changed the trigger suites' inputs and editing the harness changed their harness hashes, so #75 re-recorded every suite those changes require.

## Amendment for #77 (2026-10-20)

**Each suite runs in a smoke tier or a full tier, and the diff decides which.** #75 moved 17 skills byte for byte, which can't change a result, and still needed nine full suites, about 23 minutes run one after another. The owner set two limits on 2026-10-20: a smoke set that takes at most 1 minute and runs when a change can't move a result, and a full set that takes at most 3 minutes and runs only when one can.

- **The rule.** `eval_records.py` gives a required suite the smoke tier only when, between the merge-base and HEAD, its definition in `eval/gates.json` (apart from its smoke list), its harness files, `tools/cfg/eval.py` and its selectors are unchanged, and each of its inputs either keeps its path or moves to a path that `equivalentPaths` in `eval/gates.json` declares equivalent, with the same content. Today that list holds `config/skills/` and `config/vendored-skills/`. A swap, an undeclared rename, an added or removed input, a new suite or a script suite needs the full tier.
- **Records.** Each record holds `tier` (a record from before #77 has none and counts as full), `durationSeconds`, and for smoke `smokeCases`. The `eval-record` job's check fails a smoke record where the full tier is needed, a smoke record whose cases aren't the suite's smoke list, and a full record with a different number of cases from its case file. It prints the tier each required suite needs and the tier it found.
- **The smoke tier.** Each suite with cases names 1 to 3 smoke cases in its `smoke` list: ids, or 0-based positions for the trigger suites, whose queries have no ids. Each passed every run of its suite's latest full record, and a test checks that. A smoke run covers those cases once each, with no rerun; `scenario` runs them on HEAD's arm only, the hook suites skip their subagent run and time 5 runs, and `rubric` grades once. The four lifecycle suites have no smoke tier.
- **The full tier within 3 minutes.** `cfg eval` runs all chosen suites at the same time, under one limit on `claude` calls open at once (`--workers`), and a script suite takes its share of that limit. `plain-prose-trigger` runs each query twice and a third time only on a split, as `writing-docs-trigger` already did. The summary prints each suite's time and the run's total against its budget; a run over budget is reported and still passes or fails on its results. If the full set still takes longer than 3 minutes, the owner decided that raising `--workers` is the only lever to use without asking again.
