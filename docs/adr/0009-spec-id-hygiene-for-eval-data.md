# ADR-0009: Requirement IDs are allowed in code when living documentation defines them

**Status:** Accepted by the owner on 2026-10-16 (proposed 2026-10-16). This replaces the allow-list design the owner accepted earlier the same day, at the owner's direction. The change goes through the lifecycle skill's spec (plan issue 4), under adversarial approval.

The lifecycle skill keeps requirement IDs out of code because the spec they point to usually lives on a GitHub issue, where a reader of the code can't follow them. The owner's rule on 2026-10-16 narrows that: an ID is allowed in code when it appears in the repo's living documentation, because then a reader can look it up. The lifecycle skill's `diff-check` is amended to apply that rule. Terms are defined in the [architecture overview](../architecture/README.md#terms).

## Context

Before a PR is opened, the lifecycle skill's `issue_lifecycle.py diff-check` must report no requirement IDs in added lines outside Markdown files (lifecycle spec FR-16). IDs here means forms like `FR-12` or `AC-3`, and identifiers like `test_ac3_…`. This repo has code and data that cite requirements, and in every case the spec they cite is committed here:

- The clause inventory, `eval/clauses.json`, uses dispositions such as `superseded-by:FR-7`, a format the v2 spec defines. The v2 spec documents the payload, so it will live beside it as `config/SPEC.md`.
- Every row of `eval/scenarios.json` lists the v2 spec requirements it tests, as in `"covers": ["FR-2", …]`. That is by design: the v2 spec's behaviour eval (its AC-6) requires every requirement to be covered.
- Eval records in `eval/records/*.json` quote those scenario rows.
- The lifecycle skill's own tests in `config/skills/github-issue-lifecycle/tests/` cite its spec, which sits beside its `SKILL.md`.
- The installer code ported from scratch cites the v2 spec's install requirement (FR-23) in comments.

A second problem needs fixing at the same time. One lifecycle test, `test_package.py::test_nothing_that_could_trigger_is_installed_yet`, fails now. It checked that nothing was installed while the skill was being built in staging (lifecycle spec FR-3 and AC-2). That stopped being true once the skill was installed, and the staging path it names is in the scratch directory this repo retires.

## Options

1. **A committed allow-list of paths** (`.spec-id-allow`, one glob per line). Explicit, but it is a second thing to maintain, and it allows any ID in those paths, including IDs from issue-based specs.
2. **The owner's rule: an ID is allowed when it appears in living documentation in the repo.** No list to maintain. The check stays strict wherever the spec lives on an issue.
3. **Exempt this repo from `diff-check`.** That contradicts a MUST in an approved spec, and it drops the check entirely.

## Decision

The repo takes option 2. The lifecycle spec is amended in plan issue 4 so that `diff-check` works as follows.

The rule has two parts, both set by the owner on 2026-10-16.

**A spec lives in the repo when it sits next to the thing it documents.** A skill's `SPEC.md` beside its `SKILL.md` is the existing case. The v2 spec beside the payload it documents (`config/SPEC.md`) is the second. As a check: a spec file is living when its directory also holds its subject, meaning a `SKILL.md` or any file that isn't Markdown. A spec added anywhere else, such as a `specs/` folder of Markdown files, is still reported, so specs for repo work stay on their issues.

**An ID is defined when it appears anywhere in living documentation, in any form.** Living documentation means every maintained document in the repo: living specs, ADRs, architecture docs, READMEs and runbooks. Archived material doesn't count. That means any path with a `history/` or `archive/` segment, such as `docs/history/`, which holds frozen copies that nobody maintains. "Any form" means the check compares IDs loosely: `FR-23`, `FR23`, `fr-23` and the identifier form `fr23_` all count as the same ID, wherever they appear in a living document, in a requirement list or in running text.

An ID in an added line is allowed when it is defined in this sense. Every other ID is reported, as now. A repo with no living documentation that mentions IDs therefore behaves exactly as before.

The rule belongs to the lifecycle, not to GitHub. `jira-ticket-lifecycle` ([ADR-0011](0011-tracker-agnostic-lifecycle.md)) is the Jira counterpart of the GitHub lifecycle skill, and the owner decided on 2026-10-16 that the same rule applies to it. Its spec states the rule from the start, so no later amendment is needed.

The same amendment also does the related maintenance:
- The staging requirement (FR-3 and AC-2) is marked as satisfied by the install, and the stale test is removed or rewritten.
- `run_behaviour_evals.py` uses paths relative to the repo instead of the scratch deliverable path.
- The paths in the staging requirement and in the spec's list of related specs point at the repo layout.

Editing the lifecycle spec returns it to draft. It then goes through one adversarial review under adversarial approval (the owner's choice on 2026-10-16), and the change is built test-first in the skill's own tests.

## Consequences

- Until the amendment lands, CI runs `diff-check` in report-only mode, and each PR description lists its findings. This is the only CI check that starts as a warning.
- Ported code no longer has to strip citations of committed specs, such as the installer's references to the v2 spec's install requirement.
- IDs are not namespaced, and any mention in living documentation counts. If an issue-based spec reuses a number that living documentation already mentions, such as `FR-3`, the check can't tell them apart and allows it. In this repo the v2 spec defines `FR-1` to `FR-24`, the lifecycle spec defines its own set, and the ADRs cite several of both, so collisions are likely. Issue specs here can avoid them by starting their numbering above the committed ranges, or by using a prefix.

## Revisit when

- An ID collision lets an issue-spec reference slip into code. Namespace IDs per spec then, for example by requiring code to cite the spec's path next to the ID.
- The "directory also holds a non-Markdown file" test misjudges a real case, for example a spec for a docs-only subject. Refine how a spec's subject is recognised.
