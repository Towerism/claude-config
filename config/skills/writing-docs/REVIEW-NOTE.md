# Review note: the `writing-docs` spec

This note records the single adversarial review of `SPEC.md` for issue [#13](https://github.com/Towerism/claude-config/issues/13), run on 2026-10-17 with `engineering-skills:adversarial-reviewer` and the owner's overlay for it. The approval mode is manual review with an adversarial pass, so the spec stays a draft until the owner approves it in their own words.

**Verdict:** no critical finding. All four warnings and two of the five notes are fixed in the spec; the other three notes are listed. The owner sees every change below before approving, because the review changed the spec.

The spec also leaves two decisions to the owner, which the review didn't settle: the scope of `writing-docs` (D-1 in the spec's Context section) and the trigger suite's numbers (D-2, in FR-23).

## Warnings

**Fixed: the scope question didn't quote what the owner already approved.** The approved `plain-prose` spec for #12 says "#13 extends it to the full live line", and the live line gives `writing-docs` the same scope as `plain-prose`. This spec instead follows ADR-0010 and limits `writing-docs` to document files. That is a fair reading, but the owner has to see that it departs from the #12 handoff. The Context section now quotes that sentence beside the two sources it already named, so the owner decides D-1 with all three in view.

**Fixed: `doc_lint.py` would have written bytecode caches into the deployed `plain-prose` skill.** `prose_check.py` turns off Python's bytecode cache with a line inside its own module. A module that imports it has already compiled and cached it by the time that line runs, so `doc_lint.py` would leave a `__pycache__` directory in `plain-prose/scripts/`. In a deployed release worktree that shows up as drift. NFR-S2 now says `doc_lint.py` sets `sys.dont_write_bytecode` before the import, and AC-7 checks that a run leaves no cache in either skill.

**Fixed: removing a code fence around a model's reply was underspecified.** EC-11 said the fence "is removed", without saying which one. An implementation that stripped every fence would delete the document's own code blocks and change its lint count. EC-11 now removes only one outer fence labelled `markdown`, `md` or nothing, and only when it wraps the whole reply.

**Fixed: the template rule and its test disagreed.** FR-6 asked for each template's first line to name its kind, while AC-18 expected the lint to find nothing but `TODO:` markers. A first line that isn't a level-1 heading is itself a "no title" finding. FR-6 now asks for a `TODO:` title heading followed by a paragraph naming the kind, which satisfies both, and AC-1 and AC-18 match it.

## Notes

**Fixed: links inside inline code would have been checked.** FR-8 skipped front matter and fenced code but not inline code, so `` `[x](missing.md)` `` in a sentence about link syntax would be reported as a broken link. FR-8 now skips inline code too, for the link checks as well.

**Fixed: the cost of rerunning `plain-prose`'s suites was understated.** The spec said "about 90 model runs"; the trigger suite alone is 75, and the behaviour suite adds about 36. It now says "over 100".

**Listed: one lint finding can fail a behaviour case.** FR-27 fails a case when the skill arm has even one more `doc_lint.py` finding than the baseline, for example one code fence with no language, however well the reader did. That strictness is how the suite grades the structural steps. Each failing case gets one counted rerun, and every rerun goes in the PR.

**Listed: the skill arm's Bash pre-approval ends in a wildcard.** The rule allows `python3 -I <installed doc_lint.py> *`, as #12's suite does for `prose_check.py`. It relies on Claude Code matching compound commands part by part, so `…; rm -rf x` doesn't match. The tool list (`Skill,Read,Bash`), the private config directory and the stub `gh` still bound what a run can do.

**Listed: `doc_lint.py` reads Markdown files that links point to, anywhere on disk.** A cross-file anchor check reads the target's headings, so a document linking to `../../somewhere/notes.md#x` makes the lint read that file. It only reads headings, writes nothing and runs locally on the owner's own documents.

## Spec changes made because of this review

| Where | Change | Why |
|---|---|---|
| Context, D-1 | Adds: "The approved `plain-prose` spec (#12) followed the live line and said \"#13 extends it to the full live line\"." | The owner should decide the scope with the #12 handoff in view |
| NFR-S2, AC-7 | `doc_lint.py` "sets `sys.dont_write_bytecode` before importing `prose_check`", and a test checks no `__pycache__` is left | Importing `prose_check` would cache it inside the deployed `plain-prose` skill |
| EC-11 | Only "that outer fence" labelled `markdown`, `md` or nothing is removed, when it wraps the whole reply; "Fences inside the document are never touched." | Stripping every fence would change the document and its lint count |
| FR-6, AC-1, AC-18 | Templates "start with a level-1 heading whose text is a `TODO:` placeholder for the title, followed by a paragraph of at least 8 words saying which kind of document the template is for" | The old wording made every template fail its own lint check |
| FR-8 | "Front matter, fenced code blocks and inline code are skipped for all of them and for the link checks in FR-9" | Link syntax quoted in inline code isn't a link |
| Section C | "over 100 model runs" in place of "about 90" | The trigger suite alone is 75 runs |

# Second review: the revision for the owner's decisions D-1 and D-2

On 2026-10-17 the owner answered the two decisions the first draft left open: "I agree. Decision 1: is the full live line. Decision 2: your assumption is correct. I agree with the behavior suite you have planned. I approve once it reflects this." The spec was revised to match, and that revision is a new change, so it got its own single adversarial review with `engineering-skills:adversarial-reviewer`. This section records it. The first review above is unchanged.

**What the revision changed.** `writing-docs` now covers every text a person will read later, the same set as `plain-prose`: documents, commit messages, issue and PR text, review notes, code comments and docstrings, assertion and error messages, and CLI or UI text. Document files get all nine steps, a template and `doc_lint.py`; short texts get the steps that fit them. Five parts changed to match: the CLAUDE.md sentence (FR-15), its clause phrase (FR-16), the fourth scenario (FR-19), the trigger cases (FR-24, with a new `code` kind) and the ADR-0010 record (FR-32). FR-33 adds the decision to the plan as Q-18. The trigger numbers (FR-23) and the behaviour suite (FR-28) are the ones the owner accepted.

**Verdict:** no critical finding. Three warnings, each accepted with a reason below, and three notes, listed. The review changed nothing in the spec.

## Warnings, accepted

**`plain-prose`'s scenarios S-20 to S-22 may now see answers that name `writing-docs`.** Each passes only when the answer says it loads `plain-prose`. With the new sentence, an answer might name `writing-docs` alone, which loads `plain-prose` itself, and a strict judge could fail it. Accepted because the CLAUDE.md line still tells Claude to load `plain-prose` in its own sentence. AC-16 also requires S-20 to S-23 to keep passing, so a failure stops the work and goes to the owner.

**The trigger suite counts only `writing-docs` loads, while both descriptions now cover the same texts.** For a commit message or a test fix, Claude may load `plain-prose` alone, because its description is explicit about coding work. That is a miss under FR-14, so the 7 `prose` and `code` queries are the likeliest to fail the 13-of-15 bar. Accepted because the owner chose both the scope and the bar, and the spec already says what happens on a failure: one hand-tuned description, one run, then a stop to report (OS-7, OS-10).

**"The full live line" is read as the whole of `plain-prose`'s scope.** The live line names docs, issues, PRs, commits, code comments and review notes. The spec also includes assertion and error messages and CLI or UI text, following the owner's #12 rule for text a person reads later. Accepted because the live prototype the owner installed already covers "text a page or app shows (page copy, artifact pages, UI labels, user-facing errors, even inside code or data files)", so this reading matches what the owner runs today. The owner sees the reading in this note and in the PR.

## Notes, listed

**A long issue body, such as a spec stored on an issue, counts as a short text.** It gets the short-text steps and `plain-prose`'s check, not `doc_lint.py`. That matches ADR-0010, which gives the prose of specs on issues to `plain-prose`, but a long structured issue body would also benefit from the heading checks. A later change can widen the gate if that turns out to matter.

**No new scenario guards the old arm.** The first draft's commit-message scenario was the only new one without the `new-rule-only` tag. After D-1 it tests the new rule, so all four new scenarios are new-rule-only. S-01 to S-19 still guard the old arm.

**No new trust boundary.** The closest one is that the commit-message scenario might lead a model to act on a repo. Scenario runs use `--tools ""` in a temporary working directory, so they can't.

# Third review: a second hand-tuned description

The live trigger suite failed twice on 2026-10-17. The first run, on the approved description, had a recall of 0.533 because short texts loaded only `plain-prose`. The second, on the one tuned description OS-7 allowed, met the recall bar (13 of 15, no false trigger) but failed the rule that every delegated query triggers. Offered four ways forward, the owner chose "D. Allow a second tuned description, aimed at delegated and short-comment work". OS-7 now allows a second tuning with one run, and a stop if it fails. This section records the single adversarial review of that change.

**Verdict:** no critical finding. Two warnings, each accepted with a reason, and two notes. The review changed nothing in the spec.

## Warnings, accepted

**A tuning aimed at two named failures could fit those two queries instead of the work.** The misses were the delegated tutorial and the issue comment. Accepted because OS-7 still requires general terms and no eval query's wording, which the PR review checks, and because the run retests all 25 queries, not just the two.

**The description is already 1,020 characters of its 1,024.** Adding words about work done by a subagent means cutting others, which could drop something FR-2 requires. Accepted because the package test fails if any required element goes missing, so a cut that breaks FR-2 can't land unnoticed.

## Notes, listed

**This change doesn't address the failed behaviour suite.** `writing-docs-behaviour` failed one case of six on its counted rerun, and the spec has no further path for it. That needs its own decision from the owner.

**No new trust boundary.** The change only allows one more edit of a description and one more model run, under the same isolation as before.

# Fourth review: three tuned descriptions in total

While the third review's change waited for approval, the owner added "3 total tuned descriptions are allowed". OS-7 now allows up to three hand tunings, each with one run, and a stop after a failing third run. This change got its own adversarial review.

**Verdict:** no critical finding, one warning accepted, and the review changed nothing.

**Warning, accepted: more tries make a lucky pass more likely.** With up to three tuned runs, a description could pass by chance on a bar it only just meets. Accepted because each tuning is a real change to the description, not a bare rerun of the same one, every run goes in the PR so the owner sees the whole history, and the owner set the limit knowing the first tuned run sat exactly on the recall bar.

# Fifth review: three counted runs of the behaviour suite

The first live run of `writing-docs-behaviour` failed one case of six on its counted rerun: the reader flagged two sentences the skill arm kept word for word from the draft, while both arms answered every question right, kept every fact and had no lint findings. The owner answered "allow the same number of counted runs for the behavior suite". FR-27 now allows up to three counted runs of the suite in total, each keeping its one counted rerun per case, with every run in the PR and a stop after a failing third run. This change got its own adversarial review.

**Verdict:** no critical finding, one warning accepted, one note, and the review changed nothing.

**Warning, accepted: unlike a tuned description, a rerun changes nothing, so a pass can be chance.** The same skill body runs again, and a single reader's AI-sounding flag decides the failing case. Accepted because the owner chose it knowing that, every run goes in the PR, and the failing flag was on text the skill arm kept unchanged from the draft rather than text it wrote.

**Note: the interpretation is recorded.** "The same number" is read as three runs in total, matching OS-7's three tuned descriptions, with the failed run on 2026-10-17 counting as the first.

# Sixth review: accepting the behaviour suite's failure instead

Before approving the fifth review's change, the owner replaced it: "in that case for the behavior suite, accept the failure", in answer to that review's warning that a bare rerun could pass by chance. FR-27 is back as it was, with one counted rerun per case and no extra runs, and AC-16 records the failed run as accepted by the owner, with its record committed as it is. This change got its own adversarial review.

**Verdict:** no critical finding, one warning accepted, and the review changed nothing.

**Warning, accepted: `eval_records.py verify` will fail on this branch.** The verifier requires every required record to show a passing run and has no way to mark an accepted failure, so it reports `writing-docs-behaviour` and exits 1. Once CI runs it (#10), a PR in this state would fail that job. Accepted because the owner made the decision knowing the result, AC-16 now says exactly which record fails and why, and the failure is limited to this PR: later PRs need the suite only when they change `writing-docs` again, and then they need a fresh run anyway.

# Seventh review: a behaviour suite that doesn't depend on chance

Instead of accepting the failure, the owner asked for the suite itself to be fixed: "either the test data or the approach needs fixing so that tests aren't flaky. senior-qa skill would have warned you about this had you referenced its reference docs". The owner was right that the `senior-qa` guides were skipped when the tests were written. Read now, their guide on flaky tests names the causes this suite had: non-deterministic data, an external dependency deciding the result, and retries that hide flakiness instead of removing it.

The redesign, in FR-25 to FR-28, AC-13, AC-14, AC-16 and EC-13:

- each case lists `mustContain` patterns for its commands, error strings and numbers, checked in code, in place of the judge's lost-facts verdict;
- each text gets three reader and judge passes, and an answer counts as right only when two of three judges agree;
- the suite decides on totals, with no reruns;
- every rewrite task now says what must survive and what may be cut, because all three rewrite drafts held text outside their fact lists (history, opinions, promotional wording) under a bare "Keep every fact".

**Verdict:** one critical finding, fixed in the spec, two warnings accepted and one note. Because the review changed the spec, the owner sees each change below before approving.

## Critical, fixed

**The draft still compared the two arms on the AI-sounding flag, which made the suite a coin toss.** Both arms run `plain-prose`, the skill that owns that criterion. Where `writing-docs` adds nothing to it, "the skill arm is flagged no more often than the baseline" fails about as often as it passes, even with votes and totals. The same holds for "at least as many right answers as the baseline" when both arms read equally well. That contradicts the owner's requirement that the tests not be flaky. FR-27 now says: "summed over the cases, the skill arm gets at least 90% of all questions right", a fixed bar instead of a comparison, and "Whether a text reads as AI-written is recorded for both arms as a diagnostic and never decides this suite". The lint comparison stays, because only the skill arm has the templates and the lint, so the arms really differ there. AC-13, FR-32 and the test plan changed to match.

## Warnings, accepted

**A `mustContain` pattern can reject a faithful paraphrase.** "Three times" could become "3x". Accepted because FR-28 limits the patterns to commands, error strings and numbers, which a faithful text copies as they are, and EC-13 makes the data test reject a pattern that doesn't match the case's own facts or draft.

**A fixed 90% bar could pass a skill that reads worse than the baseline.** If both arms score 95%, the bar can't tell them apart. Accepted because reading quality is what `plain-prose` already gates, the diagnostic figures for both arms stay in the record and the PR, and the bar is high enough to catch a skill that hurts comprehension.

## Note, listed

**A suite run costs about twice as much.** Three reader and judge passes per text mean about 84 model runs per suite run instead of about 36, in exchange for no reruns.
