# Reader-first writing: the nine steps in detail

This guide expands the nine steps in `SKILL.md`, each with a weak and a stronger example. A last section shows how the steps shrink for short texts such as commit messages and code comments. Read the step you're unsure about; nobody needs the whole guide every time.

The examples share one invented system: a nightly job that backs up a database to an archive and can restore it.

## 1. Name the reader and their task

Decide who opens this and what they need to do or decide when they do. The opening lines should let that reader tell, in a few seconds, whether they're in the right place.

Weak:

> This document describes the backup subsystem.

Stronger:

> Use this page when the nightly backup has failed and you need to restore yesterday's data. It assumes shell access to the database host.

## 2. Write one kind of document

The Diátaxis framework names four kinds, and each has a different reader in a different mood:

| Kind | The reader wants to | Shape |
|---|---|---|
| Tutorial | learn by building something small | numbered lessons that always work |
| How-to | finish one real task | steps, a check, and what to do when it fails |
| Reference | look up one exact fact | tables and lists, no story |
| Explanation | understand why | prose, trade-offs, history |

Weak: a README that starts as a tutorial, turns into a list of every flag halfway down, and ends with the design history.

Stronger: a short README that links to a how-to for restores, a reference page for the flags, and an explanation of why archives are kept for seven days.

## 3. Put the answer first

The reader came for the command, the decision or the result. Background goes after it, or behind a link.

Weak:

> Since the 2025 migration, archives have been stored differently, and after several incidents the team decided that…

Stronger:

> To restore last night's archive, run `backup restore --latest`. The rest of this page covers older archives and what to do when the restore fails.

## 4. Be concrete and runnable

Use the real command, path and value, show what success looks like, and give an example before the general rule.

Weak:

> Run the restore command with the appropriate options.

Stronger:

```bash
backup restore --date 2026-10-16 --target /srv/db
```

> It prints `restored 42 tables` and exits 0. If it prints `archive not found`, list what exists with `backup list`.

## 5. Use plain language

Define each term the first time it appears. Never leave a requirement or decision ID on its own; say what it requires, and link to where it's defined.

Weak:

> Per FR-12, the RPO is 24h.

Stronger:

> The backup runs once a day, so at most a day of data can be lost (the recovery point objective, or RPO, which the backup spec sets).

## 6. Give the structure meaning

Headings should read like the reader's tasks or questions. Number a list only when the order matters. Use a table to compare things, and add a diagram only when it shows how parts connect, with a sentence saying what it shows.

Weak headings: "Overview", "Details", "Miscellaneous".

Stronger headings: "Restore last night's archive", "Restore an older archive", "When the restore fails".

## 7. Keep it maintainable

Link to the source of truth instead of copying it, so the copy can't go stale. Date decisions and anything that changes over time. When a document for the purpose already exists, update it instead of writing a near-duplicate.

Weak: a table of every flag pasted from `backup --help`, which stops matching the tool after the next release.

Stronger:

> For every flag, run `backup --help`. The flags most restores need are `--date` and `--target`.

## 8. Check it

Run every command in the document, or mark the ones you didn't run. Then run the lint:

```bash
python3 -I <this skill's base directory>/scripts/doc_lint.py docs/restore.md
```

Fix each structure and link finding, and each prose finding that would confuse or distract a reader. The lint only reports; it never blocks.

For a substantial document, run a fresh-reader check. Start a subagent with no context and give it only the document. Ask one question per task the document promises, such as "Using only this page, how would you restore the archive from 16 October, and how would you know it worked?" Where it guesses, or answers "not stated", the document has a gap.

## 9. Revise under plain-prose

Read the whole text again under the `plain-prose` rules, including tables, lists and captions. Cut filler and hedges, replace inflated words, and vary sentence length. Keep a precise technical term when it is the reader's own word.

Weak:

> It's worth noting that this robust solution seamlessly handles restores.

Stronger:

> Restores of up to 50 GB finish in about ten minutes.

## Short texts

Commit messages, issue and PR text, review notes, code comments, docstrings and messages in code get the steps that fit their length: name the reader and what they need, put the answer first, be concrete, define terms, and link to the source of truth. They don't get a kind, a template or the lint; `plain-prose`'s procedure is their check.

A commit message. Weak:

```text
fix stuff in backup
```

Stronger:

```text
fix(backup): retry a failed upload three times before giving up

The nightly job failed whenever the storage service dropped one request,
which happened about once a week. Three retries with a 30 s wait cover
every failure in the last month's logs.
```

A PR description. Weak: "Updates the backup code. See commits."

Stronger: "The nightly backup now retries a failed upload three times, so a single dropped request no longer fails the whole job. Tested by forcing two failures in a row on staging; the third attempt succeeded."

A code comment. Weak:

```python
# loop 3 times
for attempt in range(3):
```

Stronger:

```python
# The storage service drops about one request a week; three tries cover every
# failure seen so far without hiding a real outage for long.
for attempt in range(3):
```
