# Review note: the `session-handoff` spec

This note records the single adversarial review of `SPEC.md` for issue [#83](https://github.com/Towerism/claude-config/issues/83), run on 2026-10-21 by a fresh `general-purpose` agent using `adversarial-reviewer` and the owner's overlay for it. The approval mode is manual review with an adversarial pass, so the spec stays a draft until the owner approves it in their own words.

**Verdict:** BLOCK, from one critical finding, now fixed. Of six warnings, all six are fixed or answered in the spec. Of eight notes, seven are fixed and one is accepted. Because the review changed the spec, the owner sees every change below before approving.

## Critical

**The spec asked for something `skill-tester` can't give.** NFR-M1 required the package to pass `skill-tester` with no errors, but FR-1 and OS-1 forbid a `scripts/` directory and a README, and the validator reports both as errors (checked by running it on `linux-troubleshooting`). Fixed in NFR-M1: the only errors allowed are the missing `scripts/` directory and the missing README, which the owner accepts by approving this spec, and any warning about `SKILL.md` length is read against the 150-line cap.

## Warnings

**Fixed: a chat approval nobody had recorded was dropped or trusted wrongly (W-1).** FR-9 item 3 required every approval to name where it is recorded, but the owner often approves in chat before a label or status line exists. It also didn't say which version was approved, so a note could pass on an approval for a spec that has since changed. FR-9 item 3 now requires the approved version and lists an unrecorded approval as "unrecorded" with the owner's words. FR-14 item 4 tells the new session to ask the owner to confirm those, and to treat an approval as void if its subject changed. AC-4 covers it.

**Fixed: merge authorizations and the approval mode could carry over (W-2).** A new session reading "merge authorized" in a note could merge without asking. FR-9 item 7 now records authorizations and the approval mode as history only, and the new FR-14 item 5 tells the new session to ask again before merging, pushing or deploying and to settle the approval mode again. New AC-13.

**Fixed: moving facts to memory could lose them (W-3).** Memory is loaded per directory, so a new session in another worktree might not see it, and the old filter asked the model to recall what memory holds. FR-13 now has the note name each memory file written, and FR-9 item 7 includes a constraint whenever unsure.

**Fixed: several requirements had no test, and the dry run was weak (W-4).** New AC-13 to AC-15 cover authorizations, instruction text in tracker content, facts marked "not run" or unknown, the final check, several repos, an unwritable directory and same-second handoffs. AC-8 now uses a checklist written before the run instead of "what the owner would say". FR-8 names the timestamp to the second. The test plan names the negative cases and requires a table with one row per criterion. The limit of one run per prompt is stated and left to the owner (D-3).

**Fixed: the trigger was untested (W-5).** New AC-16 runs three handoff prompts and three that must not trigger, including `/compact` and an issue close-out. OS-3 makes those six prompts the first cases of the follow-up trigger suite.

**Fixed: the skills table omitted `senior-architect` (W-6).** The table now lists it for the one-row edit to `docs/architecture/skills.md`.

## Notes

- **Fixed (N-1):** FR-5 puts a 10-second timeout on each `gh` call, and NFR-P2 and NFR-R1 cover a timeout.
- **Fixed (N-2):** NFR-S1 resolves the state directory and falls back to the chat if it lies inside a git work tree.
- **Fixed (N-3):** FR-8 sets mode 0700 on the directory and 0600 on the file.
- **Fixed (N-4):** EC-3 covers a sandbox blocking or prompting for the write, and tells the owner to expect a prompt.
- **Fixed (N-5):** FR-7 asks for one line of purpose per uncommitted file.
- **Fixed (N-6):** FR-10 is now a best effort, tested by AC-5.
- **Fixed (N-7):** the skills table says skipping `tdd-guide` is a deliberate exception and why.
- **Accepted (N-8):** the 150-line cap may force cuts. If it does, EC-4 and EC-5 are the first edge cases to leave out of `SKILL.md`, since their behaviour follows from the other rules.

## Spec changes made because of this review

| Where | Change | Why |
|---|---|---|
| NFR-M1, test plan | `skill-tester` errors limited to the two expected ones | Critical |
| FR-9 item 3, FR-14 item 4, AC-4 | Approved version; "unrecorded" approvals; void if changed | W-1 |
| FR-9 item 7, FR-14 item 5, AC-13 | Authorizations and mode as history; ask again | W-2 |
| FR-13, FR-9 item 7 | Name memory files written; include when unsure | W-3 |
| FR-8, AC-8, AC-14, AC-15, test plan | Seconds in the name; checklist; new criteria; result table | W-4 |
| AC-16, OS-3 | Trigger prompts and follow-up suite | W-5 |
| Skills table | `senior-architect`; `tdd-guide` exception | W-6, N-7 |
| FR-5, FR-7, FR-8, FR-10, NFR-P2, NFR-R1, NFR-S1, EC-3 | Timeout, file purposes, modes, best-effort secrets, work-tree check, sandbox | N-1 to N-6 |
