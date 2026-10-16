# Adversarial review note: claude-md-v2/SPEC.md (one pass, 2026-10-15)

`engineering-skills:adversarial-reviewer` ran once in a fresh-context, read-only subagent. Its verdict was **BLOCK**: 2 CRITICAL, 6 WARNING and 5 NOTE findings. Under the one-pass rule it was not re-run after the fixes. Validator score: 98/100 both before and after the fixes. The one remaining warning, "no HTTP method + path", is accepted because this work has no HTTP API. The changes are in `review-changes.diff`, against `SPEC.pre-review.md`.

| ID | Sev | Finding | Handling |
|---|---|---|---|
| C1 | CRITICAL | AC-6's old-vs-new eval used subagents, and subagents always inherit the live `~/.claude/CLAUDE.md`, so the "new only" arm was really old + new. | **Fixed.** Each arm is now an isolated headless `claude -p` run with its own config dir, proven by a canary probe. A flake-hazard entry was added. |
| C2 | CRITICAL | `restore.sh` restored files byte-for-byte, silently wiping edits made during the two-week trial (data loss). | **Fixed.** FR-23 restore is non-destructive: it snapshots first, removes only its own hook entries, and refuses to overwrite an edited CLAUDE.md. AC-8 tests this. |
| W1 | WARNING | The reload-before-tests/YAML rules, the CI trigger scope, the gitlab template and "review checks" lived only in overlays that load at the wrong time. | **Fixed** in FR-4.4 and the tdd-guide/ci-cd overlays. |
| W2 | WARNING | The caller's review-handling rules lived in the reviewer overlay, which loads in the reviewer subagent, not the caller. | **Fixed.** Handling, one-pass and the PR-description note moved to CLAUDE.md FR-8. The overlay is reviewer-side only, and a delegated-review scenario was added to AC-6. |
| W3 | WARNING | AC-7 could pass without the hook, didn't test the subagent case, and copied credentials. | **Fixed.** Uses a canary, a negative control and a no-Read assertion, plus a subagent run and `--settings` against the real config. |
| W4 | WARNING | Five clauses were missing: "approved or presented", "present with review note", PR-description note, coordinator applies the manual-review label, fork validates its spec. | **Fixed** in FR-7, FR-8 and FR-11. |
| W5 | WARNING | D-1 presumed your answer on the unconfirmed W-fixes. | **Fixed.** Each item is tagged `[D-1:…]`, and FR-22 ships an item only on your explicit yes (silence means it's left out). |
| W6 | WARNING | The install didn't detect changes since the baseline, and renaming the old skill directory may not disable it. | **Fixed.** A sha256 check against the baseline, the old skill moved out of `skills/`, an AC check, and D-2 made blocking. |
| N1 | NOTE | Claude can't run `/doctor prompt-audit`. | Fixed: it's now a user-run review check. |
| N2 | NOTE | Verify-before-done was missing; ALL-CAPS was allowed on a rule never violated. | Fixed: verify line added to FR-8; emphasis limited to 2 lines (merge auth, STOP); Stop-hook gate recorded as OS-7. |
| N3 | NOTE | Routing rows for skills that auto-trigger contradict "non-obvious only". | Fixed: each row needs a cited reason, and auto-triggering rows are dropped. |
| N4 | NOTE | Overlays hard-coded the Nuxt/Vitest stack. | Fixed: phrased as "when the repo uses…". |
| N5 | NOTE | Atomic commits and the merge method were missing; "highest version" may not be the active version. | Fixed: atomic commits, merge commit or rebase with every commit conventional; paths resolved from `installed_plugins.json` `installPath` (verified to exist). |

---

# Second adversarial pass: spec changed by the user's decisions D-1/D-2/D-3 (one pass, 2026-10-15)

Run once in a fresh-context, read-only subagent, scoped to `decisions-changes.diff`. Verdict: **BLOCK** (2 CRITICAL, 3 WARNING, 3 NOTE). It was not re-run after the fixes. The fixes are in `review2-changes.diff`, against `SPEC.pre-review2.md`. Validator: 96/100. Two warnings are accepted: "no HTTP endpoint" (there is none), and "FR-3/5/10 without ACs" (they're withdrawn and kept greppable on purpose).

| ID | Sev | Finding | Handling |
|---|---|---|---|
| C1 | CRITICAL | Parts of the voided W5 survived: the fork merge-relay sentence and "iterate until green" (contradicts D-1). The original fork-routing wording was also lost. | **Fixed.** FR-8 now uses the original wording, "Merge on your own only when the user has authorized merging", and "iterate until green" is removed. FR-11 restores "Route each approval, change request or question to the fork that owns that issue". |
| C2 | CRITICAL | AC-9 could pass without the hook (unallowed Bash is denied in `-p` anyway), and its `gh pr view` half couldn't pass. | **Fixed.** Both commands are pre-allowed with `--allowedTools` and `--permission-prompts none`. The test asserts on `permission_denials`, runs a control without the hook, and moves the reason string to unit tests. FR-18a also checks `ask` under the auto and bypass permission modes. |
| W1 | WARNING | The `if: "Bash(gh pr merge *)"` matcher missed `gh -R … pr merge`, compound commands, `bash -c` and `gh api …/merge`. Nothing stopped a workaround after a refusal. | **Fixed.** FR-24 is now the `merge_confirm.py` script with a regex, unit-tested for each form. FR-8 adds "don't merge another way; report it as waiting". |
| W2 | WARNING | FR-24 wasn't wired into the design table, the API contracts, install, restore or AC-8. | **Fixed** in all five. |
| W3 | WARNING | EC-11 promised a "reports waiting" behaviour that nothing specifies. | **Fixed.** EC-11 points to FR-8's new line. |
| N1 | NOTE | The D-1 deletions left broken sentences, and the withdrawn IDs weren't greppable. | Fixed. |
| N2 | NOTE | The emphasis wording didn't match between FR-16 and AC-4. | Fixed: exactly one line, and it must be the merge rule. |
| N3 | NOTE | Ordering problems (EC-10, FR-24's section) and a stale Status line. | Fixed: a new section, E (Merge guard), and EC order restored. |

---

# Third adversarial pass: approval-mode change D-4 (one pass, 2026-10-15)

Run once in a fresh-context, read-only subagent, scoped to `modes-changes.diff`. Verdict: **BLOCK** (1 CRITICAL, 3 WARNING, 2 NOTE). It was not re-run after the fixes. The fixes are in `review3-changes.diff`. Validator: 96/100, with the same two accepted warnings.

| ID | Sev | Finding | Handling |
|---|---|---|---|
| C1 | CRITICAL | AC-6 couldn't pass: headless `-p` runs hit the "can't ask, use the default" fallback, "pre-selected" can't be seen in a transcript, the old-vs-new rule now requires failures, and FR-7 wasn't in the new-rules list. | **Fixed.** The fallback now applies only to jobs with no user turn, not `-p`. AC-6 exempts `superseded:D-4` scenarios, adds FR-7, and defines must-ask as "asks, lists all 3 modes, manual first and marked default, no spec text". |
| W1 | WARNING | D-4 silently dropped "manual review wins", "no review skips the reviewer *on specs* only", and "lasts for the session". | **Fixed.** The first two are kept in FR-7/EC-7. "Lasts for the session" is recorded as superseded by D-4. |
| W2 | WARNING | A fork would re-ask, or fall back to manual, because the mode came from the coordinator rather than your prompt. | **Fixed.** FR-11: the fork treats the relayed mode (with your words quoted) as stated by you, and never re-asks. |
| W3 | WARNING | In manual mode, the go-ahead on a raise could be mistaken for approval. | **Fixed.** FR-8: in manual mode, raising is folded into presenting the spec, and only explicit approval counts. |
| N1 | NOTE | Skill specs have no issue to post notes on. | Fixed: `REVIEW-NOTE.md` beside `SPEC.md`. |
| N2 | NOTE | The spec-driven-workflow overlay didn't mention the mode question. | Fixed: "confirm the approval mode is settled before drafting". |


---

# Adversarial pass on the Q-1 (D-5) install change and Q-2 confirmation (one pass, 2026-10-15)

Run once in a fresh-context, read-only subagent covering both specs' diffs. Verdict: **CONCERNS** (0 CRITICAL, 4 WARNING, 6 NOTE), all fixed. It was not re-run.

| ID | Sev | Finding | Handling |
|---|---|---|---|
| W-1 | WARNING | The abort checks sat in step 4, after CLAUDE.md and the hooks were already written, which contradicts "abort before any change". | Fixed: all checks moved to a step-1 preflight. |
| W-2 | WARNING | The step-4 copy and move weren't atomic, so both skills could end up installed. | Fixed: move first, then copy; a failed copy rolls back and exits non-zero. |
| W-3 | WARNING | restore compared against a scratch staging copy, and the skill folder held stray files. | Fixed: a `bundle-manifest.sha256`, and restore acts only on manifest paths. The stray files moved to `claude-md-v2/lifecycle-history/`. |
| W-4 | WARNING | The skill-list check relied on the model's prose. | Fixed: it reads the stream-json `system`/`init` event and checks set membership. |
| N-1…N-6 | NOTE | Deletion wording; restore destination exists; stale lifecycle lines 7 and 393; Q-2 phrased as a question; AC-8 wording order; decision numbering. | Fixed: deleted only on your explicit ask, restore stops if the destination exists, lines updated, Q-2 stated as a decision, the AC reordered, and Q-1 recorded as D-5. |


---

# Adversarial pass on the post-implementation amendments (both specs, one pass, 2026-10-15)

Run once in a fresh-context, read-only subagent over `impl-changes.diff` (v2 FR-23) and `lifecycle-history/impl-changes.diff`, checked against the implementation. Verdict: **BLOCK** (1 CRITICAL, 3 WARNING, 2 NOTE), all fixed. It was not re-run.

| ID | Sev | Finding | Handling |
|---|---|---|---|
| C1 | CRITICAL | The trim moved the existing-skill spec rule and the REVIEW-NOTE location into an overlay only, against v2 FR-14, FR-7 and FR-17. | Fixed in the implementation: the line is restored in CLAUDE.md under Skills and removed from the overlay. New clause rows C-77 and C-78 guard it. |
| W1 | WARNING | Routing for a bare "fix the export bug" had no real gate. | Fixed: lifecycle AC-3 adds case 23, a v2-routed behaviour eval with recorded fake `gh`, and `run_eval.py` now exits 1 on any regression or new-rule failure. Case 23 first failed on Sonnet, so the CLAUDE.md "Start" line was sharpened within FR-4; it now passes on both models. |
| W2 | WARNING | A malformed `settings.json` left a half-done install. | Fixed: the FR-23 preflight aborts on a malformed or symlinked `settings.json`, and any later error exits non-zero naming `restore.sh`. Tests added. |
| W3 | WARNING | Install could overwrite unknown files in the lifecycle folder. | Fixed: the FR-23 preflight aborts if that folder has anything besides `SPEC.md` and `REVIEW-NOTE.md`. Tests added. |
| N1 | NOTE | Stale cross-spec line; manifest file names not given. | Fixed. |
| N2 | NOTE | "Byte-for-byte in meaning" wording; symlinked settings. | Fixed: now "semantically equal, formatting may change", and symlinks abort. |

Final results:
- pytest: v2 93 passed, lifecycle 81 passed.
- CLAUDE.md: 1,400 tokens, 54 lines.
- AC-6: new arm 19/19, no regressions.
- AC-4 rubric: 78, then 84 on the allowed re-run (pass; the judge varies between 78 and 86).
- Case 23: passes on both models.
