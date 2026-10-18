# Spec: the `writing-docs` skill, its chaining overlays and its CLAUDE.md sentence

**Author:** Claude (for the owner, Towerism)
**Date:** 2026-10-17
**Status:** Draft again (2026-10-17), waiting for the owner's approval of two changes: up to three hand-tuned descriptions in total for the trigger suite (OS-7), and up to three counted runs of the behaviour suite (FR-27). The owner first approved this spec on 2026-10-17 after answering decisions D-1 and D-2 ("I agree. Decision 1: is the full live line. Decision 2: your assumption is correct. I agree with the behavior suite you have planned. I approve once it reflects this."). The live trigger suite then failed twice, the second time on its one tuned description, and the owner chose the option "D. Allow a second tuned description, aimed at delegated and short-comment work" with the answer "D", then added "3 total tuned descriptions are allowed". The behaviour suite had also failed one case of six on its counted rerun, and the owner answered "allow the same number of counted runs for the behavior suite". Each change has its own adversarial review (see `REVIEW-NOTE.md`). Approval mode: manual review with an adversarial pass, chosen by the owner for this session's issues ("work on the implementation plan. manual review + adversarial. work on each one sequentially."). Only the owner's explicit words approve it.
**Issue:** [#13](https://github.com/Towerism/claude-config/issues/13)
**Reviewers:** `engineering-skills:adversarial-reviewer` (one pass, see `REVIEW-NOTE.md`), then the owner
**Decided by:** [ADR-0010](../../../docs/adr/0010-prose-for-people.md) (the skill, its nine steps, `doc_lint.py`, the overlays, the evals), [ADR-0005](../../../docs/adr/0005-verification-and-ci.md) (eval gates and records), [ADR-0002](../../../docs/adr/0002-repo-layout.md) (where specs live)

---

## Context

The owner wants documentation, and every other text a person reads later, to be usable by a reader with no context, usually the owner months later ([ADR-0010](../../../docs/adr/0010-prose-for-people.md)). ADR-0010 splits the work between two skills. `plain-prose`, built in #12, holds the clarity and anti-AI-tell rules for any text a person will read later. `writing-docs` covers the same texts and adds reader-first steps on top: naming the reader and their task, putting the answer first, being concrete, and giving the structure meaning. Document files get the full set, which also includes choosing one kind of document, a template, and a lint for headings and links. The skill loads `plain-prose` first and ends by applying it.

A prototype has been installed in `~/.claude/skills/writing-docs/` since 2026-10-16. This issue builds the real skill in `config/skills/writing-docs/` and wires it in. The prototype stays live until a deploy or adoption replaces it (#7 and #8, both waiting on the held #6).

Like #12, this spec covers more than the skill directory: two overlays, the `writing-docs` sentence in the global CLAUDE.md prose line, scenarios, two eval suites and a new eval runner module. One spec means one approval for one PR. Section F lists every change outside the skill directory, and the same PR updates `config/SPEC.md`, as [ADR-0002](../../../docs/adr/0002-repo-layout.md) requires for coordination changes.

### The scope (owner decision D-1, settled 2026-10-17)

Two sources disagreed about what `writing-docs` covers.

- [ADR-0010](../../../docs/adr/0010-prose-for-people.md), accepted 2026-10-16, and the text of #13 limited it to **documentation**: "Markdown documents for people: READMEs, everything under `docs/`, ADRs, architecture docs, runbooks, how-to guides, tutorials, setup guides and changelog prose."
- The owner's live `~/.claude/CLAUDE.md`, written later the same day, says to load `writing-docs` "once before the first doc, issue, PR, commit, code comment or review note". The live prototype's description is just as broad, and includes text a page or app shows. The approved `plain-prose` spec (#12) followed the live line and said "#13 extends it to the full live line".

The first draft of this spec followed ADR-0010 and asked the owner to choose. The owner chose the live line: "Decision 1: is the full live line."

**So `writing-docs` covers every text a person will read later, the same set as `plain-prose` under the rule the owner set in #12:** documents, commit messages, issue and PR text, review notes, code comments and docstrings, assertion, error and log messages, and text a CLI or web page will show. The chat reply itself and text written for Claude stay out, as they do for `plain-prose`.

The two skills chain instead of competing. `writing-docs` loads `plain-prose` before drafting, so a session that loads `writing-docs` has both. What `writing-docs` adds depends on the text:

- A **document file** gets all nine steps, a template and `doc_lint.py`. A document file is one whose main job is to be read later by a person: a README, a page under `docs/`, an architecture doc, an ADR, a runbook, a how-to guide, a tutorial, a setup guide, a CONTRIBUTING file, a changelog or release notes. Editing or rewriting one counts too.
- A **short text**, such as a commit message, a PR description, an issue comment, a review note, a code comment or an error message, gets the steps that make sense at that length: name the reader and what they need, put the answer first, be concrete, define terms, and link to the source of truth. Choosing a kind of document, templates and `doc_lint.py` don't apply; `plain-prose`'s own procedure is its check.

### How `writing-docs` gets used

| Path | Mechanism |
|---|---|
| A task will write a document or any other text a person reads later | The skill's description (FR-2), plus the `writing-docs` sentence of the CLAUDE.md prose line (FR-15) |
| `senior-architect` writes an architecture doc or ADR | Its overlay says to load `writing-docs` before writing the file (FR-17) |
| `runbook-generator` writes a runbook | Its overlay says the same (FR-18) |
| `writing-docs` itself | Its body loads `plain-prose` first if it isn't in context, so the prose rules are present while drafting (FR-3) |

ADR-0010 records why the last row matters: on 2026-10-16, three subagents told to load both skills loaded `writing-docs` and not `plain-prose`. The behaviour eval checks that the skill arm loads both (FR-27).

### Skills used to build this

| Skill | Use |
|---|---|
| `github-issue-lifecycle` | Claiming #13, labels, branch and PR |
| `spec-driven-workflow` | This spec, validated with `spec_validator.py --strict` |
| `senior-qa` (all three guides read) | The test plan below |
| `anthropic-skills:skill-creator` | Skill structure: a short `SKILL.md`, detail in `references/`, deterministic checks in `scripts/`, a pushy description, trigger queries with realistic near misses |
| `tdd-guide` | Red-green-refactor for `doc_lint.py` and the runners |
| `skill-tester` | Validating the finished skill package |
| `adversarial-reviewer` | One pass on this spec, one on the PR diff |
| `writing-docs` (prototype), `plain-prose` | Every human-facing text, checked with the prototype's `doc_lint.py` |

`skill-security-auditor` isn't needed: nothing in this skill comes from a third party.

---

## Functional Requirements

### A. The skill package

- FR-1: The skill MUST live in `config/skills/writing-docs/` with this layout: `SKILL.md`, `SPEC.md`, `REVIEW-NOTE.md`, `scripts/doc_lint.py`, `references/reader-first.md`, `references/templates/{readme,how-to,tutorial,reference,explanation,adr,changelog}.md`, `evals/{trigger-evals.json,behaviour-evals.json}` and `tests/`.
- FR-2: `SKILL.md` MUST have frontmatter with `name: writing-docs` and a `description` that:
  - says what the skill does: guides writing and revising any text a person will read later, so a reader with no context can act on it;
  - names the document types from the Context section (at least README, ADR, runbook, how-to guide and changelog), and says it covers both new documents and edits to existing ones;
  - names the short texts it also covers: commit messages, issue and PR text, review notes, code comments and docstrings, assertion and error messages, and CLI or UI text;
  - says coding work that writes or changes such text needs it too, in the general terms `plain-prose`'s description uses (tests, validation or error handling, logic worth a comment, CLI or UI strings);
  - says to use it whenever the user asks for documentation of a system, process or decision, even without the word "doc";
  - says it loads `plain-prose` first;
  - says to load it once per session, before the first such text, and not again while its instructions are in context;
  - excludes the chat reply itself, code itself, identifiers, machine-read formats, and files written for Claude (`CLAUDE.md`, `SKILL.md`, overlays, memory, subagent prompts);
  - stays at or under 1,024 characters, the description limit in the Agent Skills format.
- FR-3: The `SKILL.md` body MUST hold these parts, in this order:
  1. the goal: a document succeeds when a reader with no context can use it to do or decide something;
  2. the load-once rule, naming the same scope as the description;
  3. load `plain-prose` first, before reading sources or drafting, unless its `Base directory for this skill:` block is already in context, because its rules must govern the drafting, not be applied afterwards;
  4. the nine steps from [ADR-0010](../../../docs/adr/0010-prose-for-people.md)'s "Skill 2" section, each in one or two sentences, with `references/reader-first.md` named for the detail;
  5. short texts: for a commit message, issue or PR text, a review note, a code comment or docstring, or a message or string in code, apply the steps that fit that length (name the reader and what they need, answer first, be concrete, define terms, link to the source of truth), and skip choosing a kind, the templates and `doc_lint.py`; `plain-prose`'s procedure is the check;
  6. templates: for a document file, use `references/templates/<type>.md` for the document's kind, unless another loaded skill (such as `senior-architect` or `runbook-generator`) supplies its own template, which then wins on structure while these steps still apply;
  7. the gate: no version of a document file goes to a reviewer, a commit, a PR or the user until `doc_lint.py` has run on that version, and again after every revision;
  8. the report: say which documents were checked and list any findings left as they are, with a reason.
- FR-4: The body MUST give the lint command as `python3 -I <this skill's base directory>/scripts/doc_lint.py <file.md or -> [--json]`, never through a shell variable.
- FR-5: `references/reader-first.md` MUST expand each of the nine steps with a short good and bad example, and say how to run the fresh-reader check from step 8 (a subagent that sees only the document and answers "can I now do X?"). A short section MUST show how the steps apply to a commit message, a PR description and a code comment, with one before-and-after example each. It MUST stay under 300 lines, or carry a table of contents.
- FR-6: Each template in `references/templates/` MUST start with a level-1 heading whose text is a `TODO:` placeholder for the title, followed by a paragraph of at least 8 words saying which kind of document the template is for and when to use it. It then gives the section headings in the order a reader needs them. Every placeholder is a `TODO:` marker, so `doc_lint.py` catches any left behind.

### B. `doc_lint.py`

- FR-7: `doc_lint.py` MUST read a Markdown file path, or `-` for stdin, and print a text report, or JSON with `--json`. It MUST exit 0 whatever it finds. It exits 2 only for a usage error or an unreadable file, with a message on stderr. It never blocks.
- FR-8: It MUST report these **structure** findings, each with a line number (0 for the whole document). Front matter, fenced code blocks and inline code are skipped for all of them and for the link checks in FR-9, and line numbers match the input.
  - no title: the first heading isn't level 1, or there is no heading;
  - more than one level-1 heading;
  - no purpose near the top: no prose paragraph of at least 8 words between the title and the first level-2 heading, looking at no more than the 15 non-blank lines after the title. Lists, tables, code and the bold metadata lines that start ADRs (`**Status:** …`) don't count as the paragraph;
  - a skipped heading level, such as a level-4 heading directly under a level-2 one;
  - a code fence with no language;
  - a leftover marker outside code: `TODO`, `TBD`, `FIXME`, `XXX` or "lorem ipsum".
- FR-9: It MUST report these **link** findings, each with a line number:
  - a relative link or image whose target file doesn't exist, resolved against the document's directory;
  - a `#anchor` link with no matching heading in the same document;
  - a `file.md#anchor` link whose target exists but has no matching heading.

  Anchors follow GitHub's rules: lowercase, punctuation other than hyphens and spaces removed, spaces turned into hyphens, and `-1`, `-2` and so on added for repeated headings. Links with a scheme (`https:`, `mailto:` and so on) aren't checked, because the lint makes no network calls. With `-` as input, relative file links can't be resolved: they aren't reported, and the report counts them as `linksNotChecked`. In-page anchors are still checked.
- FR-10: It MUST include `plain-prose`'s report for the same text by importing `prose_check` from the sibling skill, found at `../../plain-prose/scripts/` relative to its own resolved path, and calling its `check` function. It doesn't repeat checks `prose_check` already makes: acronyms used before they're defined and bare requirement IDs come from that report. If the sibling isn't there, the `prose` field holds an error message, the text report prints it on its own line, and the exit code stays 0.
- FR-11: The JSON report MUST hold the path (or `-`), the word count, the `structure` and `links` findings with counts, `linksNotChecked`, and the `prose` report as `prose_check.py --json` gives it.

### C. The eval runners

The runners from #12 install only the skill under test and compare it with no skill. `writing-docs` needs `plain-prose` beside it, as it will be when deployed, and the useful question is what `writing-docs` adds on top of `plain-prose`. So this issue adds two runners. They reuse the helpers in `eval/skill_evals.py` but live in a new module, `eval/doc_evals.py`, and `eval/skill_evals.py` stays byte for byte as it is. That keeps `plain-prose`'s two records valid: their harness files don't change, so neither suite has to run again (over 100 model runs) for a change that doesn't touch them.

- FR-12: `eval/doc_evals.py` MUST provide two runners, `doc-trigger` and `doc-reader`. A suite that uses them names its skill, its case file and a `companions` list (here `["plain-prose"]`). `tools/cfg/eval.py` and `tools/cfg/eval_records.py` MUST accept the two runner names and the `companions` field, and refuse a suite for these runners that lacks `skill`, `cases` or a non-empty `companions` list.
- FR-13: Every model run MUST keep the isolation from FR-23 of the `plain-prose` spec: a private config directory outside the repo and `$HOME`, live settings without hooks, a private credentials copy removed afterwards, this session's `CLAUDE*` variables stripped, tools set with `--tools`, a stub `gh` first on `PATH` and an empty `GH_CONFIG_DIR`. The `skills/` directory holds the HEAD commit's copies of exactly the skills that run needs. Each run's `system`/`init` event MUST list each installed skill exactly once and each other skill under test or companion not at all; otherwise the suite stops with an isolation error and writes no record.
- FR-14: The `doc-trigger` runner MUST work like the `trigger` runner (three runs per query as `claude -p` with `stream-json` output, `--max-turns 4`, `--tools "Skill,Read,Agent"`, an empty CLAUDE.md, two triggering runs of three trigger the query, a delegated case counts only a load inside a subagent), with one difference: the companions are installed too. The skill under test then competes with `plain-prose` for every query, as it will after deploying. Both descriptions cover the same texts, so a run that loads only `plain-prose` is a miss: the trigger suite measures whether Claude also reaches for `writing-docs`, which brings `plain-prose` with it. The pass rule is the same per-class rule: at least 85% of the should-trigger queries trigger, no should-not-trigger query triggers, and every delegated query triggers. Overall accuracy is reported as information only.

### D. Wiring into the global configuration

- FR-15: In `config/CLAUDE.global.md`, the prose line #12 added MUST be extended to name `writing-docs`, so it reads:
  > Prose, alongside every step: load `plain-prose` once per session before the first text a person will read later: code comments, docstrings, assertion and error messages, CLI and UI text, commits, issue and PR text, docs and review notes (not the chat reply itself, and not text written for Claude). Load `writing-docs` once before the first such text too; it loads `plain-prose` itself and adds the reader-first steps, with templates and a lint for document files. Their rules then cover every later such text. Never re-invoke one while its `Base directory for this skill:` block is still in context; only reload after compaction drops it.

  This keeps the live line's shape (both skills named, `writing-docs` for docs, issues, PRs, commits, code comments and review notes) with the scope the owner set in #12, so the chat reply stays out.
- FR-16: `eval/clauses.json` MUST gain one `claude-md` row for the new sentence. Its `text` cites ADR-0010, #13 and the owner's decision D-1, and its `mustAppear` is "Load `writing-docs` once before the first such text too". The existing `plain-prose` row (C-79) keeps its phrase, which still appears.
- FR-17: A new overlay, `config/skill-overlays/engineering-skills:senior-architect.md`, MUST tell Claude to load `writing-docs` before writing an architecture doc or ADR file, and say that its steps apply to every document the skill produces. Like the existing overlays, it starts with "User policy for this skill." and names no paths.
- FR-18: A new overlay, `config/skill-overlays/engineering-advanced-skills:runbook-generator.md`, MUST tell Claude to load `writing-docs` before writing the runbook file, and say that the runbook's own template wins on structure while the `writing-docs` steps still apply.
- FR-19: `eval/scenarios.json` MUST gain four scenarios, all tagged `new-rule-only`, because the merge-base has neither the overlays nor the `writing-docs` sentence:
  1. "Draft the architecture doc for the new export service", with `loadsSkills: ["engineering-skills:senior-architect"]`: the answer loads `writing-docs` before writing the document;
  2. "Write the runbook for restoring the nightly database backup", with `loadsSkills: ["engineering-advanced-skills:runbook-generator"]`: the answer loads `writing-docs` before writing the runbook;
  3. "Write a README for this repo's command-line tool": the answer loads `writing-docs` before writing;
  4. "Write the commit message for this change: the nightly backup now retries a failed upload three times before giving up": the answer loads `writing-docs` before writing, because a commit message is in its scope (decision D-1).

  The first draft's fourth scenario said a commit message must not load `writing-docs`. D-1 reversed that, so it is replaced by the fourth one above. It uses a different change from S-21, `plain-prose`'s commit-message scenario, so the two don't share a prompt.
- FR-20: `hosts/personal.json` MUST list `writing-docs` in `skills`.
- FR-21: In the same PR, `config/SPEC.md` MUST change in three places: FR-15a describes the extended line, including that `writing-docs` covers the same texts as `plain-prose` and loads it itself, and drops "`writing-docs` joins this line in #13"; FR-17's overlay list gains the two new overlays and their required content; and its amendment line names #13 and quotes the owner's decision D-1.

### E. Evals

- FR-22: `eval/gates.json` MUST gain two suites:
  - `writing-docs-trigger`, runner `doc-trigger`, required when the `description` in `config/skills/writing-docs/SKILL.md` changes, when the `description` in `config/skills/plain-prose/SKILL.md` changes (the two compete in every run), or when `config/skills/writing-docs/evals/trigger-evals.json` changes;
  - `writing-docs-behaviour`, runner `doc-reader`, required when the `writing-docs` `SKILL.md` body changes, or anything under its `scripts/` or `references/`, or its `evals/behaviour-evals.json`.

  Both list `eval/doc_evals.py`, `eval/skill_evals.py` and `eval/run_eval.py` as harness files. `scenario` and `rubric` already cover `config/CLAUDE.global.md`, and `scenario` covers the overlays.
- FR-23: The `doc-trigger` suite MUST pass under FR-14's rule, with the numbers the owner accepted for decision D-2 ("Decision 2: your assumption is correct"): 15 should-trigger queries, 10 near misses, a recall of at least 85% (13 of 15), no false trigger, and every delegated query triggering. These are the same numbers as `plain-prose`'s suite.
- FR-24: `writing-docs`' `trigger-evals.json` MUST hold 25 queries, each with a `kind`.
  - Fifteen should trigger, split as 6 of kind `new-doc` (writing a new document, across at least 4 of the document types in FR-2), 2 of kind `doc-edit` (revising or restructuring an existing document), 4 of kind `prose` (a commit message, a PR description, an issue comment and a review note), and 3 of kind `code` (coding work that writes or changes text a person reads: a docstring or comment, a test fix that touches assertion messages, and a CLI help or UI string). Two of the fifteen, of any kind, are delegated: the user asks a subagent to write the text. At least 2 should not use the word "doc" or "documentation", such as "write up how to restore the backup so the on-call person can follow it".
  - Ten are near misses, as in `plain-prose`'s suite: at least 5 of kind `for-claude` (a `CLAUDE.md` edit, a `SKILL.md` edit, a subagent prompt, an overlay, a memory note) and at least 3 of kind `chat-only` (a question answered in chat, with no file or text kept for later). No near miss writes or changes a document, a commit, issue or PR text, a comment, or any string a person will see. After D-1, commit messages and PR text are should-trigger cases, not near misses.
- FR-25: The `doc-reader` runner MUST grade each case in `behaviour-evals.json` with three arms and a reader:
  - **Skill arm:** `writing-docs` and `plain-prose` installed, told "Load the writing-docs skill first, then do this task", with `--tools "Skill,Read,Bash"` and a pre-approval only for running the installed `doc_lint.py` and `prose_check.py` with `python3 -I`.
  - **Baseline arm:** only `plain-prose` installed, told "Load the plain-prose skill first, then do this task", with the same tools and a pre-approval only for `prose_check.py`. For a rewrite case, both arms rewrite the same draft, so the baseline is `plain-prose`'s own rewrite.
  - **Reader and judge:** the same as the `cold-reader` runner, in fresh config directories with no skills. Each case's questions ask what a reader of the document needs: what it's for, what to run or do first, how to tell it worked, and what to do when it doesn't.
- FR-26: For each text, the runner MUST also run the HEAD commit's `doc_lint.py` on it through stdin and record the number of structure and link findings.
- FR-27: A `doc-reader` case MUST pass only when all of these hold for the skill arm:
  - it gets at least as many answers right as the baseline;
  - it loses or distorts no `mustKeep` fact;
  - it is flagged as AI-sounding no more often than the baseline;
  - it has no more structure and link findings from `doc_lint.py` than the baseline;
  - its stream shows a `Skill` tool use for `writing-docs` and one for `plain-prose`.

  A failing case is rerun once and the rerun counted, as in the `cold-reader` runner. The suite passes when every case passes. The suite may be run up to three times in total, as the owner allowed on 2026-10-17 ("allow the same number of counted runs for the behavior suite", matching the three tuned descriptions in OS-7); each run is counted, every run and its result go in the PR, and a failing third run stops the work for the owner. `prose_check.py`'s rates and the detectability score are recorded for every text as diagnostics that don't affect the result.
- FR-28: `writing-docs`' `behaviour-evals.json` MUST hold at least six cases: three write cases (a README for a small command-line tool, a runbook for restoring a database backup, and an ADR recording a decision between two options) and three rewrite cases (a how-to guide, a README whose answer is buried, and release notes). The facts are invented and name no real person, repo or path. The cases stay document cases after D-1: the owner accepted this suite as planned ("I agree with the behavior suite you have planned"), and short texts are graded by `plain-prose`'s behaviour suite (OS-9).

### F. The repo's own files

- FR-29: `pyproject.toml` MUST lint, format-check and type-check `doc_lint.py`, `eval/doc_evals.py` and the skill's tests, and branch coverage MUST measure `doc_lint.py`.
- FR-30: The repo's `CLAUDE.md` MUST give the command for the skill's tests with coverage.
- FR-31: [ADR-0005](../../../docs/adr/0005-verification-and-ci.md)'s gates table MUST gain a row saying that a change to `writing-docs`' scripts, references or behaviour cases requires `writing-docs-behaviour`, its trigger cases or `plain-prose`'s description require `writing-docs-trigger`, and both run through `eval/doc_evals.py`. Its amendment line names #13.
- FR-32: [ADR-0010](../../../docs/adr/0010-prose-for-people.md) MUST record, with an amendment line on its Status naming #13:
  - the scope decision D-1, quoting the owner ("Decision 1: is the full live line"). The summary paragraph and the "Skill 2" section's heading and scope paragraph change to say that `writing-docs` covers every text a person reads later, the same set as `plain-prose`, with document files getting the full nine steps, templates and `doc_lint.py`, and short texts getting the steps that fit them. The "How `plain-prose` gets used" row for `writing-docs` says it loads `plain-prose` before drafting and applies it last. The Evals paragraph says `writing-docs` must trigger on the same texts as `plain-prose`;
  - that `doc_lint.py` takes acronyms and bare requirement IDs from `prose_check.py`'s report instead of checking them twice;
  - that the behaviour suite compares `writing-docs` with `plain-prose` alone, not with no skill, and adds `doc_lint.py`'s findings as a pass condition, which is how it grades "against the nine steps".
- FR-33: [The build plan](../../../docs/architecture/plan.md) MUST gain a resolved question, Q-18, recording decision D-1 with the owner's words and a link to ADR-0010, the way Q-9 records `plain-prose`'s scope.

## Non-Functional Requirements

### Performance

- NFR-P1: The `SKILL.md` body MUST stay at or under 1,000 estimated tokens (characters ÷ 4, as `check_config.py` counts). Once loaded it stays in context for the rest of the session, and the detail lives in `references/`.
- NFR-P2: `doc_lint.py` MUST finish in under 2 seconds on a 5,000-word Markdown file with 100 links on the owner's machine. The time is measured once and quoted in the PR; no test asserts wall-clock time.
- NFR-P3: `config/CLAUDE.global.md` MUST stay within the checker's limits (1,800 estimated tokens and 200 lines), and each new overlay under 700 estimated tokens.

### Security

- NFR-S1: `doc_lint.py` MUST import only the standard library and the sibling `prose_check`, and MUST NOT import `socket`, `urllib`, `http` or `subprocess`. A test checks this from the AST.
- NFR-S2: `doc_lint.py` MUST only read files: the document and the targets of its relative links. It MUST NOT follow a link outside the filesystem or write anything. That includes bytecode caches: it sets `sys.dont_write_bytecode` before importing `prose_check`, because `prose_check` turns caching off only once its own module runs, and a cache written into a deployed skill directory would show up as drift.
- NFR-S3: No file in the skill may hold a home path, a scratch path, an email address or a token. The checker's path rule and gitleaks cover this. Eval case data names no real person or private repo.

### Reliability

- NFR-R1: `doc_lint.py` MUST never raise on any text input: empty, binary-looking, all code, CRLF line endings, an unclosed fence, a link with odd characters, or a link to a directory. Unreadable bytes are replaced, not fatal.
- NFR-R2: An eval run that can't reach the model, or gets a grader reply it can't parse, MUST fail that case with the reason recorded. It never counts as a pass.

### Maintainability

- NFR-M1: `doc_lint.py` and `eval/doc_evals.py` MUST reach 100% branch coverage.
- NFR-M2: `doc_lint.py` and `eval/doc_evals.py` MUST pass `ruff check`, `ruff format --check` and `mypy --strict`.

---

## Acceptance Criteria

### AC-1: The package has its files and frontmatter (FR-1, FR-2, FR-3, FR-4, FR-6, NFR-P1)
Given the skill directory at HEAD
When the package test runs
Then every file named in FR-1 exists. The frontmatter parses with `name: writing-docs`; the description names README, ADR, runbook, how-to and changelog, names commit messages, PR text, code comments and error messages as covered, names tests and CLI or UI strings as coding work that needs it, says it loads `plain-prose`, excludes the chat reply, `CLAUDE.md` and `SKILL.md`, never says commit messages or code comments are `plain-prose`'s alone, and is at most 1,024 characters. The body names the lint command in the form FR-4 gives, names `plain-prose`, links `references/reader-first.md`, never claims chat replies are covered, and is at most 1,000 estimated tokens. Each template starts with a `TODO:` title followed by a paragraph naming its kind. Whether the body's parts follow FR-3's order is a review check.

### AC-2: Exit codes are report-only (FR-7, NFR-R1)
Given a document with findings of every kind
When `doc_lint.py` reads it from a file and from stdin
Then it exits 0 both times, and it exits 2 with a stderr message for a missing file or no arguments.

### AC-3: Each structure finding is detected (FR-8)
Given one small fixture per structure finding (no title, two titles, no purpose, a skipped level, an unlabelled fence, each leftover marker) and a clean twin for each
When each is checked
Then that finding is reported with the expected line, the twin reports none of that kind, an ADR-style `**Status:**` line doesn't count as the purpose paragraph, and markers or headings inside front matter or a fenced block are ignored.

### AC-4: Links and anchors are checked (FR-9, NFR-S2)
Given a temporary directory with a document linking to an existing file, a missing file, an image, an existing and a missing in-page anchor, an existing and a missing anchor in another file, a repeated heading (`#setup-1`), a heading with punctuation, an `https:` link, a `mailto:` link and a link to a directory
When the document is checked from its path
Then exactly the missing file, the missing in-page anchor and the missing cross-file anchor are reported, with their lines; and when the same text comes through stdin, no relative file link is reported and `linksNotChecked` counts them.

### AC-5: The prose report comes from `plain-prose` (FR-10, FR-11)
Given the skills directory with both skills
When a document with a session reference and an undefined acronym is checked with `--json`
Then the `prose` field holds `prose_check`'s report with those findings, and `structure` holds neither. Given a copy of `writing-docs` with no sibling `plain-prose`, the `prose` field is an error message, the text report prints it, and the exit code is 0.

### AC-6: The JSON report has its fields (FR-11)
Given any input
When it is checked with `--json`
Then the output parses and holds the path, word count, `structure` and `links` findings with counts, `linksNotChecked`, and `prose`.

### AC-7: Standard library only, and nothing written (NFR-S1, NFR-S2)
Given `doc_lint.py`
When its imports are read from the AST
Then each is a standard-library module or `prose_check`, and none of `socket`, `urllib`, `http` or `subprocess` appears. Running it on a document in a test leaves no `__pycache__` in either skill's `scripts/`.

### AC-8: Odd input never crashes the lint (NFR-R1)
Given empty input, CRLF text, an all-code file, an unclosed fence, invalid UTF-8 bytes, and a link whose target contains `%20` and parentheses
When each is checked
Then each exits 0 with a report.

### AC-9: The CLAUDE.md line, clause, host and overlays pass the checker (FR-15, FR-16, FR-17, FR-18, FR-20, NFR-P3)
Given the PR's `config/` tree
When `check_config.py --host hosts/personal.json --plugin-index plugins.lock.json --clauses eval/clauses.json` runs
Then it exits 0: `writing-docs` and both overlay skills resolve, both clause phrases appear in CLAUDE.md, the overlays are under 700 tokens, and CLAUDE.md is within its limits.

### AC-10: Gates require the right suites (FR-12, FR-22)
Given branches that each change only one of: the `writing-docs` description, `plain-prose`'s description, the `writing-docs` body, `scripts/doc_lint.py`, a template, or `evals/trigger-evals.json`
When `eval_records.py` works out the required suites
Then the first, second and sixth require `writing-docs-trigger`, the third, fourth and fifth require `writing-docs-behaviour`, and a `doc-trigger` or `doc-reader` suite missing `skill`, `cases` or `companions` is refused with a message.

### AC-11: `plain-prose`'s records stay valid (FR-12)
Given the PR branch
When `eval_records.py verify --base origin/main` runs
Then `eval/skill_evals.py` is unchanged from the base, and the `plain-prose-trigger` and `plain-prose-behaviour` records still verify without a new run.

### AC-12: Trigger scoring with a companion (FR-13, FR-14, FR-23)
Given a fake `claude` that replays recorded streams
When the `doc-trigger` runner scores them
Then the config directory holds both skills; a stream that lists `plain-prose` twice or not at all stops the suite with an isolation error and no record; a `Skill` call for `plain-prose` alone doesn't count as a trigger; and the per-class rule decides the result as FR-14 says.

### AC-13: Reader scoring against `plain-prose` (FR-25, FR-26, FR-27, NFR-R2)
Given faked writer, reader and judge replies and a fixed `doc_lint` result per text
When the `doc-reader` runner scores a case
Then the baseline arm's config holds only `plain-prose` and the skill arm's holds both; the case fails if the skill arm gets fewer answers right, drops a `mustKeep` fact, is flagged as AI-sounding when the baseline isn't, has more lint findings than the baseline, or lacks a `Skill` call for either skill; it passes otherwise; one rerun is counted; and an unparsable reply fails the case with the reason recorded. Every run's command has the FR-25 tool list and pre-approvals, `PATH` starts with the stub `gh`, and `GH_CONFIG_DIR` is empty.

### AC-14: Case files are well formed (FR-24, FR-28, NFR-S3)
Given the two case files
When the eval-data test runs
Then the trigger file has 25 queries with the kinds and counts FR-24 sets (15 should trigger: 6 `new-doc`, 2 `doc-edit`, 4 `prose`, 3 `code`, 2 of them delegated; 10 near misses: at least 5 `for-claude` and 3 `chat-only`), including at least 2 should-trigger queries without "doc" in them, and the behaviour file has at least three write and three rewrite cases, each with facts, questions and `mustKeep`.

### AC-15: The repo's checks cover the new code (FR-29, NFR-M1, NFR-M2)
Given the PR branch
When ruff, ruff format, mypy and pytest with branch coverage run as the repo's `CLAUDE.md` says
Then all pass, and `doc_lint.py` and `eval/doc_evals.py` are at 100% branch coverage.

### AC-16: Live evals pass (FR-15, FR-17, FR-18, FR-19, FR-23, FR-27, NFR-P3)
Given the owner's machine, logged in, on a clean tree at the PR's head
When `TMPDIR=/tmp python3 -I tools/cfg/eval.py` runs the required suites
Then `scenario` passes with no regressions and the four new scenarios passing, `rubric` scores at least 80, and `writing-docs-trigger` and `writing-docs-behaviour` pass. Their records are committed and `eval_records.py verify` exits 0. Every rerun is reported in the PR.

### AC-17: Docs follow the change (FR-21, FR-30, FR-31, FR-32, FR-33)
Given the PR diff
When it is reviewed
Then `config/SPEC.md`, the repo `CLAUDE.md`, ADR-0005, ADR-0010 and the plan carry the changes those requirements describe, and none of them still says `writing-docs` covers documents only (a review check).

### AC-18: The references read as FR-5 says (FR-5, FR-6)
Given `references/reader-first.md` and the templates
When they are reviewed, and `doc_lint.py` runs on each
Then the guide covers all nine steps with an example each and has the short-text section with its three examples, the lint reports no structure or link finding on the guide, and each template's only structure findings are its `TODO:` markers (a review check plus a test).

---

## Edge Cases

- EC-1: The input is empty or has only front matter. The report has 0 words, a "no title" finding, no other structure finding, and a `prose` report with no findings. It exits 0.
- EC-2: The file doesn't exist or can't be read. The lint prints a message to stderr and exits 2.
- EC-3: The input isn't valid UTF-8. Bad bytes are replaced and the check goes on.
- EC-4: A code fence is never closed. Everything after it counts as code, so no heading, marker or link after it is reported.
- EC-5: A relative link points at a directory. It counts as existing.
- EC-6: A link target has URL escapes such as `%20`. It is unescaped before the file check.
- EC-7: A heading is written in setext style (underlined with `===` or `---`). This lint only reads `#` headings, so such a document reports "no title". The templates and the guide use `#` headings only, and the finding's text says setext headings aren't read.
- EC-8: The sibling `plain-prose` skill is missing, as in a partial deploy. FR-10's error message appears and the structure and link checks still run.
- EC-9: A trigger run hits `--max-turns` before a subagent loads anything. That run doesn't trigger.
- EC-10: The skill arm loads `writing-docs` but not `plain-prose`. The case fails, with the reason "the skill arm never loaded plain-prose".
- EC-11: The model replies with the whole document wrapped in one code fence. When the reply's first non-blank line opens a fence labelled `markdown`, `md` or nothing, and its last non-blank line closes it, that outer fence is removed before linting and grading, in both arms, so neither is penalised for it. Fences inside the document are never touched.
- EC-12: `claude` isn't on `PATH`, or isn't logged in. Like the existing suites, `eval.py` refuses before any run and writes no record.

---

## API Contracts

```typescript
// doc_lint.py
// python3 -I <skill>/scripts/doc_lint.py <path | -> [--json]
// exit 0 = report printed (whatever it found); 2 = usage error or unreadable file
interface Finding { line: number; kind: "structure" | "links"; text: string; fix: string }  // line 0 = whole document
interface DocReport {
  path: string;                       // "-" for stdin
  words: number;
  findings: { structure: Finding[]; links: Finding[] };
  counts: { structure: number; links: number };
  linksNotChecked: number;            // relative file links skipped because the input came from stdin
  prose: ProseReport | { error: string };   // ProseReport as prose_check.py --json gives it
}

// eval/doc_evals.py, called by tools/cfg/eval.py like the other runners
function run_doc_trigger(ctx: Context, suite: Suite): SuiteResult;  // pass, recall, falseTriggers, accuracy (info), cases
function run_doc_reader(ctx: Context, suite: Suite): SuiteResult;   // pass, rerunsUsed, cases (each with lint counts and diagnostics)
```

## Data Models

### Suite entry (`eval/gates.json`), new fields

| Field | Type | Constraint |
|---|---|---|
| `runner` | `"doc-trigger"` \| `"doc-reader"` | New values |
| `skill` | string | A directory under `config/skills/` |
| `cases` | string | Repo-relative path to the case file |
| `companions` | string[] | Non-empty; each a directory under `config/skills/`; installed beside the skill (and alone in the `doc-reader` baseline) |
| `paths`, `selectors`, `harness` | as today | `harness` lists `eval/doc_evals.py`, `eval/skill_evals.py`, `eval/run_eval.py` |

### Trigger case (`evals/trigger-evals.json`, a list)

| Field | Type | Constraint |
|---|---|---|
| `query` | string | A realistic user prompt |
| `should_trigger` | boolean | |
| `delegated` | boolean | Optional; true only with `should_trigger` |
| `kind` | `"new-doc"` \| `"doc-edit"` \| `"prose"` \| `"code"` \| `"for-claude"` \| `"chat-only"` | The first four should trigger, the last two shouldn't. The runner ignores it; AC-14 checks the mix |

### Behaviour case (`evals/behaviour-evals.json`, a list)

The same fields as `plain-prose`'s behaviour cases: `id`, `kind` (`write` or `rewrite`), `task`, `facts`, `draft` (rewrite cases only), `questions` (3 or 4) and `mustKeep`.

---

## Out of Scope

- OS-1: `plain-prose` itself (#12, merged) and any change to `eval/skill_evals.py`, which would make both `plain-prose` suites run again.
- OS-2: Overlays chaining `writing-docs` from other document-producing skills, such as `changelog-generator`, `codebase-onboarding` or `senior-devops`. ADR-0010 names only `senior-architect` and `runbook-generator`; more can follow once the evals show the two work.
- OS-3: A CI job running `doc_lint.py` on changed Markdown files. CI is #10.
- OS-4: Fixing findings `doc_lint.py` reports on this repo's existing docs. Running it on them is a useful check (see the test plan), but the fixes belong to their own change.
- OS-5: Replacing the live prototype in `~/.claude/skills/writing-docs/` or editing the live `~/.claude/CLAUDE.md`. That happens through adoption or deploy (#7, #8). This PR doesn't touch `~/.claude`. After D-1 the repo's `writing-docs` scope matches the live line's, but the live line still puts chat replies in `plain-prose`'s scope, until a deploy or the owner edits it.
- OS-6: Checking `https:` links. The lint makes no network calls.
- OS-7: Optimising the description automatically with skill-creator's `run_loop`. If the trigger suite fails, the description may be tuned by hand up to three times in total, each tuned version getting one run, and a failing third run stops the work for the owner. The first tuning follows the rule from #12. The owner allowed more on 2026-10-17 ("D", then "3 total tuned descriptions are allowed"), after the first tuned run met the recall bar (13 of 15) but failed the rule that every delegated query triggers, with the delegated tutorial loading the skill inside its subagent on one run of three and the issue-comment query on none. Every tuning names kinds of work in general terms and copies no eval query's wording; the later ones aim at work done by a subagent and at short comments, and keeps the description within 1,024 characters. The cases and the bar in FR-23 and FR-24 don't change.
- OS-8: The `work` host profile. That is #17.
- OS-9: Behaviour cases for short texts (commit messages, PR text, comments). The owner accepted the document-only behaviour suite as planned. `plain-prose`'s behaviour suite already grades short texts, and `writing-docs` adds only a few sentences of guidance for them.
- OS-10: Changing `plain-prose`'s description to mention `writing-docs`. It would make both `plain-prose` suites run again (OS-1). If the trigger suite shows Claude picking `plain-prose` alone for short texts, the tuning rule in OS-7 applies to `writing-docs`' description first.

---

## Test Plan

This plan follows the overlay for `senior-qa`; the guides' React and Jest examples are translated to pytest.

**Pyramid placement.**

| Level | Criteria | How |
|---|---|---|
| Unit | AC-2 to AC-8 | Pure functions in `doc_lint.py`, called in-process on small fixture documents, plus `main()` for exit codes |
| Integration | AC-1, AC-9 to AC-15, AC-18 (its test half) | The real files in the tree; the checker and `eval_records.py` in-process; the runners end to end with the existing fake `claude` from `tests/eval_fakes.py` |
| Live, owner's machine | AC-16 | `tools/cfg/eval.py` with the real `claude`; run once, records committed |
| Review checks | AC-17, AC-18 (its review half), FR-3's order | Read in the PR |

**Coverage by risk.** 100% branch coverage for `doc_lint.py` and `eval/doc_evals.py`. The runners decide whether a PR may merge, and a wrong link or anchor finding would send Claude chasing problems that aren't there. The templates and the guide are text, checked by AC-18.

**Flake hazards and how they're removed.**
- Model variance: unit and integration tests never call a model; the fake `claude` returns canned streams chosen by prompt. Live variance is handled by the two-of-three trigger rule and one counted rerun per behaviour case, both reported.
- Wall-clock time: no test asserts a duration. NFR-P2 is measured once and quoted.
- Filesystem: link tests build their files in `tmp_path`, so nothing depends on the repo's own docs. A separate, non-gating test runs `doc_lint.py` on every Markdown file in `docs/` and only asserts that each exits 0 with a report; the findings are printed for the PR (OS-4).
- Temporary directories and environment: `tmp_path` and `monkeypatch` for `HOME`, `TMPDIR` and `PATH`; a teardown check confirms no eval directory or credentials copy is left.
- Thread order: results are sorted by case before scoring and writing.

**Isolation and fixtures.** Fixture documents live in `config/skills/writing-docs/tests/fixtures/`, one small file per finding plus a clean twin. A `doc(...)` factory in the skill's tests builds documents from parts (title, purpose, headings, links), so each test states only what it needs. Canned streams come from the helpers already in `tests/eval_fakes.py`, extended to list more than one skill in the `init` event.

**Behaviour over mocks.** Tests assert what a user sees (findings, lines, exit codes, the record) and never which internal function ran. The only fake is the `claude` executable at the process boundary. `prose_check` is the real sibling module, not a stub.

**Existing tests changed.** `tests/test_eval_records.py` and `tests/test_eval_cmd.py` gain the two runner names and the `companions` field. `tests/test_repo_config.py` keeps passing with `writing-docs` in the host profile. No test is deleted, and no test of `eval/skill_evals.py` changes.

**Test locations.** The skill's tests go in `config/skills/writing-docs/tests/` and deploy with it ([ADR-0002](../../../docs/adr/0002-repo-layout.md)). They run as `.venv/bin/python -I -m pytest config/skills/writing-docs/tests --cov=config/skills/writing-docs/scripts --cov-branch`. Runner and gates tests go in `tests/`.
