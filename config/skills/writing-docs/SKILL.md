---
name: writing-docs
description: Guides writing any text a person will read later, so a reader with no context can act on it. Documents (README, docs/ page, architecture doc, ADR, runbook, how-to, tutorial, changelog, release notes), new or edited, get reader-first steps, templates and a lint. Short texts get the steps that fit them, such as commit messages, issue and PR text, review notes, code comments, docstrings, assertion and error messages, and CLI or UI text. Load it before coding that writes or changes such text too, such as tests (assertion messages), validation or error handling, logic worth a comment, or CLI or UI strings. Use it whenever the user asks to document a system, process or decision, even without the word "doc". Loads plain-prose first. Load it once per session, before the first such text, and not again while its instructions are in the conversation. Not for the chat reply, code itself, identifiers, machine-read formats, or files written for Claude (CLAUDE.md, SKILL.md, skill overlays, memory, subagent prompts).
---

# Writing docs

A text succeeds when a reader with no context, often the owner months later, can use it to do or decide something.

## Load once

These steps cover every text a person will read later, for the rest of the session: documents, commit messages, issue and PR text, review notes, code comments and docstrings, and messages or strings in code. The chat reply itself is out of scope. While the block starting `Base directory for this skill: …/writing-docs` is in the conversation, don't load the skill again; reload only if it's gone, for example after compaction.

## First, plain-prose

Before reading sources or drafting, load the `plain-prose` skill with the Skill tool, unless a block starting `Base directory for this skill: …/plain-prose` is already in the conversation. Its rules must be in context while you draft, not applied afterwards.

## The nine steps

`references/reader-first.md` has a good and a bad example for each.

1. **Reader and task.** Name who reads this and what they need to do or decide. The opening lines say what the text is for.
2. **One kind of document.** A tutorial, a how-to, a reference or an explanation, never a mix. Link between them.
3. **Answer first.** The summary, the decision or the command comes before the background.
4. **Concrete and runnable.** Real commands, paths and values, expected output where it helps, an example before the general rule.
5. **Plain language.** Define terms on first use. A requirement ID never stands alone; say what it requires.
6. **Structure that carries meaning.** Headings shaped like the reader's tasks, numbered lists only for real sequences, tables for comparisons, a diagram only when it shows how something works.
7. **Maintainable.** Link to the source of truth instead of copying it, date decisions, and update the existing document instead of adding a near-duplicate.
8. **Checked.** Run the commands, or mark them as not run. Run the lint below. For a substantial document, give a fresh subagent only the document and ask "can I now do X?"
9. **Plain prose.** Revise the whole text under the `plain-prose` rules, tables and lists included.

## Short texts

For a commit message, issue or PR text, a review note, a code comment or docstring, or a message or string in code, apply the steps that fit that length: name the reader and what they need, put the answer first, be concrete, define terms, and link to the source of truth. Skip choosing a kind, the templates and the lint; `plain-prose`'s procedure is the check.

## Templates

For a document file, start from `references/templates/<kind>.md`: readme, how-to, tutorial, reference, explanation, adr or changelog. Every placeholder is a `TODO:` marker, so the lint finds any left behind. When another loaded skill, such as `senior-architect` or `runbook-generator`, brings its own template, that template wins on structure and these steps still apply.

## Gate

No version of a document file goes to a reviewer, a commit, a PR or the user until the lint has run on that version, and again after every revision:

`python3 -I <this skill's base directory>/scripts/doc_lint.py <file.md or -> [--json]`

It reports structure and link problems and includes `plain-prose`'s report. Fix each finding that would confuse or distract a reader. It only reports, so it never blocks.

## Report

Say which documents you checked, and list any findings you left as they are, with a reason.
