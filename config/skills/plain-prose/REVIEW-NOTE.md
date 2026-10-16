# Review note: the `plain-prose` spec

This note records the single adversarial review of `SPEC.md` for issue [#12](https://github.com/Towerism/claude-config/issues/12), run on 2026-10-16 with `engineering-skills:adversarial-reviewer` and the owner's overlay for it. The approval mode is manual review with an adversarial pass, so the spec stays a draft until the owner approves it in their own words.

**Verdict:** one critical finding, now fixed in the spec. Two of the three warnings are fixed and one is accepted; the one note is fixed. The owner sees every change below before approving, because the review changed the spec.

Before writing findings, the reviewer checked two assumptions against real `claude` output saved by the #9 eval runs. The `system`/`init` stream event has a `skills` list, so the per-run isolation check in FR-23 can be built. A subagent's `Skill` call appears in the stream with `parent_tool_use_id` set, so the delegated trigger cases in FR-24 can be scored.

## Critical

**An upstream change would have failed `lock.py --check`, against ADR-0010.** The draft reported a changed or missing marketplace copy in the `vendored` list, which makes `lock.py --check` exit 1. [ADR-0010](../../../docs/adr/0010-prose-for-people.md) says such a change is reported "as a prompt to review, not as a failure". Fixed in FR-30, EC-6, AC-18 and the API contract. Marketplace entries now go in a separate `upstream` list that is printed under its own heading and never changes the exit code. Entries for installed plugins, such as `spec_validator.py`, keep their current behaviour.

## Warnings

**Fixed: the eval runs could have reached GitHub with the owner's login.** The trigger and cold-reader runs copy the live settings, and their prompts ask for issue comments and PR descriptions. The draft said "only the Skill, Read and Agent tools" without saying how, and gave the write arm Bash. The live allowlist has no `gh` rule today, but a later one would let an eval run post for real. FR-23 now requires `--tools` (which removes tools, where a pre-approval only skips the prompt), a stub `gh` first on `PATH` and an empty `GH_CONFIG_DIR`, as #9 did for the merge check. FR-24 and FR-26 name the exact tool lists, the write arm may only run `prose_check.py`, and AC-14 checks all of it.

**Fixed: ADR-0005's ruff note would have become false.** FR-31 narrows the `config/` exclusion in `pyproject.toml`, but ADR-0005 says `config/` stays excluded until #20, and FR-32 only amended its gates table. FR-32 now changes both places in ADR-0005.

**Accepted: each cold-reader case rests on one reader sample.** A case compares one AI-sounding flag for the skill arm with one for the baseline, so model noise can fail a sound case. Accepted: this is the scenario suite's rule too, one counted rerun per failing case. Every rerun goes in the record and the PR. If the grading misfires, ADR-0010's "Revisit when" section covers tuning it.

## Notes

**Fixed: where the tell-word lists come from was unclear.** FR-13 now says the lists live in `prose_check.py`, drawn from the vendored checklist and the detector's phrase list. A reference change is then reviewed by hand and can't silently change the checker.

**Filed, not part of this spec: the live CLAUDE.md no longer matches the v2 manifest.** The owner's live `~/.claude/CLAUDE.md` has gained the prose line, so its sha256 no longer matches the v2 install manifest. Under [ADR-0004](../../../docs/adr/0004-drift-and-adoption.md), adoption (#7) aborts on any mismatch. Filed as a follow-up for #7 to plan for.

## Spec changes made because of this review

| Where | Change | Why |
|---|---|---|
| FR-30, EC-6, AC-18, API contract | Marketplace differences go in a separate `upstream` list that never sets the exit code | ADR-0010: an upstream change is a prompt to review, not a failure |
| FR-23, FR-24, FR-26, AC-14 | `--tools` lists, a stub `gh`, an empty `GH_CONFIG_DIR`, and only `prose_check.py` allowed in the write arm | Eval prompts about issues and PRs must never reach GitHub with the owner's login |
| FR-32 | ADR-0005's ruff note is amended too | FR-31 makes the old note false |
| FR-13 | The tell lists live in `prose_check.py` | Removes an ambiguity about the checker's source of truth |

# Second review: the narrowed scope (2026-10-16)

This section records the single adversarial review of one change to the approved spec. The first live `plain-prose-trigger` run scored 0.7. Six of its ten near misses (a regex, a subagent prompt, a config bump, a failing-test fix, JSON to TOML, a bash one-liner) loaded the skill, because the description put chat replies in scope and every coding task ends in a chat reply. The owner then decided: "chat replies are not in scope. but since code comments are, coding-only tasks must remain scope to that extent". The spec went back to draft, and only this change was reviewed.

**What changed:** chat replies and questions to the user leave the description (FR-2) and the CLAUDE.md line (FR-15); code comments and docstrings stay in. FR-25 rebuilds the trigger cases around that line, with a `kind` on each case so AC-17 can check the mix. FR-28 calls the third rewrite case a reply drafted for the owner to send. New FR-33 amends ADR-0010 and the plan's Q-9, which both say chat replies are covered.

**Verdict:** no critical finding. Two warnings, both fixed; three notes, listed below.

## Warnings

**Fixed: one example near miss wasn't a near miss under the new rule.** FR-25 listed "a regex" as a coding task with no text for people. Adding a regex to a validator is exactly where a careful coder writes a comment, which the owner kept in scope, so the case would punish correct behaviour. FR-25 now limits these near misses to tasks whose output can't reasonably include a comment (a config value, a format conversion, a rename, a one-off shell command) and says new logic such as a regex is left out.

**Fixed: the skill body could still claim chat replies.** FR-2 narrowed the description, but FR-3 said nothing about the body, and the body written for the first version says the rules cover "chat replies, code comments, commits and PR text". FR-3 now says the load-once rule names the same scope as the description, and AC-1 checks that the body doesn't claim chat replies.

## Notes

- **Every suite reruns.** The CLAUDE.md line, the `SKILL.md` description and body, and both case files all change, so the committed `scenario`, `rubric` and `plain-prose-behaviour` records go stale and `plain-prose-trigger` has none yet. The gates already require all four; no spec change is needed.
- **The rewrite case needs new wording.** The behaviour case `rewrite-chat-reply` has to be renamed and reworded as a reply the owner will send, to match FR-28. That's an implementation step.
- **The live CLAUDE.md still says chat replies are included.** This PR doesn't touch `~/.claude`, so the owner's live line and the repo's differ until a deploy or a manual edit. The spec's Context section says so.

## Spec changes made because of this review

| Where | Change | Why |
|---|---|---|
| FR-25 | Coding-only near misses limited to tasks that can't reasonably include a comment; a regex is left out | A careful coder may comment new logic, which is in scope |
| FR-3, AC-1 | The body's load-once rule names the description's scope and never claims chat replies | Otherwise the body contradicts the narrowed description |
