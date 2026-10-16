# Adversarial review note: github-issue-lifecycle SPEC.md (one pass, 2026-10-15)

Approval mode: manual review with adversarial pass, chosen by the user.

The review used `engineering-skills:adversarial-reviewer` and ran **once**, with three personas: Saboteur, New Hire and Security Auditor. It ran **inline in the spec-writing fork, not in a fresh-context subagent**, because forks may not spawn subagents. That makes it a same-context self-review, weaker than the fresh-context passes used on the v2 spec.

**Verdict: BLOCK** (1 CRITICAL, 7 WARNING, 2 NOTE). Per the one-pass rule, it was not re-run after the fixes. The fixes are in `review-changes.diff`, against `SPEC.pre-review.md`.

**Validator:** 96/100. Two warnings are accepted:
- "No HTTP method + path": the skill drives `gh`, not HTTP.
- "FR-24 without AC": a false positive from the reference to v2 FR-24, which is defined in the v2 spec, not here.

| ID | Sev | Persona(s) | Finding | Handling |
|---|---|---|---|---|
| C1 | CRITICAL | Saboteur | `diff-check` flagged any added `SPEC.md` or `*spec*.md` under `docs/`. That rejects the `SPEC.md` that v2 FR-14 requires beside a `SKILL.md` kept in a repo (contradicts the approved spec), and it false-matches `docs/inspection.md`. | **Fixed.** FR-16 has exact patterns: `.md` only, a `specs/` segment, or basename `spec-*.md`, `*-spec.md` or `*.spec.md`. A `SPEC.md` next to a `SKILL.md` is exempt. AC-11 adds the skill, `inspection.md` and `*.spec.ts` cases. |
| W1 | WARNING | Saboteur | Staging the skill (FR-3) needs v2 FR-23 to copy the bundle at install, which the approved v2 doesn't say. The v2 checker would also fail to resolve the name before install. | **Fixed in this spec; v2 change raised as Q-1.** The checker runs with the staging skill root. |
| W2 | WARNING | Saboteur | The behaviour and trigger evals would run with the old `issue-spec-driven-workflow` installed and competing, which confounds the results. `--plugin-dir` doesn't fit a bare skill. | **Fixed.** Isolated temp `CLAUDE_CONFIG_DIR` without the old skill, credentials removed in a trap, canary skill-list probe. |
| W3 | WARNING | New Hire | The behaviour-eval harness (fake `gh`/`git`, grading) was assumed but not specified anywhere. | **Fixed.** `evals/run_behaviour_evals.py` added to FR-2 as a dev-only tool. |
| W4 | WARNING | Security Auditor, New Hire | `approval: *` and `approved` labels were trusted no matter who added them. Any collaborator or other account could switch on "no review" or unlock implementation. The order of precedence with an existing label was also unclear. | **Fixed.** FR-9 has a 4-step precedence. Labels are trusted only when the label event's actor is the authenticated user. FR-12, EC-16 and the new AC-18 cover it. (The 2-persona promotion would make this CRITICAL, but it isn't CRITICAL on its own because the repos are solo-maintained, so it stays a WARNING.) |
| W5 | WARNING | Security Auditor | Issue and PR text from strangers (public repos) goes into the model's context, with no rule against following instructions found in it. | **Fixed.** New FR-23 treats that text as data, plus AC-17 (an injected `curl \| sh` and "add approved"). |
| W6 | WARNING | Security Auditor | Temp-file locations were unspecified. The old skill's fixed `/tmp/issue-42-body.md` lets parallel jobs on `#42` in two repos cross-publish bodies. | **Fixed.** FR-10 requires a per-run `mktemp -d` under `$CLAUDE_JOB_DIR/tmp`. |
| W7 | WARNING | Saboteur | "Every commit on the branch must pass" breaks on merge-from-main commits, and could push Claude into rewriting pushed history unasked. | **Fixed.** FR-15: update by rebase, check only commits Claude adds, ask before rewriting pushed commits, `--force-with-lease` on the feature branch only. |
| W8 | WARNING | New Hire | In manual mode, the coordinator's brief said "Claude never sets `approved`", but v2 FR-7 has the coordinator relay approval. The spec was ambiguous. | **Fixed (wording)** in FR-11: Claude applies the label only to record your explicit words, with a quoting comment. **Raised as Q-2** for you to choose. |
| N1 | NOTE | New Hire | AC-13 needs ship-gate to produce a verdict inside the harness. | Fixed: the harness supplies a fixture repo. |
| N2 | NOTE | Saboteur | The spec-ID grep can false-match real text such as "AC-130" or "OS-2" in prose. | Listed, not fixed: rare, and the remedy is rewording the line. Revisit if it fires falsely in practice. |


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
