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
