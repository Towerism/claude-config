# Spec: the `plain-prose` skill, its CLAUDE.md line and the reviewer overlay line

**Author:** Claude (for the owner, Towerism)
**Date:** 2026-10-16
**Status:** Approved by the owner on 2026-10-20 ("approve") with [#77](https://github.com/Towerism/claude-config/issues/77)'s spec, which amends FR-24: each trigger query runs twice and a third time only when the two disagree, instead of three times, to fit the owner's 3-minute limit for the full eval set. Before that: Approved by the owner on 2026-10-20 ("approve") for [#66](https://github.com/Towerism/claude-config/issues/66): #62 renamed the overlays in `config/skill-overlays/` after the bare skill name, so the requirement for the `adversarial-reviewer` overlay line (FR-18) now uses the new file name. Before that: Approved by the owner on 2026-10-17 ("approve"), as it stood at 716ef9f, which allows tuning the description by hand but not with the automatic loop. Before that it was a draft again (2026-10-17): The owner approved the spec at b8c1122 on 2026-10-16 ("approve"). The trigger suite then passed at 13 of 15 and, after a harness-only change, failed at 12 of 15; the regex and failing-test queries missed on both runs. Asked "Which option for #12's trigger suite: A (tune the description to cover coding tasks that touch tests and messages, recommended), B (one counted rerun), C (an 80% bar), or D (rerun as is)?", the owner answered "A". That allows a hand edit of the description (FR-2, OS-7). Earlier history: The owner approved the first version on 2026-10-16 ("approve"). It came back to draft after the first live trigger run, when the owner set the scope in three statements the same day: "chat replies are not in scope. but since code comments are, coding-only tasks must remain scope to that extent", then "The near misses are fine. Fixing a failing test can sometimes mean changing assertion messages. Those absolutely are in scope since they are human facing. That's another way to look at it. Anything that is (future--any amount of time in the future) human facing is in scope.", and "This includes text that is rendered by a CLI or a web page eventually". Asked whether the chat reply itself stays out, the owner confirmed: "yes, keep chat replies out of scope". The owner then asked for 15 should-trigger cases ("increase triggering cases to 15"). Approval mode: manual review with adversarial pass, chosen by the owner for this session's issues ("work on the implementation plan. manual review + adversarial.").
**Issue:** [#12](https://github.com/Towerism/claude-config/issues/12)
**Reviewers:** `adversarial-reviewer` (one pass per change, see `REVIEW-NOTE.md`; named `engineering-skills:adversarial-reviewer` before #62), then the owner
**Decided by:** [ADR-0010](../../../docs/adr/0010-prose-for-people.md) (where the guidance lives, the vendoring, the evals), [ADR-0005](../../../docs/adr/0005-verification-and-ci.md) (eval gates and records), [ADR-0002](../../../docs/adr/0002-repo-layout.md) (where specs live)

---

## Context

The owner wants everything Claude writes for people to make sense to a reader with no context, usually the owner months later, and to be free of the patterns that mark text as AI-written ([ADR-0010](../../../docs/adr/0010-prose-for-people.md)). A prototype `plain-prose` skill has been installed in `~/.claude/skills/plain-prose/` since 2026-10-16 so it could be used before this repo could deploy anything. This issue builds the real skill in `config/skills/plain-prose/` and wires it in. The prototype stays live until a deploy or adoption replaces it (#7 and #8, both waiting on the held #6).

This spec covers more than the skill itself. The issue also adds a line to the global CLAUDE.md, a line to the `adversarial-reviewer` overlay, four scenarios, two eval suites and an upstream check for vendored files. Under [ADR-0002](../../../docs/adr/0002-repo-layout.md), those coordination and tooling changes would normally be specced in the issue body. Splitting one issue into two specs would mean two approvals for one PR, so this file specs all of it. Section F lists every change outside the skill directory. As ADR-0002 requires for coordination changes, the same PR updates `config/SPEC.md`.

**The live CLAUDE.md is ahead of the repo.** The owner's live `~/.claude/CLAUDE.md` already has a prose line that `config/CLAUDE.global.md` lacks. It covers both `plain-prose` and `writing-docs`:

> Prose, alongside every step: load `plain-prose` once per session before the first human-facing text (chat replies and questions included), and `writing-docs` once before the first doc, issue, PR, commit, code comment or review note. Their rules then cover every later text. Never re-invoke one whose `Base directory for this skill:` block is still in context; only reload after compaction drops it.

The checker fails when CLAUDE.md names a skill it can't resolve, and `writing-docs` doesn't exist in the repo until #13. So this PR adds the `plain-prose` half of the owner's wording (FR-15), and #13 extends it to the full live line. The ADR-0010 draft wording ("Before writing prose for people …, load `plain-prose`") is replaced by the owner's newer wording, because the owner wrote it later.

**Scope set after the first live run (2026-10-16).** The first approved version followed the live line above and covered chat replies. The first trigger run then loaded the skill for six coding tasks that were meant as near misses, among them a failing-test fix, a regex and a config bump. The owner answered in three statements: "chat replies are not in scope. but since code comments are, coding-only tasks must remain scope to that extent", and then "The near misses are fine. Fixing a failing test can sometimes mean changing assertion messages. Those absolutely are in scope since they are human facing. That's another way to look at it. Anything that is (future--any amount of time in the future) human facing is in scope.", and "This includes text that is rendered by a CLI or a web page eventually". Asked whether that keeps the chat reply itself out of scope, the owner confirmed: "yes, keep chat replies out of scope".

This spec reads them together:

- **In scope:** any text a person will read at some later time, including text a CLI or web page will eventually show. Examples: code comments and docstrings; assertion, error and log messages; CLI help, usage text and output; UI labels, page copy and templates; user-facing errors kept in data files; commit messages, issue and PR text, docs, review notes, and messages drafted for the owner to send.
- **Out of scope:** the chat reply itself, which is read once and gone, and text written for Claude (CLAUDE.md, `SKILL.md`, overlays, memory, subagent prompts).

A coding task is in scope whenever it may produce such text, so loading the skill for a failing-test fix or a regex is correct, not a false trigger. The live `~/.claude/CLAUDE.md` still says "chat replies and questions included"; this PR doesn't touch `~/.claude` (OS-5), so that line stays different from the repo's until a deploy or until the owner edits it.

**Vendored material.** Four files come from the claude-code-skills marketplace at commit `19392f7a08264ed00486a251f5b2098321771f94` (MIT). They come from the `marketing-skills` plugin, which isn't installed ([ADR-0010](../../../docs/adr/0010-prose-for-people.md) explains why). The owner's local clone of that marketplace, `~/.claude/plugins/marketplaces/claude-code-skills/`, is at that commit. The prototype changed all four files, even the two scripts it says are "unmodified except for this header". This build takes them from upstream again.

### Skills used to build this

| Skill | Use |
|---|---|
| `github-issue-lifecycle` | Claiming #12, labels, branch and PR |
| `spec-driven-workflow` | This spec, validated with `tools/vendor/claude-skills/spec_validator.py --strict` |
| `senior-qa` (all three guides read) | The test plan below |
| `anthropic-skills:skill-creator` | Skill structure: a short `SKILL.md`, detail in `references/`, deterministic work in `scripts/`, a pushy description, trigger queries with near misses |
| `tdd-guide` | Red-green-refactor for `prose_check.py`, the runners and the `lock.py` change |
| `skill-tester` | Validating the finished skill package |
| `skill-security-auditor` | The vendored scripts, before they're committed |
| `adversarial-reviewer` | One pass on this spec, one on the PR diff |
| `writing-docs`, `plain-prose` (prototypes) | Every human-facing text, checked with `doc_lint.py` |

---

## Functional Requirements

### A. The skill package

- FR-1: The skill MUST live in `config/skills/plain-prose/` with this layout: `SKILL.md`, `SPEC.md`, `REVIEW-NOTE.md`, `VENDORED.json`, `LICENSE-vendored.md`, `scripts/prose_check.py`, `scripts/vendored/{readability_scorer.py, ai_content_detector.py}`, `references/vendored/{ai-tells-checklist.md, plain-english-alternatives.md}`, `evals/{trigger-evals.json, behaviour-evals.json}` and `tests/`.
- FR-2: `SKILL.md` MUST have frontmatter with `name: plain-prose` and a `description` that:
  - says what the skill does;
  - says it covers any text a person will read later, and lists examples: code comments, docstrings, assertion, error and log messages, text a CLI or web page will show (help and usage text, CLI output, UI labels, page copy, templates), commit messages, issue and PR text, review notes, docs, artifact pages, release notes and messages drafted for the user to send;
  - says a coding task needs it whenever the task may write or change such text, and names the coding work that usually does: writing or fixing tests (assertion messages), adding validation or error handling (error messages), adding logic that deserves a comment or docstring (a regex, a non-obvious branch), and editing CLI help, CLI output or UI strings. The two live trigger runs showed the description too weak on exactly these tasks, so the wording may be tuned by hand, without changing the cases or the bar in FR-24 and FR-25. The tuning names kinds of work in general terms and doesn't copy the eval queries' wording. Each tuned version gets one trigger run, and every run goes in the PR; if a tuned version fails, Claude stops and reports instead of tuning again;
  - stays at or under 1,024 characters, the description limit in the Agent Skills format. The current one is 1,096, so the tuning also has to tighten it;
  - says to load it once per session, and not again while its instructions are in context;
  - excludes the chat reply itself, code itself, identifiers, machine-read formats, and files written for Claude.
- FR-3: The `SKILL.md` body MUST hold these parts, in this order:
  1. the goal: a cold reader understands the text on the first read, and AI tells cost comprehension;
  2. the load-once rule, naming the same scope as the description, so the body never claims chat replies are covered;
  3. the cold-reader rules;
  4. the AI-tell rules from [ADR-0010](../../../docs/adr/0010-prose-for-people.md)'s Decision section, pointing to `references/vendored/ai-tells-checklist.md` and `plain-english-alternatives.md` for the full lists;
  5. precedence: comprehension first, then technical precision and the reader's own terms, then formats other tools need;
  6. the procedure: write for a cold reader, run `prose_check.py` on anything longer than a few sentences, fix the findings that would confuse or distract, and stop without chasing the score.
- FR-4: The `SKILL.md` body MUST give the checker's command as `python3 -I <this skill's base directory>/scripts/prose_check.py <file or -> [--json]`, never through a shell variable.

### B. Vendored files

- FR-5: Each of the four upstream files MUST first be committed byte for byte as it is at the pinned commit, in a commit of its own. Adaptations come in later commits, so every change from upstream shows up as a reviewable diff.
- FR-6: `scripts/vendored/readability_scorer.py`, `scripts/vendored/ai_content_detector.py` and `references/vendored/plain-english-alternatives.md` MUST stay byte-identical to upstream. `prose_check.py` replaces the scorer's marketing thresholds with its own when it calls the scorer. Attribution goes in `VENDORED.json` and `LICENSE-vendored.md`, not in a header inside these files.
- FR-7: `references/vendored/ai-tells-checklist.md` MUST be adapted as [ADR-0010](../../../docs/adr/0010-prose-for-people.md) says. Keep categories 1 to 5. Add a note that technical senses of flagged words are allowed ("ensure" for a real guarantee, "framework" naming one, "robust" with a stated property). Drop the brand-voice remedies, but keep the rhythm and structure advice. Add a header naming the source and the adaptation.
- FR-8: `VENDORED.json` MUST list each vendored file with:
  - `path`, relative to the skill directory;
  - `upstream` (`repo`, the 40-character `commit`, and `path` inside the marketplace repo);
  - `marketplace` (`claude-code-skills`) and `marketplacePath` (the path inside the local marketplace clone);
  - `sha256` of the upstream file at the pinned commit;
  - `adapted` (true only for the checklist);
  - `licence` (`MIT`) and `licenceFile` (`LICENSE-vendored.md`).
- FR-9: `LICENSE-vendored.md` MUST hold the upstream MIT licence text and say which files it covers.
- FR-10: `skill-security-auditor` MUST run on the two vendored scripts before they are committed. Its verdict goes in the PR description. A FAIL verdict stops the import and goes to the owner.

### C. `prose_check.py`

- FR-11: `prose_check.py` MUST read a file path, or `-` for stdin, and print a text report, or JSON with `--json`. It MUST exit 0 whatever it finds. It exits 2 only for a usage error or an unreadable file, with a message on stderr. It never blocks anything.
- FR-12: Before analysing, it MUST blank out YAML front matter, fenced code blocks, inline code, Markdown tables and lines that start with an HTML tag, while keeping line numbers aligned with the input.
- FR-13: It MUST report four kinds of finding, each with a line number where one applies (0 means the whole text):
  - **cold reader:** session references ("as discussed", "see above", "the earlier approach" and similar), bare requirement or finding IDs (`FR-23`, `W2`), and acronyms used before they're defined, with common technical acronyms allowed;
  - **readability:** sentences over 30 words, passive voice and filler words (both from the vendored scorer's patterns), plus the Flesch–Kincaid grade as information only;
  - **AI tells:**
    - the tell words and phrases, each with a plainer alternative. The lists are kept in `prose_check.py`, drawn from the vendored checklist and the detector's phrase list, so a change to either reference is reviewed by hand and doesn't silently change the checker;
    - em-dashes, and tricolons of same-length words;
    - rhythm: a run of four sentences of similar length (all at least 8 words, within 4 words of each other), or three or more paragraphs opening with the same two words;
    - structure: more than half the lines being list items in a text of 8 or more lines, more than 1.5 bold spans per 100 words, or more than 2.5 headings per 100 words when there are at least 4;
  - **detectability:** the vendored detector's composite score and verdict, labelled as a diagnostic, never a gate.
- FR-14: The JSON report MUST include the word count, every finding, the count and the rate per 1,000 words for each kind, the structure measures (list-line share, bold and headings per 100 words), the readability information, the detectability score and the standard deviation of sentence length.

### D. Wiring into the global configuration

- FR-15: `config/CLAUDE.global.md` MUST gain this line after the pipeline list, unemphasised:
  > Prose, alongside every step: load `plain-prose` once per session before the first text a person will read later: code comments, docstrings, assertion and error messages, CLI and UI text, commits, issue and PR text, docs and review notes (not the chat reply itself, and not text written for Claude). Its rules then cover every later such text. Never re-invoke it while its `Base directory for this skill:` block is still in context; only reload after compaction drops it.
- FR-16: `eval/clauses.json` MUST gain one `claude-md` row for that line. Its `text` cites the owner's decision (ADR-0010, 2026-10-16), and its `mustAppear` is "load `plain-prose` once per session before the first text a person will read later".
- FR-17: `eval/scenarios.json` MUST gain four scenarios, each tagged `new-rule-only` because the merge-base CLAUDE.md has no prose rule:
  1. "write the PR description for this diff": the answer loads `plain-prose` before writing.
  2. "write the commit message for this change": the answer loads `plain-prose` before writing.
  3. "draft the issue for this bug report": the answer loads `plain-prose` before writing.
  4. "comment on #12 that the fix is merged", with `plain-prose` already loaded (`loadsSkills: ["plain-prose"]`): the answer writes the comment without loading `plain-prose` again.
- FR-18: `config/skill-overlays/adversarial-reviewer.md` (named `engineering-skills:adversarial-reviewer.md` until #62) MUST gain one line: review notes and the findings written into PR descriptions follow `plain-prose`.
- FR-19: `hosts/personal.json` MUST list `plain-prose` in `skills`.
- FR-20: In the same PR, `config/SPEC.md` MUST gain a requirement for the prose line, filed under its global CLAUDE.md section, that the new scenarios cite in `covers`. That requirement says the line covers any text a person will read later, including comments, docstrings, assertion or error messages and text a CLI or web page will show, and not the chat reply itself or text written for Claude. Its `adversarial-reviewer` overlay requirement MUST gain the new line, and its amendment line MUST name #12.

### E. Evals

- FR-21: `eval/gates.json` MUST gain two suites:
  - `plain-prose-trigger`, run by a new `trigger` runner, required when the `description` in `config/skills/plain-prose/SKILL.md` changes or `evals/trigger-evals.json` changes;
  - `plain-prose-behaviour`, run by a new `cold-reader` runner, required when the `SKILL.md` body, anything under `scripts/` or `references/`, or `evals/behaviour-evals.json` changes.

  `scenario` and `rubric` already cover `config/CLAUDE.global.md`, and `scenario` covers the overlay.
- FR-22: Both runners MUST live in a new `eval/skill_evals.py`, with the skill's name and case file taken from the suite entry, so #13 can reuse them for `writing-docs`. `tools/cfg/eval.py` and `tools/cfg/eval_records.py` MUST accept the two runner names and the suite fields `skill` and `cases`, and refuse a suite that lacks them.
- FR-23: Every model run in both runners MUST use the isolation from `eval/run_eval.py`:
  - its own temporary `CLAUDE_CONFIG_DIR` outside the repo and outside `$HOME`;
  - the live settings without hooks, the plugins as a symlink, and a private credentials copy removed afterwards;
  - this session's `CLAUDE*` variables stripped from the environment;
  - `skills/` holding only the HEAD commit's copy of the skill under test, or nothing for a baseline arm.

  Each run's `system`/`init` stream event MUST list the skill under test exactly once in a with-skill run and not at all in a baseline run. Otherwise the suite stops with an isolation error and writes no record.

  Every run MUST also limit what the model can reach. The available tools are set with `--tools`, not merely pre-approved. A stub `gh` that records its arguments and exits 0 comes first on `PATH`, and `GH_CONFIG_DIR` points at an empty temporary directory. That way no prompt about issues or PRs can reach GitHub with the owner's login, even if the copied live settings later allow `gh`.
- FR-24: The `trigger` runner MUST run each query in `trigger-evals.json` twice, and a third time only when the two runs disagree (amended by #77; it was three times), as `claude -p` with `stream-json` output, `--max-turns 4` and `--tools "Skill,Read,Agent"`. The config directory has an empty CLAUDE.md, so only the description can trigger the skill.
  - A run triggers when the stream holds a `Skill` tool use for the skill.
  - For a case marked `delegated`, it counts only when that tool use comes from a subagent, that is, when the event has a non-null `parent_tool_use_id`.
  - A query triggers when two of its runs trigger.
  - The suite passes when three things hold, each judged on its own class:
    - at least 85% of the should-trigger queries trigger (with 15, at least 13);
    - no should-not-trigger query triggers;
    - every delegated query triggers.

    The record also reports overall accuracy, as information only. With 15 queries that should trigger and 10 that shouldn't, one accuracy figure would weigh the two classes unevenly, so each class gets its own rule.
- FR-25: `trigger-evals.json` MUST hold 25 queries.
  - Fifteen should trigger. Two of them are delegated: the user asks for a subagent to write the prose, as [ADR-0010](../../../docs/adr/0010-prose-for-people.md) requires. At least three are coding tasks that may write or change text a person reads later, such as a failing-test fix that may touch assertion messages, new validation with an error message, or a function that needs a docstring. At least one edits text a CLI or web page shows, such as `--help` text or a button label. The fifteen SHOULD be split as eight prose texts (two of them delegated), five coding tasks and two CLI or web page edits, so coding work, which the owner put in scope, is tested as often as written prose.
  - Ten are near misses that shouldn't trigger. At least five are text written for Claude: a CLAUDE.md edit, a `SKILL.md` edit, a subagent prompt, an overlay edit and a memory note. At least three are questions the user wants answered in chat only, with no file, commit or post, because the chat reply itself is out of scope. No near miss touches code comments or any string a person will see.
- FR-26: The `cold-reader` runner MUST grade each case in `behaviour-evals.json` by cold-reader comprehension:
  - **Write cases** give a writing task and its facts. The with-skill arm gets the skill, the instruction to load it, `--tools "Skill,Read,Bash"` and a pre-approval for running `prose_check.py` only. The baseline arm has no skill and no tools. Both must reply with only the text.
  - **Rewrite cases** give a draft full of AI tells. The baseline is the draft itself.
  - **The reader** is a fresh config directory that sees only the produced text. It answers the case's fixed questions (what changed or is needed, why, what to do next, what the risk is), answering "not stated" where the text is silent. Then, as a reader who knows AI writing, it says whether anything read as AI-generated and distracted it, quoting the passage.
  - **The judge** is a second fresh call, given the facts, the questions, the reader's answers and the text. It marks each answer right or wrong, and lists any fact from the case's `mustKeep` list that the text leaves out or gets wrong.
- FR-27: A cold-reader case MUST pass only when all three hold for the skill arm:
  - it gets at least as many answers right as the baseline;
  - it loses or distorts no `mustKeep` fact;
  - it is flagged as AI-sounding no more often than the baseline.

  A failing case is rerun once, as the scenario suite does, and the rerun is counted in the record. The suite passes when every case passes. For a write case, the with-skill run MUST show a `Skill` tool use for the skill, or the case fails.
- FR-28: `behaviour-evals.json` MUST hold at least six cases: three write cases (a PR description, a commit message and an issue comment) and three rewrite cases (an issue body, a review note and a reply drafted for the owner to send). The facts are invented for the evals and name no real person, repo or path.
- FR-29: Each record from both runners MUST include, per case, `prose_check.py`'s rates per 1,000 words and the detectability score for every produced text, labelled as diagnostics that don't affect the pass.

### F. The upstream check and the repo's own files

- FR-30: `tools/cfg/lock.py --check` MUST read every `VENDORED.json` under `tools/vendor/*/` and `config/skills/*/`.
  - An entry with `plugin` and `installedPath` keeps its current check and stays in the `vendored` list, which sets the exit code.
  - An entry with `marketplace` and `marketplacePath` is compared with the file at `~/.claude/plugins/marketplaces/<marketplace>/<marketplacePath>`. A difference is reported as "the marketplace copy is missing" or "the marketplace copy has a different sha256" in a separate `upstream` list. That list never changes the exit code, because [ADR-0010](../../../docs/adr/0010-prose-for-people.md) says an upstream change is a prompt to review, not a failure. The text output prints it under its own heading.
  - An entry with neither pair is a lock input error.
- FR-31: `pyproject.toml` MUST lint, format-check and type-check `prose_check.py`, `eval/skill_evals.py` and the skill's tests, while still excluding the vendored files, the hooks (until #20) and the lifecycle skill. Branch coverage MUST measure `prose_check.py`.
- FR-32: The repo's `CLAUDE.md` MUST give the command for the skill's tests with coverage. [ADR-0005](../../../docs/adr/0005-verification-and-ci.md) MUST change in two places, both recorded on its amendment line naming #12:
  - its gates table gains the extra paths from FR-21 that require `plain-prose-behaviour`;
  - its note that `config/` is excluded from ruff until #20 says instead that only the hooks, the lifecycle skill and vendored files stay excluded, as FR-31 sets.
- FR-33: [ADR-0010](../../../docs/adr/0010-prose-for-people.md) MUST record the owner's three 2026-10-16 statements and the confirmation that followed ("yes, keep chat replies out of scope"), quoted, and the scope they set: any text a person will read later, including text a CLI or web page will eventually show, is in scope, and the chat reply itself and text written for Claude are not. It records this in its opening summary and its scope list, which today say chat replies are covered, and in its eval section, which today says the skill must not trigger on code. The plan's resolved question Q-9 in `docs/architecture/plan.md`, which says `plain-prose` covers chat replies, MUST be updated the same way.

## Non-Functional Requirements

### Performance

- NFR-P1: The `SKILL.md` body MUST stay at or under 800 estimated tokens (characters ÷ 4, as `check_config.py` counts), because once loaded it stays in context for the rest of the session.
- NFR-P2: `prose_check.py` MUST finish in under 2 seconds on a 5,000-word Markdown file on the owner's machine. The time is measured once and quoted in the PR; no test asserts wall-clock time.
- NFR-P3: `config/CLAUDE.global.md` MUST stay within the checker's limits (1,800 estimated tokens and 200 lines).

### Security

- NFR-S1: `prose_check.py` and the vendored scripts MUST import only the standard library and MUST NOT import `socket`, `urllib`, `http` or `subprocess`. A test checks this from the AST.
- NFR-S2: No file in the skill may hold a home path, a scratch path, an email address or a token. The checker's path rule and gitleaks cover this.
- NFR-S3: Eval case data MUST NOT include real credentials, names of real people or private repo names.

### Reliability

- NFR-R1: `prose_check.py` MUST never raise on any text input: empty, binary-looking, all code, CRLF line endings, or no sentence punctuation. Unreadable bytes are replaced, not fatal.
- NFR-R2: An eval run that can't reach the model, or gets a grader reply it can't parse, MUST fail that case with the reason recorded. It must never count as a pass.

### Maintainability

- NFR-M1: `prose_check.py`, `eval/skill_evals.py` and the `lock.py` change MUST reach 100% branch coverage.
- NFR-M2: `prose_check.py` MUST pass `ruff check`, `ruff format --check` and `mypy --strict`.

---

## Acceptance Criteria

### AC-1: The package has the files and frontmatter it needs (FR-1, FR-2, FR-3, FR-4, NFR-P1)
Given the skill directory at HEAD
When the package test runs
Then every file named in FR-1 exists, and the frontmatter parses with `name: plain-prose`. The description names code comments, assertion messages, CLI or web page text, commits and review notes, and the coding work FR-2 lists (tests, validation or error handling, and logic that deserves a comment), and is at most 1,024 characters, and its exclusions name the chat reply, `CLAUDE.md` and `SKILL.md`. The body doesn't say chat replies are covered, links both vendored references, names the checker command in the form FR-4 gives, and is at most 800 estimated tokens. Whether the body's parts follow FR-3's order is a review check.

### AC-2: Vendored files match their record (FR-5, FR-6, FR-8, FR-9)
Given `VENDORED.json`
When the vendored-files test runs
Then every listed file exists, every entry has the fields from FR-8 with a 40-character commit, every file not marked `adapted` has the recorded `sha256`, `LICENSE-vendored.md` starts with the MIT licence text, and every file under the two `vendored/` directories is listed.

### AC-3: The checklist adaptation is visible (FR-7)
Given the git history of `references/vendored/ai-tells-checklist.md`
When it is reviewed
Then its first commit matches the recorded upstream `sha256` and a later commit holds the adaptation (a review check, quoted in the PR).

### AC-4: Report-only exit codes (FR-11, NFR-R1)
Given text with findings of every kind
When `prose_check.py` reads it from a file and from stdin
Then it exits 0 both times, and it exits 2 with a stderr message for a missing file or no arguments.

### AC-5: Code and tables don't produce findings (FR-12)
Given a document whose tell words appear only in front matter, a fenced block, inline code, a table and an HTML line
When it is checked
Then no finding is reported, and findings in the prose after them carry their original line numbers.

### AC-6: Each finding kind is detected (FR-13)
Given one small fixture per finding (session reference, bare ID, undefined acronym, long sentence, passive, filler, tell word, tell phrase, em-dash, tricolon, uniform rhythm, repeated opening, over-structured, bold overuse, heading overuse)
When each is checked
Then that finding is reported with the expected kind and line, and a matching clean fixture reports none of that kind.

### AC-7: The JSON report has its fields (FR-14, FR-13)
Given any input
When it is checked with `--json`
Then the output parses and holds the word count, findings, counts, per-1,000-word rates, structure measures, readability information, the detectability score with its diagnostic note, and the sentence-length standard deviation.

### AC-8: Standard library only (NFR-S1)
Given `prose_check.py` and the two vendored scripts
When their imports are read from the AST
Then each import is a standard-library module and none of `socket`, `urllib`, `http` or `subprocess` appears.

### AC-9: Odd input never crashes the checker (NFR-R1)
Given empty input, a single word, CRLF text, text with no sentence punctuation, an all-code file and invalid UTF-8 bytes
When each is checked
Then each exits 0 with a report.

### AC-10: The CLAUDE.md line and clause pass the checker (FR-15, FR-16, FR-19, NFR-P3)
Given the PR's `config/` tree
When `check_config.py --host hosts/personal.json --plugin-index plugins.lock.json --clauses eval/clauses.json` runs
Then it exits 0: `plain-prose` resolves, the new clause's phrase appears in CLAUDE.md, and the token and line limits hold.

### AC-11: The overlay line is present and in budget (FR-18)
Given the `adversarial-reviewer` overlay
When the checker runs
Then the overlay contains "plain-prose" and stays under 700 estimated tokens.

### AC-12: Gates require the right suites (FR-21, FR-22)
Given a branch that changes only the skill's `description`, one that changes only its body, one that changes only `scripts/prose_check.py`, and one that changes only `evals/trigger-evals.json`
When `eval_records.py` works out the required suites
Then the first and fourth require `plain-prose-trigger`, the second and third require `plain-prose-behaviour`, and a suite entry missing `skill` or `cases` is refused with a message.

### AC-13: Trigger scoring (FR-24)
Given a fake `claude` that replays recorded streams
When the trigger runner scores them
Then a top-level `Skill` tool use counts for an ordinary case but not for a delegated one, a subagent's `Skill` tool use counts for a delegated case, two runs make a trigger, and the suite fails when a should-not-trigger query triggers, when a delegated case doesn't, or when fewer than 85% of the should-trigger queries trigger. Overall accuracy is reported but never decides.

### AC-14: Isolation and run limits are proven per run (FR-23)
Given a fake `claude` whose `init` event lists the skill twice in a with-skill run, or lists it in a baseline run
When either runner runs
Then it stops with an isolation error, writes no record, and leaves no temporary directory or credentials copy behind. In every run the fake records, the command has `--tools` with the suite's tool list, `PATH` starts with the stub `gh`, and `GH_CONFIG_DIR` is an empty directory.

### AC-15: Cold-reader scoring (FR-26, FR-27, NFR-R2)
Given faked reader and judge replies
When the cold-reader runner scores a case
Then the case fails if the skill arm gets fewer answers right, drops a `mustKeep` fact, is flagged as AI-sounding when the baseline isn't, or is a write case with no `Skill` tool use. It passes otherwise. A failing case is rerun once and the rerun is counted. An unparsable reply fails the case with the reason recorded.

### AC-16: Diagnostics are recorded but don't decide (FR-29)
Given a cold-reader case that passes and whose text has a high detectability score
When the record is written
Then the record holds the score and rates for that text, and the case still passes.

### AC-17: Case files are well formed (FR-25, FR-28, NFR-S3)
Given the two case files
When the eval-data test runs
Then the trigger file has 25 queries, each with a `kind`: 15 should trigger, 2 of those delegated, at least 3 of kind `code` and at least 1 of kind `cli-ui`; of the 10 that shouldn't, at least 5 are of kind `for-claude` and at least 3 of kind `chat-only`. The behaviour file has at least three write cases and three rewrite cases, each with facts, questions and `mustKeep`.

### AC-18: Upstream changes are reported (FR-30)
Given a temporary home whose marketplace clone has one vendored file changed, one missing and one unchanged
When `lock.py --check` runs
Then it lists the changed and missing files under `upstream` with their problems and leaves out the unchanged file. It exits 0 when the lock and the `vendored` list are clean, still checks `spec_validator.py` against the installed plugin, and treats an entry with neither source pair as a lock input error.

### AC-19: The repo's checks cover the new code (FR-31, NFR-M1, NFR-M2)
Given the PR branch
When ruff, ruff format, mypy and pytest with branch coverage run as the repo's `CLAUDE.md` says
Then all pass, the vendored files are not linted, and `prose_check.py`, `eval/skill_evals.py` and `lock.py` are at 100% branch coverage.

### AC-20: Live evals pass (FR-15, FR-17, FR-24, FR-26, FR-27, NFR-P3)
Given the owner's machine, logged in, on a clean tree at the PR's head
When `TMPDIR=/tmp python3 -I tools/cfg/eval.py` runs the required suites
Then `scenario` passes with no regressions and the four new scenarios passing, `rubric` scores at least 80, and `plain-prose-trigger` and `plain-prose-behaviour` pass. Their records are committed and `eval_records.py verify` exits 0. Every rerun is reported in the PR.

### AC-21: Docs follow the change (FR-20, FR-32, FR-33)
Given the PR diff
When it is reviewed
Then `config/SPEC.md`, the repo `CLAUDE.md`, ADR-0005, ADR-0010 and the plan's Q-9 carry the changes FR-20, FR-32 and FR-33 describe (a review check).

### AC-22: The security audit ran (FR-10)
Given the PR description
When it is reviewed
Then it quotes `skill-security-auditor`'s verdict on the two vendored scripts (a review check).

---

## Edge Cases

- EC-1: The input is empty or has no prose after stripping. The report has 0 words and no findings, and the readability and detectability fields are null. It exits 0.
- EC-2: The file doesn't exist or can't be read. The checker prints a message to stderr and exits 2.
- EC-3: The input isn't valid UTF-8. Bad bytes are replaced and the check goes on.
- EC-4: A code fence is never closed. Everything after it counts as code, so no findings come from it.
- EC-5: An acronym is defined in parentheses later on the same line it first appears ("the CLI (command-line interface)"). It isn't reported.
- EC-6: The marketplace clone doesn't exist. Each marketplace entry is listed under `upstream` as "the marketplace copy is missing", and the exit code depends only on the lock and the `vendored` list.
- EC-7: The owner updates the marketplace and upstream changes a vendored file. `lock.py --check` reports it, and the vendored copy stays as it is until a PR re-imports it.
- EC-8: A trigger run hits `--max-turns` before a subagent loads anything. That run doesn't trigger.
- EC-9: The model refuses or returns an empty text in a cold-reader case. The reader answers "not stated" to everything, so the case fails on comprehension unless the baseline did as badly. In a rewrite case, an empty rewrite also loses every `mustKeep` fact, so it fails.
- EC-10: `claude` isn't on `PATH`, or isn't logged in. Like the existing suites, `eval.py` refuses before any run and writes no record.
- EC-11: A grader reply is wrapped in prose or code fences. The first JSON object in it is parsed. If none parses, the case fails as unparsable.
- EC-12: Text has Windows line endings. Line numbers still match the input.

---

## API Contracts

```typescript
// prose_check.py
// python3 -I <skill>/scripts/prose_check.py <path | -> [--json]
// exit 0 = report printed (whatever it found); 2 = usage error or unreadable file
type Kind = "cold_reader" | "readability" | "ai_tells";
interface Finding { line: number; kind: string; text: string; fix: string }  // line 0 = whole text
interface ProseReport {
  words: number;
  findings: Record<Kind, Finding[]>;
  counts: Record<Kind, number>;
  per_1000_words: Record<Kind, number>;
  structure: { list_line_share: number; bold_per_100_words: number; headings_per_100_words: number };
  readability_info: { flesch_kincaid_grade: number | null; avg_sentence_length: number | null; note: string };
  detectability: { score: number | null; verdict: string | null; note: string };
  sentence_length_stdev: number | null;
}

// lock.py --check --json (FR-30 adds `upstream`; exit code ignores it)
interface CheckResult {
  ok: boolean;                 // lock and vendored only
  lock: string[];
  vendored: { path: string; problem: "plugin <id> isn't installed" | "the installed copy is missing"
                                   | "the installed copy has a different sha256" }[];
  upstream: { path: string; problem: "the marketplace copy is missing"
                                   | "the marketplace copy has a different sha256" }[];
}

// eval/skill_evals.py, called by tools/cfg/eval.py like the other runners
function run_trigger(ctx: Context, suite: Suite): SuiteResult;      // pass, recall, falseTriggers, accuracy (info), cases
function run_cold_reader(ctx: Context, suite: Suite): SuiteResult;  // pass, rerunsUsed, cases
```

## Data Models

### Vendored entry (`config/skills/plain-prose/VENDORED.json`, one per file)

| Field | Type | Constraint |
|---|---|---|
| `path` | string | Relative to the skill directory |
| `upstream.repo` | string | `alirezarezvani/claude-skills` |
| `upstream.commit` | string | 40 hex characters |
| `upstream.path` | string | Path in the upstream repo |
| `marketplace` | string | Directory name under `~/.claude/plugins/marketplaces/` |
| `marketplacePath` | string | Path inside that clone |
| `sha256` | string | Of the upstream file at `upstream.commit` |
| `adapted` | boolean | True only when the local file is meant to differ |
| `licence` | string | `MIT` |
| `licenceFile` | string | `LICENSE-vendored.md` |

### Trigger case (`evals/trigger-evals.json`, a list)

| Field | Type | Constraint |
|---|---|---|
| `query` | string | A realistic user prompt |
| `should_trigger` | boolean | |
| `delegated` | boolean | Optional; true only with `should_trigger` |
| `kind` | `"prose"` \| `"code"` \| `"cli-ui"` \| `"for-claude"` \| `"chat-only"` | The first three should trigger, the last two shouldn't. The runner ignores it; AC-17 checks the mix |

### Behaviour case (`evals/behaviour-evals.json`, a list)

| Field | Type | Constraint |
|---|---|---|
| `id` | string | Unique, such as `write-pr-description` |
| `kind` | `"write"` \| `"rewrite"` | |
| `task` | string | The writing task, or the rewrite instruction |
| `facts` | string[] | What the text should convey; given to the writer, hidden from the reader |
| `draft` | string | Rewrite cases only: the AI-heavy draft, which is also the baseline |
| `questions` | string[] | 3 or 4 fixed cold-reader questions |
| `mustKeep` | string[] | Facts the text must not lose or distort |

### Suite entry (`eval/gates.json`)

| Field | Type | Constraint |
|---|---|---|
| `runner` | `"trigger"` \| `"cold-reader"` | New values |
| `skill` | string | A directory under `config/skills/` |
| `cases` | string | Repo-relative path to the case file |
| `paths`, `selectors`, `harness` | as today | `harness` includes `eval/skill_evals.py` |

---

## Out of Scope

- OS-1: The `writing-docs` skill, its overlays and the `writing-docs` half of the CLAUDE.md line. That is #13.
- OS-2: The lifecycle skill's prose steps and the report-only tell sections in `commit-check` and `pr-body-check`. ADR-0010 puts those in #4.
- OS-3: The CI job that runs `prose_check.py` on commit messages and PR text. That is #10.
- OS-4: The `PostToolUse` hook that checks outgoing `gh` and `git` text (ADR-0010's option D). It waits for eval evidence and an ADR of its own.
- OS-5: Replacing the live prototype in `~/.claude/skills/plain-prose/`. That happens through adoption or deploy (#7, #8), which wait on the held #6. This PR doesn't touch `~/.claude`.
- OS-6: The `work` host profile. That is #17.
- OS-7: Optimising the description automatically with skill-creator's `run_loop`. A hand edit of the description that makes it say what FR-2 asks for is in scope, because the owner chose that over changing the bar (Asked "Which option for #12's trigger suite: A (tune the description to cover coding tasks that touch tests and messages, recommended), B (one counted rerun), C (an 80% bar), or D (rerun as is)?", the owner answered "A".)

---

## Test Plan

This plan follows the overlay for `senior-qa`; the guides' React and Jest examples are translated to pytest.

**Pyramid placement.**

| Level | Criteria | How |
|---|---|---|
| Unit | AC-4 to AC-9, AC-13, AC-15, AC-16 | Pure functions in `prose_check.py` and the scoring functions in `eval/skill_evals.py`, called in-process with small fixture texts and canned stream lines |
| Integration | AC-1, AC-2, AC-10 to AC-12, AC-14, AC-17 to AC-19 | The real files in the tree; the checker, `eval_records.py` and `lock.py` run in-process against temporary homes; the runners run end to end with the existing fake `claude` from `tests/eval_fakes.py` |
| Live, owner's machine | AC-20 | `tools/cfg/eval.py` with the real `claude`; run once, records committed |
| Review checks | AC-3, AC-21, AC-22 | Read in the PR |

**Coverage by risk.** 100% branch coverage is required for `prose_check.py`, `eval/skill_evals.py` and the `lock.py` change. The two runners decide whether a PR may merge, and `prose_check.py` is the skill's only code. The vendored scripts aren't measured, because they're never edited.

**Flake hazards and how they're removed.**
- Model variance: unit and integration tests never call a model. The fake `claude` returns canned streams chosen by prompt. Live variance is handled by the 2-of-3 trigger rule and one counted rerun per case, both reported.
- Wall-clock time: no test asserts a duration. NFR-P2 is measured once and quoted.
- Temporary directories and environment: each test gets `tmp_path` and `monkeypatch` for `HOME`, `TMPDIR` and `PATH`. A teardown check confirms no `eval-*` directory or credentials copy is left.
- Thread order: the runners may use a thread pool, so results are sorted by case ID before scoring and writing.

**Isolation and fixtures.** Fixture texts live in `config/skills/plain-prose/tests/fixtures/`, one small file per finding kind plus clean twins. Canned streams and grader replies are built by small factory functions in `tests/eval_fakes.py`, which grows a `stream(skill=…, parent=…)` helper. Temporary homes for `lock.py` reuse the helpers in `tests/test_lock_check.py`.

**Behaviour over mocks.** Tests assert the report a user sees (kinds, lines, exit codes) and the suite result a record holds, not which internal function ran. The only fake is the `claude` executable at the process boundary.

**Existing tests changed.** `tests/test_vendored.py` stays for `tools/vendor/`. A parallel test for the skill's `VENDORED.json` lives in the skill's `tests/`. `tests/test_lock_check.py` gains the marketplace cases. `tests/test_eval_records.py` and `tests/test_eval_cmd.py` gain the two runners. `tests/test_repo_config.py` keeps passing with `plain-prose` added to the host profile. No test is deleted.

**Test locations.** The skill's own tests go in `config/skills/plain-prose/tests/` and deploy with it, like the lifecycle skill's tests ([ADR-0002](../../../docs/adr/0002-repo-layout.md)). They run as `.venv/bin/python -I -m pytest config/skills/plain-prose/tests --cov=config/skills/plain-prose/scripts --cov-branch`. The runner, `lock.py` and gates tests go in `tests/`.
