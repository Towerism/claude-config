# Adversarial review note: jira-ticket-lifecycle SPEC.md (one pass, 2026-10-20)

This note records the single adversarial review of the first draft of `SPEC.md` for #16 and how each finding was handled. Read it alongside the spec before approving it.

Approval mode: manual review with an adversarial pass, set by the owner on 2026-10-16 for every plan issue ("work on the implementation plan. manual review + adversarial. work on each one sequentially.").

**Verdict: BLOCK** (2 CRITICAL, 7 WARNING, 3 NOTE). Both CRITICAL findings and every WARNING are fixed in the spec, so per the one-pass rule the review was not run again. The spec still needs the owner's approval.

**How it ran.** One pass with `engineering-skills:adversarial-reviewer` and its three personas: Saboteur, New Hire and Security Auditor. It ran inline in the fork that wrote the spec, because a fork can't start subagents. That makes it a same-context self-review, weaker than a fresh-context pass; the owner's review is the check on what it missed.

**Validator:** `spec_validator.py --strict` scored 100/100 before and after the fixes, with no warnings.

## Findings

| ID | Severity | Persona | Finding | Handling |
|---|---|---|---|---|
| C1 | CRITICAL | Security Auditor, Saboteur | The draft had the skill run `search --text "<words from the prompt>"` and `approve --quote "<the user's words>"`, and pass the ticket summary to `branch-name --title`. Every Bash command is a shell string, so `$(…)` or backticks inside those double quotes would run. That is the injection ADR-0011 tells this skill to fix, reached through the user's prompt or through a ticket summary anyone in the project can edit. | **Fixed.** FR-28 now says any text from the user or a ticket reaches the script only through a file written with the Write tool, and command lines hold only fixed words, checked keys, numbers, printed URLs and paths. FR-8, FR-15 and FR-18 use `--text-file`, `--quote-file` and `--title-file`. AC-20 adds the summary case and checks that no Bash command holds the text, and EC-26 is new. |
| C2 | CRITICAL | Saboteur | The draft stored a hash of the description text the script *wrote* and compared it with the text *read back*. The Context section itself says those never match, so every sync after the first would be refused, and AC-11's second run couldn't pass as written. | **Fixed.** FR-12 and FR-13 step 5 now hash the normalised text read back right after each sync, and Data Models defines the normalisation. AC-11 checks that a run after a sync with no edit isn't refused. |
| W1 | WARNING | Saboteur | With no `synced.json` record (a new machine, or lost local state), the draft didn't say what `sync-spec` does when the description already holds the markers. It could overwrite someone's edits or refuse forever. | **Fixed.** FR-13 step 1: no record plus markers means `refused` with the same question; no record and no markers means an original description. `pull-spec` writes the record (FR-12). AC-11 covers it. |
| W2 | WARNING | Saboteur, New Hire | The working copy's path used the basename of the repo's top-level directory, which differs between the main checkout and a worktree, so a spec written in one wouldn't be found in the other. | **Fixed.** FR-12 takes the name from the common git directory, so every checkout of a repo shares one path. EC-25 is new. |
| W3 | WARNING | Security Auditor, Saboteur | `label-actor` read the changelog without saying it is paginated. Reading only the first page could miss a newer label change by someone else and trust their `spec-approved`. | **Fixed.** FR-11 requires every page, and a failed page means the label isn't trusted. EC-24 is new. |
| W4 | WARNING | Saboteur | `claim` assigned the ticket unconditionally, while the skill was only told to ask first. In a shared team project, a skipped question takes someone else's ticket. | **Fixed.** FR-10: `claim` refuses a ticket assigned to someone else, or in the done state or later, unless `--take-over` or `--reopen` is passed, which the skill passes only on the user's word. AC-8 covers it. |
| W5 | WARNING | Security Auditor | The draft said search text never goes into JQL, but the jira CLI puts its free-text argument into a JQL text clause itself, so quotes in the text could change the query. | **Fixed.** FR-28 has the script escape `\` and `"` in search text. AC-20 checks the escaping. |
| W6 | WARNING | Saboteur | The 32,649-character limit was measured on the Markdown sent, but Jira's limit applies after conversion, so a spec just under it could still be rejected. A failed pointer write after a successful upload was also unspecified. | **Fixed.** FR-13 step 4 falls back to the attachment on Jira's size error, and keeps older attachments when the pointer write fails. |
| W7 | WARNING | New Hire | AC-2 had no test: "every project key … is one of the invented values" named no check that could run in this repo. | **Fixed.** AC-2 now scans the package for ticket keys, URLs and email addresses and allows only the invented ones; the excluded-terms scan stays on the work machine. |
| N1 | NOTE | New Hire | Several requirements say "as GH FR-n does", so a reader needs the GitHub spec open too. | Listed, not fixed: copying those rules would let the two specs drift, and OS-4 already leaves shared code for later. |
| N2 | NOTE | New Hire | The draft didn't say that labels Claude adds count as the owner's, because Claude uses the owner's token, so `label-actor` guards only against other people. | **Fixed** with one sentence in FR-11. |
| N3 | NOTE | New Hire | A `summaryFormat` using `{repo}` with no `repo` in the `create` input was undefined. | **Fixed:** exit 2 (FR-4's settings table). |
| — | WARNING, accepted | Security Auditor | `LIFECYCLE_JIRA_REST` lets an environment variable send the token to any port on `127.0.0.1`. | **Accepted:** anything that can set this session's environment can already read `JIRA_API_TOKEN` from it, so the seam adds no new exposure. It accepts only loopback HTTP origins, and every other value is refused (FR-6). |

## Decisions for the owner

The spec lists four decisions in its last section, each with a recommendation: D-1 (plain-text `spec:start`/`spec:end` markers), D-2 (a local working copy under `~/.local/state/claude-config/jira-specs/`), D-3 (two read-only REST calls beyond ADR-0011, with an ADR amendment), and D-4 (deploy the skill on the personal machine too).
