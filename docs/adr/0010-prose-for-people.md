# ADR-0010: Prose a cold reader understands (a `plain-prose` skill for all human-facing text, and a `writing-docs` skill for documentation)

**Status:** Accepted by the owner on 2026-10-16 (proposed 2026-10-16).

Everything Claude writes for people should be easy to follow for a reader with no context, and free of the patterns that mark text as AI-written. A small `plain-prose` skill holds those rules for every kind of prose, from commit messages to chat replies. A second skill, `writing-docs`, adds the steps specific to documentation. One line in the global CLAUDE.md says when to load `plain-prose`, and the lifecycle skill and `writing-docs` call it at the points where they produce prose.

Prototypes of both skills were installed in `~/.claude/skills/` on 2026-10-16 so they could be used before this repo exists. The skills built in plan issues 12 and 13 will replace them.

Terms such as overlay, the lifecycle skill, host profile, scenario eval, eval record, clause inventory and the v2 spec are defined in the [Terms section of the architecture doc](../architecture/README.md#terms).

## Context

The owner asked for two things on 2026-10-16. First, Markdown that Claude writes as documentation (READMEs, pages under `docs/`, ADRs, architecture docs, runbooks, how-to guides) should be friendly and useful to people. Second, all prose Claude writes for people should avoid sounding AI-generated. The second request is broader than documentation: it covers issue descriptions, comments, PR titles and descriptions, commit subjects and bodies, review notes, and similar text. Most of that text is written through `gh` and `git` during ordinary repo work, usually with no documentation skill loaded.

The purpose is understanding. AI tells make text harder to read. Inflated words hide the plain meaning, hedges and filler bury the point, and formulaic structure makes every paragraph look alike, so the important one doesn't stand out. The reader to design for is the owner months later, opening an issue, a commit or a doc with none of today's context. Success means that reader can tell what the text says, why, and what to do next.

Detectability counts as a cost too. The owner pointed out that readers who know AI writing spot its patterns and find them hard to ignore. Once they notice one, they start reading for tells instead of for meaning. For that reason, the rules that make text harder to detect as AI-written (sentence rhythm, list overuse, formulaic structure) carry the same weight as the plain-clarity rules. Both serve the same goal: a reader who stays on the content.

These existing skills were considered:

| Candidate | What it does | Fit |
|---|---|---|
| `marketing-skills:content-humanizer` (claude-code-skills marketplace, not installed) | Three modes. Detect: `references/ai-tells-checklist.md` lists overused words, hedges, and structural, punctuation and tone tells. Humanize: remove the patterns and fix rhythm. Voice injection: add brand personality. | Detect and humanize fit. Voice injection doesn't, because issues, commits and docs need clarity, not personality. |
| `marketing-skills:copy-editing` (not installed) | `scripts/ai_content_detector.py` (sentence-length variance, vocabulary diversity, known AI phrases; standard library only, `--json` output); `scripts/readability_scorer.py` (Flesch–Kincaid grade, passive voice, filler words, long sentences); `references/plain-english-alternatives.md`; marketing-oriented editing passes | The readability scorer, the plain-English list and the detector fit. The detector's score measures how AI-like the text reads, which matters here because AI-familiar readers notice it. The editing passes don't fit. |
| `behuman` (not installed) | Conversational tone for emotional chats | No. Its own description says it isn't for technical content. |
| `markdown-html:md-document` (not installed) | Renders Markdown as styled HTML | No. It presents documents; it doesn't write them. |
| `runbook-generator`, `changelog-generator`, `codebase-onboarding` (installed) | One document type each, from templates | Partly. They say nothing about the reader or about AI tells. |
| `anthropic-skills:setup-writing-style` (synced from claude.ai) | Captures the owner's personal voice for text sent as the owner | No. It explicitly excludes Claude's own writing. |

The two useful skills ship only inside the `marketing-skills` plugin, which bundles 48 skills under the MIT licence, at version 2.9.0 and marketplace commit `19392f7`. That is the same commit as the owner's installed engineering plugins. The marketplace offers neither skill on its own. Installing the whole bundle would add about 5,800 tokens of skill descriptions to every session (measured across the 48 descriptions), four times the size of the global CLAUDE.md. It would also put 46 marketing skills in competition for prompts such as "polish this" or "make this better".

## Options

### Where the guidance lives

| Option | For | Against |
|---|---|---|
| A. Inside `writing-docs` only | One skill | Misses issues, comments, PRs and commits, which are the most frequent prose. Doesn't cover the owner's scope. |
| B. The full checklist in the global CLAUDE.md | Always present | About 1,500 tokens in every session, including sessions that write no prose at all. Prose rules in CLAUDE.md are the failure mode the v2 spec documented: Claude treats them as advice and skips them. |
| C. A small `plain-prose` skill holding the checklist, word list and checker; one CLAUDE.md line saying when to load it; the lifecycle skill and `writing-docs` calling it at their prose steps | A broad trigger costs one short line. The detail loads only when needed and stays loaded for the rest of the session. Wherever a skill controls the prose step, the call is deterministic. | One more skill. The CLAUDE.md line is a new rule, so it needs a clause-inventory row and new scenarios. |
| D. A hook on `gh` and `git` commands that checks the outgoing text | Deterministic | A check after the tool call fires once the issue, comment or PR is already posted and notifications have gone out. A check that denies the call on tells would block work on false positives, against the v2 rule that hooks never block (v2 spec FR-21). Bodies passed with `--body-file` would mean reading files from inside the hook. |

### How the marketplace material gets in

1. Install `marketing-skills`, plus an overlay limiting `content-humanizer` to its detect and humanize modes.
2. Point at the marketplace clone in `~/.claude/plugins/marketplaces/`.
3. Vendor the pieces that fit: copy them in, adapt them, pin them to a commit and attribute them.

## Decision

The repo takes option C for placement and option 3 for the material, and keeps option D as a fallback to be triggered by eval results.

### Skill 1: `plain-prose`, for any prose written for people

The skill lives in `config/skills/plain-prose/` with a `SPEC.md` beside its `SKILL.md`, because new skills always go through spec-driven-workflow. It deploys on both host profiles.

Its scope is any text Claude writes for a person to read:

- issue titles and bodies, including the prose of specs stored on issues;
- issue and PR comments, PR titles and descriptions, commit subjects and bodies;
- review notes and adversarial-review write-ups;
- documentation, artifact pages and release notes;
- messages drafted for the owner to send;
- chat replies to the owner. The owner decided this on 2026-10-16, expecting it to save tokens over time, because tighter replies cost less to read and to carry forward in context.

It doesn't cover text written for Claude (`CLAUDE.md`, `SKILL.md`, overlays, memory, subagent prompts), which follows its own style rules (v2 spec FR-16). Nor does it cover code, identifiers or machine-read formats.

`SKILL.md` stays short and states the goal, the rules below and a three-step procedure. Write for a reader with no context. Run `scripts/prose_check.py` on anything longer than a few sentences. Fix the findings that make the text harder to understand, and leave the rest. Short texts, such as commit subjects and PR titles, get the rules without the script.

The cold-reader rules come first, because they follow directly from the goal. The first line of an issue, PR, commit body or comment says what happened or what is needed. Commit bodies and PR descriptions explain why, and which alternative was rejected, since the diff can't show that. Nothing should depend on context the reader won't have. Phrases such as "as discussed", "per above", "the C1 fix" or "the earlier approach" mean nothing later. The text names the issue number, file, command, error message or decision instead. Terms are defined and IDs spelled out on first use, because a bare `FR-23` or `W2` means nothing months on. Concrete values beat abstract ones: "from 4 s to 300 ms" says more than "faster". Each paragraph carries one idea.

The rules against AI tells are adapted from the checklist's detect and humanize modes. Each one either hides the meaning or distracts a reader who recognizes it:

- inflated vocabulary such as "delve", "leverage", "seamless", or "robust" with no stated property: use the plain word from the vendored plain-English list;
- reflexive hedges and filler such as "it's worth noting", "it's important to note" or "basically": cut them;
- a concluding paragraph that repeats the text: cut it;
- triplets and parallel lists added for cadence: keep only the items that carry information;
- "not X, but Y" framing and colon-then-reveal sentences: state Y directly;
- false warmth and artificial enthusiasm: cut them;
- em-dashes and bold: use them sparingly;
- uniform rhythm: vary sentence and paragraph length the way a person writing naturally would, and avoid runs of same-shaped sentences or paragraphs that open the same way;
- over-structuring: use lists and tables for content that really is list-shaped (steps, options, comparisons, inventories), and prose for reasoning, causes and trade-offs. Don't turn full-sentence thoughts into bullets, put a heading on every short section, pad sections to matching lengths, or bold every other phrase;
- template openings and closings: no "Great question", "In summary" or "Let me know if…", and no "Overview, Details, Conclusion" scaffold on every section.

Comprehension wins over every rule. Next come technical precision ("ensure" for a real guarantee, "framework" when naming one) and the reader's own terms. Formats other tools require also win, such as Conventional Commit syntax, the lifecycle skill's spec markers and the `Closes #N` line.

`scripts/prose_check.py` uses only the standard library and is invoked as `python3 -I <absolute path> [file|-]`. It strips fenced code, inline code, tables and front matter, then reports four kinds of finding, each with a line number where one applies. It never blocks.

- **Cold-reader findings:** references to session context ("as discussed", "above", "earlier"), bare requirement or finding IDs, and acronyms used before they are defined.
- **Readability findings,** from the vendored `readability_scorer.py`: sentences over about 30 words, passive voice and filler words. It also reports the Flesch–Kincaid grade, as information only, because technical vocabulary inflates it.
- **AI-tell findings:** checklist words and phrases; rhythm (sentence-length variance, repeated paragraph openings); and structure (the share of text in lists, bold density, headings per 100 words).
- **A detectability score** from the vendored detector, based on sentence-length variation, vocabulary diversity and phrase density. It is a diagnostic only.

The vendored files sit under `references/vendored/` and `scripts/vendored/`. A `VENDORED.json` records each file's source path, marketplace commit and sha256, plus the MIT licence text. Each file is first committed unchanged, so every adaptation shows up as a reviewable diff.

| Upstream file | Adaptation |
|---|---|
| `content-humanizer/references/ai-tells-checklist.md` | Keep categories 1 to 5. Allow technical senses of flagged words. Drop the brand-voice remedies, but keep the rhythm and structure advice. |
| `copy-editing/references/plain-english-alternatives.md` | Used as is |
| `copy-editing/scripts/readability_scorer.py` | Called by `prose_check.py` on the stripped prose. The marketing thresholds are replaced with ones suited to technical prose (the long-sentence limit, the passive share). The grade is information only. |
| `copy-editing/scripts/ai_content_detector.py` | Both the phrase list and the score are used, on the stripped prose. The score is a detectability diagnostic, never a gate. |

The upstream checklist goes stale; its own header says to re-validate it about every six months. `cfg check --live` compares each vendored file's hash with the local marketplace clone and reports any upstream change as a prompt to review, not as a failure. The behaviour evals show whether a refreshed list still helps.

### How `plain-prose` gets used

| Path | Mechanism | Deterministic? |
|---|---|---|
| Any session, any prose | One new line in the global CLAUDE.md, about 40 tokens, under the git or authoring rules: "Before writing prose for people (issues, comments, PR titles and bodies, commit messages, review notes, docs), load `plain-prose`: write it so a reader with no context understands it." It gets a new `claude-md` row in the clause inventory, citing the owner's decision. The v2 spec allows a routing line when the user picked the skill (FR-6). The line isn't emphasised, because the rule of a single emphasised line stays. | No; the scenario eval measures it |
| The lifecycle skill (issues, comments, PRs, commits) | Folded into the amendment of the lifecycle skill's spec in plan issue 4. Its `SKILL.md` says to apply `plain-prose` before each `gh issue create/edit/comment`, each `gh pr create/edit` and each commit. `issue_lifecycle.py commit-check` and `pr-body-check` gain a report-only section listing tells from the vendored checklist. The script's rules (standard library only, no network) don't change. | The check runs as a script; the rewrite is Claude's |
| `writing-docs` | Its last procedure step is `plain-prose` | Yes, within the skill |
| `adversarial-reviewer` | The existing overlay gains one line: review notes and the findings in PR descriptions follow `plain-prose` | Yes, injected by the overlay hook |
| This repo's CI | The `commits` job runs `prose_check.py` on commit messages and on the PR title and body (from the event payload). It only reports, writing to the job summary. | Yes, but it only warns |

The hook from option D is held in reserve. If the scenario eval shows Claude writing issues, comments or commits full of tells despite the CLAUDE.md line and the lifecycle steps, a later ADR adds a `PostToolUse` hook on `Bash`. It would match `gh issue|pr create|edit|comment` and `git commit`, run `prose_check.py` on the text, and inject the findings as `additionalContext`, so Claude edits the issue or amends the unpushed commit. It wouldn't block, and it would have to meet the hook review bar in [ADR-0007](0007-visibility-and-security.md). It isn't built now because it adds code to every Bash call, and it can only correct text after it's posted.

Early evidence argues against relying on instructions alone. On 2026-10-16, three subagents were each told explicitly to load `writing-docs` and `plain-prose` with the Skill tool before rewriting these architecture docs. All three loaded `writing-docs`, and none loaded `plain-prose` until reminded. The likely cause is that the subagents inherited a conversation where `plain-prose` was already loaded, though this wasn't confirmed. The trigger eval for `plain-prose` therefore includes delegated work: a subagent or fork asked to write prose must load the skill itself. The repo's own prompts to subagents name the skill and the reason for loading it.

### Skill 2: `writing-docs`, for documentation at rest

The skill lives in `config/skills/writing-docs/` with a `SPEC.md` beside its `SKILL.md`, and deploys on both host profiles. It covers Markdown documents for people: READMEs, everything under `docs/`, ADRs, architecture docs, runbooks, how-to guides, tutorials, setup guides and changelog prose.

Its procedure has nine steps:

1. Name the reader and their task. Who reads this, and what do they need to do or decide? The opening lines say what the document is for.
2. Write one kind of document per file: a tutorial, a how-to, a reference or an explanation (the four kinds from the Diátaxis framework), each with its own structure.
3. Put the answer first: the summary, the decision or the command.
4. Be concrete and runnable: real commands, paths and values, expected output where it helps, and an example before the general case.
5. Use plain language. Define terms on first use. A requirement ID such as `FR-23` never stands alone; say what it means.
6. Give the structure meaning: headings shaped like the reader's tasks, numbered lists only for real sequences, tables for comparisons, and a diagram only when it shows how something works.
7. Keep it maintainable: link to the source of truth, date decisions, keep one document per purpose, and update rather than duplicate.
8. Check it. Run the commands, or mark them as not run. Make sure links resolve and `scripts/doc_lint.py` is clean. For a substantial document, a fresh-context reader must be able to answer "can I now do X?" from the document alone.
9. Apply `plain-prose`, so the document reads clearly to someone without context.

`scripts/doc_lint.py` uses only the standard library and only reports. It looks for a missing title or purpose near the top, skipped heading levels, and links or anchors that don't resolve. It also flags code fences without a language, acronyms used before they are defined, bare requirement IDs, and leftover `TODO` or `TBD` markers. It calls `plain-prose`'s `prose_check.py` for the prose findings.

Two overlays chain the skill in, one line each. The overlays for `engineering-skills:senior-architect` and `engineering-advanced-skills:runbook-generator` tell Claude to load `writing-docs` before writing the document file.

### Evals

These suites run locally and are gated as described in [ADR-0005](0005-verification-and-ci.md).

The scenario eval for the CLAUDE.md change adds four scenarios for the new rule:

- "write the PR description for this diff";
- "comment on #12 that the fix is merged";
- "write the commit message for this change";
- "draft the issue for this bug report".
 In each, Claude must load or apply `plain-prose`. Scenarios that pass under the old CLAUDE.md must still pass.

The main grade is comprehension by a cold reader. A fresh-context grader stands in for the owner months later. It gets only the produced text, with the underlying facts held back for the grading step. It answers fixed questions per scenario: what changed or is needed, why, what to do next, and what the risk is. Then it answers one more question as a reader familiar with AI writing: did anything read as AI-generated, and did it distract? It quotes the passage. A scenario passes on three conditions. The new version answers at least as many content questions correctly as the old one. It loses or distorts no fact. And it is flagged as AI-sounding no more often.

`prose_check.py`'s counts (findings per 1,000 words in each category) and the detectability score go into the eval record as diagnostics only, so trends stay visible.

`plain-prose` also gets its own trigger and behaviour suites. It must trigger on prose tasks, and not on code or CLAUDE.md edits. Its rewrites must keep or raise cold-reader comprehension, keep every fact, and draw fewer AI-sounding flags. Fewer findings with worse comprehension doesn't count as a pass. `writing-docs` gets trigger and behaviour suites graded by a fresh-context reader against the nine steps above. The lifecycle skill's behaviour suite re-runs, because its `SKILL.md` changes in plan issue 4.

## Consequences

The global CLAUDE.md grows by one line, about 40 tokens, which keeps it well under its 1,400-token target. The PR that adds the line needs the clause-inventory row, the new scenarios and a scenario-eval record.

The amendment of the lifecycle skill's spec in plan issue 4 now also covers the prose steps and the two report-only checks. It still returns that spec to draft, and it goes through the approval mode the owner chose for it.

Once loaded, `plain-prose` stays in context for most working sessions. Its `SKILL.md` therefore has to stay small, about 800 tokens. The full checklist loads only when `prose_check.py` reports something or Claude needs the detail.

This architecture was first drafted before either skill existed. The drafts were rewritten for a cold reader with the 2026-10-16 prototypes.

## Revisit when

- Evals or transcripts show issues, comments or commits full of tells despite the CLAUDE.md line and the lifecycle steps. Take option D through its own ADR.
- `content-humanizer` or `copy-editing` becomes available as a standalone plugin, or Claude Code allows enabling single skills from a bundle. Install it then, replace the vendored copies with an overlay, and drop `VENDORED.json`.
- The evals show a rule hurting understanding without reducing AI-sounding flags; for example, a list turned into prose that is harder to follow. Narrow that rule. When readability and detectability pull in different directions, the cold reader's content questions decide.
- The readability findings misfire on technical prose, such as passive voice in specs or long sentences listing exact values. Tune the thresholds, or drop the finding.
- An upstream skill appears that covers reader-first technical writing. Prefer it, plus an overlay, over `writing-docs`.
