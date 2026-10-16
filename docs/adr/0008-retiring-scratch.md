# ADR-0008: Retiring the scratch artefacts (a curated import into `docs/history/`, the payload taken from the live config, and nothing deleted without the owner's instruction)

**Status:** Accepted by the owner on 2026-10-16 (proposed 2026-10-15).

Before this repo existed, the layered config was designed and built in scratch directories. This ADR decides what moves into the repo. Specs, review notes, change diffs and the evidence the review notes cite go into `docs/history/`, byte for byte. Caches, probe output and raw logs are dropped. The payload, the deployable files under `config/`, is copied from the live config rather than from scratch, because adoption compares against the live files.

Terms such as live config, payload, adoption, clause inventory and the v2 spec are defined in the [Terms section of the architecture doc](../architecture/README.md#terms).

## Context

Three locations hold the material.

`~/dev/scratch/claude-md-v2/` contains:
- the approved v2 spec and its review note, which records five adversarial review passes;
- nine earlier snapshots of the spec, and the change diffs the review note cites;
- evidence files;
- the implementation: code, tests, 892 KB of eval results and 284 KB of probe output;
- the staging bundle for the lifecycle skill, with 1.1 MB of eval results.

`~/dev/scratch/claude-md-rewrite/` holds an earlier spec, which the v2 spec superseded, and `baseline/CLAUDE.md.orig`. That file is v1 of the global CLAUDE.md, and the clause inventory quotes its rules. Only `CLAUDE.md.orig` is imported from this directory.

`~/.old-claude/skills/issue-spec-driven-workflow/` holds the retired skill that the lifecycle skill replaced.

The owner asked that the review notes and the decision log survive, and that caches and probe output don't.

## Options

1. **Reconstruct the history** as one backdated commit per spec snapshot (`SPEC.pre-*.md`). That would fabricate commits that never existed, and the diffs already record each step.
2. **Import everything as is.** Simple, but it brings in about 2.5 MB of caches and logs, raw transcripts that could hold secrets, and noise.
3. **A curated, byte-for-byte import.** Keep the documents and the evidence the review notes cite, and drop caches and raw logs. Build the payload from the live files, because adoption needs the live bytes. Give the global CLAUDE.md a real two-commit history: v1, then v2.

## Decision

The repo takes option 3. Each group of files becomes its own commit in plan issue 3, except the payload, which plan issue 2 imports.

| Source | Destination | Notes |
|---|---|---|
| `claude-md-v2/SPEC.md`, `REVIEW-NOTE.md` | `config/SPEC.md`, `config/REVIEW-NOTE.md` | Living documentation beside the payload it documents ([ADR-0002](0002-repo-layout.md)), imported byte for byte with the status line unchanged. The owner's decisions D-1 to D-5 are in the spec's Decisions section, so the decision log survives with it. |
| `claude-md-v2/SPEC.pre-*.md` (9 files), `*-changes.diff` (9 files) | `docs/history/claude-md-v2/revisions/` | The review note cites the diffs by name, and the snapshots let each diff be replayed. About 400 KB of text. |
| `claude-md-v2/evidence-official-docs.md` | `docs/history/claude-md-v2/evidence/` | Scanned: no email addresses, tokens or excluded terms |
| `claude-md-v2/evidence-conversation.txt`, `PROMPT-claude-config-repo.md` | not imported | Both mention content that must stay out of the repo |
| `impl/FR-18a-results.md`, `impl/README.md` | `docs/history/claude-md-v2/evidence/` | The verified harness behaviour is still relevant: CLAUDE.md loading through ancestor directories, and the merge hook's `ask` being honoured under bypass mode |
| From `impl/eval/results/`: `ac6-summary-final.md`, `ac6-summary-addendum.md`, `ac6-summary-s19-sonnet.md`, `ac4-rubric-final-run{1,2}.json`, `check-real.json`, `hook-timing.txt`, `ac8-checks.txt`, `optimizer-compare.txt` | `docs/history/claude-md-v2/evidence/results/` | These are the figures the review note's "Final results" section quotes. Everything else in `results/` is dropped. |
| `impl/probe/`, the rest of `impl/eval/results/`, `.pytest_cache`, `__pycache__` | dropped | Probe output and caches |
| `impl/check_config.py`, `install_v2.py`, `tests/`, `eval/clauses.json`, `eval/scenarios.json`, `run_eval.py`, `score_ac7.py` | `tools/`, `tests/`, `eval/` | Ported, not frozen. Each is first committed unchanged, so later diffs show every change, then adapted in plan issues 5, 6 and 9. |
| `impl/deliverable/` | not imported | Byte-identical to the live config, per the install manifest. `config/` is built from the live files. |
| `staging/github-issue-lifecycle/` | not imported | Identical to the live copy, apart from `SPEC.md` and `REVIEW-NOTE.md`, which exist only in the live copy |
| From `staging/eval-results/`: `final-default.log`, `final-sonnet.log`, `trigger-final.log`, `case23*-{default,sonnet}.log` | `docs/history/github-issue-lifecycle/evidence/` | The rest (partial outputs, JSON dumps, diagnostic scripts) is dropped |
| `lifecycle-history/` (3 spec snapshots, 2 diffs) | `docs/history/github-issue-lifecycle/revisions/` | |
| `claude-md-rewrite/SPEC.md`, `REVIEW-NOTE.md` | not imported | The v2 spec superseded this spec (decision D-2) and records the supersession. The spec also mentions content that must stay out of the repo. |
| `claude-md-rewrite/baseline/CLAUDE.md.orig` | The first commit of `config/CLAUDE.global.md` (v1), followed by the v2 commit | A real history: `git log -p config/CLAUDE.global.md` shows the v1-to-v2 change that the clause inventory describes. The file's hash is checked against the baseline the inventory records and against the copy in the `claude-md-v2-*` backup. |
| `~/.old-claude/skills/issue-spec-driven-workflow/`, without `.pytest_cache` | `docs/history/retired-skills/issue-spec-driven-workflow/` | Outside `config/`, so it is never deployed |
| `~/.claude/` `CLAUDE.md`, `skill-overlays/`, `hooks/`, and `skills/` `github-issue-lifecycle` and `linux-troubleshooting` | `config/…` | Copied from the live config and checked by hash (plan issue 2), without caches. Other live skills are not imported. |

### Requirement IDs in imported code

The lifecycle skill's `diff-check` rejects requirement IDs in code unless living documentation in the repo mentions them ([ADR-0009](0009-spec-id-hygiene-for-eval-data.md)). `install_v2.py`, the checker's tests and the eval data cite the v2 spec, which lives at `config/SPEC.md`, so their IDs stay as they are. Any ID that no living document mentions is renamed in the port commit.

### Deletion

Claude deletes nothing in `~/dev/scratch/` or `~/.old-claude/` unless the owner asks explicitly; the lifecycle spec says the same about the retired skill (OS-6). Once the imports are merged and adoption has passed, the runbook lists what may be deleted.

The owner doesn't intend to return to v1. Once adoption has passed, neither the retired skill in `~/.old-claude/skills/issue-spec-driven-workflow/` nor the `claude-md-v2-*` backup has any role in rollback, and the retired skill is preserved in `docs/history/`. On 2026-10-16 the owner told Claude to delete both once adoption passes on the personal machine; issue 7 does it, listing the two paths first. The rest of `~/.old-claude/` is out of scope: `~/.old-claude/work-skills/` holds the existing work skill ([ADR-0011](0011-tracker-agnostic-lifecycle.md)) and is never deleted as part of this cleanup.

## Consequences

About 0.6 MB of history text enters the repo once and is never edited afterwards. Later changes to the subjects of those documents happen through new issues and ADRs.

Hard-coded paths into the scratch directories stop working once they are deleted. Plan issues 4, 5, 6 and 9 remove every such reference first, and a CI rule fails on `/home/` or `dev/scratch` appearing in `tools/`, `config/` or `eval/`.

## Revisit when

- The history grows enough to slow down cloning (not likely at this scale), or the owner wants it out of the main repo. Move it to an orphan `history` branch or a separate archive repo then.
