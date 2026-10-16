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

# Second review: the scope the owner set after the first live run (2026-10-16)

This section records the single adversarial review of the change to the approved spec. The first live `plain-prose-trigger` run scored 0.7: six of its ten near misses loaded the skill, among them a failing-test fix, a regex and a config bump. The owner then set the scope in three statements the same day:

1. "chat replies are not in scope. but since code comments are, coding-only tasks must remain scope to that extent"
2. "The near misses are fine. Fixing a failing test can sometimes mean changing assertion messages. Those absolutely are in scope since they are human facing. That's another way to look at it. Anything that is (future--any amount of time in the future) human facing is in scope."
3. "This includes text that is rendered by a CLI or a web page eventually"

An intermediate draft (commit 04def1a) followed only the first statement and treated those coding tasks as near misses. Its review was posted on #12, but the second statement replaced that reading before the owner approved anything. That review is superseded, and this one covers the current text.

**What changed:** in scope is now any text a person will read later, including what a CLI or web page will eventually show; out of scope are the chat reply itself and text written for Claude. That changes the description (FR-2), the body's load-once rule (FR-3), the CLAUDE.md line and its clause phrase (FR-15, FR-16), the `config/SPEC.md` wording (FR-20), and the trigger cases (FR-25), which now treat coding tasks as triggers and use text written for Claude and chat-only questions as near misses, with a `kind` per case that AC-17 checks. FR-28 calls the third rewrite case a reply drafted for the owner to send. New FR-33 amends ADR-0010 and the plan's Q-9.

**Verdict:** no critical finding. One warning, fixed; four notes.

## Warning

**Fixed: ADR-0010 would still have said the skill must not trigger on code.** FR-33 first named only ADR-0010's opening summary and scope list. Its eval section also says `plain-prose` "must trigger on prose tasks, and not on code or CLAUDE.md edits", which the owner's second statement reverses. FR-33 now names that section too.

## Notes

- **Every suite reruns.** The CLAUDE.md line, the description, the body and both case files change, so the `scenario`, `rubric` and `plain-prose-behaviour` records go stale, and `plain-prose-trigger` has none yet. The gates already require all four.
- **Implementation steps follow from the spec:** the trigger cases get new near misses and `kind` fields, the case `rewrite-chat-reply` is renamed and reworded as a reply the owner will send, the package test checks the new description and body wording, and ADR-0010, the plan's Q-9, `config/SPEC.md` and the CLAUDE.md line are edited.
- **The live CLAUDE.md and the live prototype skill still say chat replies are included.** This PR doesn't touch `~/.claude`, so they stay different from the repo until a deploy (#7 and #8, held behind #6) or a manual edit.
- **The behaviour-case fix is accepted.** The owner said "the behaviour-case fix in 22b959d is fine" about the reworded `rewrite-review-note` draft.

## Spec change made because of this review

| Where | Change | Why |
|---|---|---|
| FR-33 | Also amends ADR-0010's eval section | It says the skill must not trigger on code, which the owner's second statement reverses |

# Third review: fifteen should-trigger cases (2026-10-16)

This section records the single adversarial review of the owner's next change request on the draft: "increase triggering cases to 15". The same day, the owner also confirmed that the chat reply itself stays out of scope: "yes, keep chat replies out of scope". That confirmation is now quoted in the spec's Status and Context sections and in FR-33, alongside the three earlier statements; it changes nothing else.

**What changed:**

- FR-25 now asks for 25 queries: 15 that should trigger and 10 near misses. The minimum mix stays: 2 delegated, at least 3 coding tasks, at least 1 CLI or web page edit. The 5 extra go to coding work and prose, so the planned split is 8 prose texts (2 of them delegated), 5 coding tasks and 2 CLI or web page edits. AC-17 checks the new counts.
- FR-24 replaces the single "90% of queries" rule with one rule per class. At least 85% of the should-trigger queries must trigger (13 of 15), no near miss may trigger, and every delegated query must trigger. Overall accuracy stays in the record as information only. With 15 positives and 10 negatives, one accuracy figure would weigh the classes unevenly. And because any false trigger already fails the suite, the old rule only ever limited missed triggers, so it is now stated that way. AC-13 and the API contract follow.

**Verdict:** no critical finding and no warning. Four notes.

## Notes

- **The per-class rule goes beyond the literal request, so the owner should look at it.** The old rule allowed 2 missed triggers out of 10 (80%); the new one allows 2 out of 15 (about 87%). The miss allowance is unchanged, and the rate is stricter. No ADR sets a threshold; the 90% came only from this spec and its runner.
- **The runner has to change.** `eval/skill_evals.py` still applies the 90% accuracy rule, its docstring says so, and its tests check it. They change with the trigger cases after approval.
- **Each trigger run costs more.** 25 queries at 3 runs each is 75 model runs, up from 60.
- **The near-miss set stays at 10.** Nothing breaks: 5 text-for-Claude cases and 3 chat-only questions leave 2 free.
