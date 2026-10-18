---
name: plain-prose
description: Makes text people will read later clear to a reader with no context and free of the patterns that mark it as AI-written. Load it once per session, before the first such text: commit messages, issue and PR text, review notes, docs, release notes, messages drafted for the user to send, code comments and docstrings, assertion, error and log messages, and text a CLI or web page will show. Most coding work writes some of this, so load it before coding that writes or changes tests (assertion messages), adds validation or error handling (error messages), adds logic worth a comment or docstring, or edits CLI help, CLI output or UI strings. Its rules then cover every later such text; if its instructions are already in the conversation, don't invoke it again. Also use when the user says text is hard to follow or sounds like AI. Not for the chat reply itself, code itself, identifiers, machine-read formats, or files written for Claude (CLAUDE.md, SKILL.md, skill overlays, memory, subagent prompts).
---

# Plain prose

Write so that someone opening the text months from now, with none of today's context, understands it on the first read. Removing AI tells serves that goal: readers who know AI writing notice its patterns and start reading for tells instead of meaning.

## Load once

These rules cover every text a person will read later, for the rest of the session: code comments, docstrings, assertion and error messages, CLI and web page text, commits, issue and PR text, and docs. The chat reply itself is out of scope. While the block starting `Base directory for this skill: …/plain-prose` is in the conversation, don't load the skill again; reload only if it's gone, for example after compaction. Asked whether the rules were followed, point to the checks you ran.

## For a cold reader

- Lead with the point: what happened or what is needed, then why. A diff shows what changed; only prose can say why, and which alternative was rejected.
- Assume nothing from the conversation. "As discussed" or "the earlier fix" mean nothing later; name the issue, file, command, error or decision.
- Spell out an acronym or a requirement ID the first time; a bare `FR-23` needs a few words saying what it requires.
- Prefer concrete values: "from 4 s to 300 ms", not "faster". One idea per paragraph.

## Against AI tells

- Plain words: "use", not "leverage" or "utilize". No "delve", "seamless", or "robust" without a stated property.
- Cut hedges and filler ("it's worth noting", "basically"), restating conclusions, false warmth, and stock openers or sign-offs ("Great question", "Let me know if…").
- Keep only list items that carry information; no triplets for cadence. State Y directly instead of "not X, but Y".
- Use em-dashes and bold rarely. Vary sentence and paragraph length, and don't open paragraphs the same way.
- Lists and tables for list-shaped content; prose for reasoning, causes and trade-offs. No heading on every short section.

The full lists are in `references/vendored/ai-tells-checklist.md` and `references/vendored/plain-english-alternatives.md`. Read them when the checker flags something and you need the detail.

## Precedence

Comprehension comes first. Then technical precision ("ensure" for a real guarantee is fine), the reader's own terms, and formats other tools need, such as Conventional Commit syntax, spec markers and `Closes #N`. If removing a tell makes the text harder to follow, keep the clearer version.

## Procedure

1. Write for a reader with no context.
2. For anything longer than a few sentences, save it to a file (or pipe it) and run:
   `python3 -I <this skill's base directory>/scripts/prose_check.py <file or -> [--json]`
3. Fix the findings that would confuse or distract a reader. Ignore those that wouldn't, such as passive voice where the actor doesn't matter.
4. Stop. The detectability score and reading grade are diagnostics; don't chase them.

Short texts, such as commit subjects and PR titles, get the rules without the script.
