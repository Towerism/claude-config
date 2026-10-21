# ADR-0002: Repo layout and boundaries

**Status:** Accepted by the owner on 2026-10-16 (proposed 2026-10-15). Amended 2026-10-20 by #62 (spec approved by the owner): skills copied whole from upstream (see "Amendment for #62"). Amended 2026-10-20 by #75 (spec approved by the owner): those skills live in their own tree, `config/vendored-skills/` (see "Amendment for #75").

Everything that gets deployed lives under `config/`, and nothing outside it reaches the live config (`~/.claude`). The global CLAUDE.md is stored as `config/CLAUDE.global.md`, so it never loads as memory while someone works on the repo. Terms used here are defined in the [architecture overview](../architecture/README.md#terms).

## Context

The repo holds four kinds of content:

1. the payload, which is deployed into `~/.claude`;
2. tooling that is never deployed, such as the checker, the deploy tool and their tests;
3. local model evals and their committed results;
4. documentation and history.

The owner's global rules put a skill's spec in a `SPEC.md` beside its `SKILL.md`. Specs for any other work in a repo go in the body of the GitHub issue.

Two behaviours of Claude Code constrain file names. First, a file named `CLAUDE.md` loads as memory. At the repo root it becomes project memory, and in a subdirectory it loads as soon as Claude reads files there. A draft of the global CLAUDE.md stored under that name would be live in every session that works on the repo, which would affect that work and any eval run started from inside the repo. Second, a `.claude/skills/<name>/` directory inside a project registers project skills, so draft copies stored there would shadow or duplicate the deployed user skills.

## Options

1. Mirror `~/.claude` at the repo root (`CLAUDE.md`, `skills/`, `hooks/`). This suits GNU stow, but the root `CLAUDE.md` would be the global file, so the first problem applies.
2. Put the repo's content under `.claude/` (`.claude/CLAUDE.md`, `.claude/skills/`). Familiar, but both problems apply.
3. Put the deployable payload under `config/` with the global file renamed, and keep tooling, evals and docs beside it.

## Decision

Option 3, with the layout shown in section 3 of the [architecture overview](../architecture/README.md). These are its boundaries:

| Path | Deployed? | Rule |
|---|---|---|
| `config/` | Yes. It is the only deployed directory. | `config/CLAUDE.global.md` deploys as `~/.claude/CLAUDE.md`, the one rename. Everything else maps one to one: `config/X` becomes `~/.claude/X`. |
| `config/skills/<name>/` | Yes | The whole skill as it is installed today: `SKILL.md`, plus `SPEC.md` and `REVIEW-NOTE.md` where they exist, and `scripts/`, `references/`, `tests/` and `evals/`. The live skill directory is a symlink to this directory in the deployed release ([ADR-0001](0001-deployment-model.md)), so tests and evals stay reachable without being copied. |
| `config/vendored-skills/<name>/` | Yes, to `~/.claude/skills/<name>` | Since #75: the skills copied whole from upstream, each with a `VENDORED.json` that lists its `SKILL.md`. They are not edited by hand; see the amendments below. |
| `CLAUDE.md` at the repo root | No | Project memory for working on this repo: the commands, the gates, and the rule to merge only after `gh pr checks` is green. |
| `tools/`, `tests/`, `schemas/` | No | Repo tooling and its tests |
| `eval/` | No | The eval harness, the scenarios, the clause inventory (the list of every CLAUDE.md rule and where it now lives), the map from changed paths to required evals, and the committed eval records. Raw output goes to `eval/out/`, which git ignores. |
| `docs/adr/`, `docs/architecture/` | No | The ADRs are the source of truth. The owner's global rules require a new or updated ADR for any departure from them. |
| `docs/history/` | No | Frozen files imported from the old scratch directories ([ADR-0008](0008-retiring-scratch.md)). Never edited after import. |

Where a spec lives depends on what the issue changes. The owner set this rule on 2026-10-16:

- **A change to how a skill works:** the spec lives next to the skill, in `config/skills/<name>/SPEC.md`, with its review note in `REVIEW-NOTE.md` beside it. The issue still exists, because the lifecycle skill opens one for all work, but its body links to the `SPEC.md` instead of holding the spec. Approval is recorded in the spec's Status line. A skill without a `SPEC.md` gets one with its first change in this repo (the owner's decision, 2026-10-16). That overrides the global rule to edit such skills directly, so the repo's own `CLAUDE.md` states it. Both files deploy with the skill, which matches what is live now.
- **A change to how claude-config itself works** (`cfg`, CI, the checker, the eval harness), **or to how it coordinates the skills** (the global CLAUDE.md, the hooks, the overlays, the settings fragment): the spec goes in the issue body, between the lifecycle skill's spec markers. The repo has no `specs/` directory.

The v2 spec is a third case. It documents the coordination layer as a whole, so it lives beside the payload as `config/SPEC.md`, with its review note in `config/REVIEW-NOTE.md`, imported byte for byte. The owner confirmed on 2026-10-16 that it is living documentation, not frozen history ([ADR-0009](0009-spec-id-hygiene-for-eval-data.md)). Changes to the coordination layer are still specced on their issues. When such a change merges, the same PR updates `config/SPEC.md` so it keeps describing current behaviour (confirmed by the owner on 2026-10-16), and the eval data's requirement IDs keep pointing at a living document. Neither file is linked into `~/.claude`, because `cfg` links only the components a host profile lists.

## Consequences

The checker, the eval harness and the tests take their paths from the repo root, never from `~` or the old scratch directories.

`/revise-claude-md` run inside the repo won't find `CLAUDE.global.md`, which is intended. It edits `~/.claude/CLAUDE.md` instead, and `cfg capture` brings that edit back into the repo as a reviewed change.

Because of the rename, GNU stow can't be used without reversing this ADR, which is consistent with [ADR-0001](0001-deployment-model.md).

## Revisit when

- Claude Code gains a way to exclude paths from memory loading or skill discovery. The repo root could then mirror `~/.claude` directly.
- Deployed skills are slimmed so their tests and evals aren't deployed. Those tests would then move to `tests/skills/<name>/`.

## Amendment for #62 (2026-10-20)

**Vendored skills.** The owner removed the two `claude-code-skills` plugins and kept 17 of their skills as repo skills (#62). Each is a whole copy under `config/skills/<name>/`, taken from upstream as it was. A skill whose `VENDORED.json` lists its `SKILL.md` is a vendored skill:

- Its `VENDORED.json` lists every file in its directory, with the file's upstream path and sha256. A file changed here, for example to pass the repo's email and token checks, is marked `adapted`, with a note saying what changed and the hash of the repo copy (`localSha256`). `tests/test_vendored_skills.py` checks all of this.
- ruff skips its directory, as it skips `tools/vendor/`, because lint findings in upstream code aren't fixed here.
- The rule that a skill gets a `SPEC.md` with its first change in this repo applies to the first change that isn't a copy from upstream. Copying the skill in, copying a newer upstream version over it, or adapting a file only to pass the repo's checks doesn't count. The first change to how the skill works adds its `SPEC.md` and `REVIEW-NOTE.md`, and from then on the skill follows the rule above like any other.

`plain-prose` also has a `VENDORED.json`, but it lists only the four files under its `vendored/` directories, not its `SKILL.md`, so it isn't a vendored skill. [ADR-0005's amendment](0005-verification-and-ci.md#amendment-for-62-2026-10-20) says how the copies are checked against upstream.

## Amendment for #75 (2026-10-20)

**Vendored skills have their own tree.** The owner asked for the layout to show which skills can be edited by hand. The 17 vendored skills moved, unchanged, from `config/skills/<name>/` to `config/vendored-skills/<name>/`, and `config/skills/` now holds only the skills maintained here: `github-issue-lifecycle`, `jira-ticket-lifecycle`, `linux-troubleshooting`, `plain-prose` and `writing-docs`.

- The rule from #62 that defines a vendored skill now also sets its tree: a skill whose `VENDORED.json` lists its `SKILL.md` lives in `config/vendored-skills/`, and no other skill does. `plain-prose` stays in `config/skills/`. `tests/test_vendored_skills.py` checks both trees.
- Both trees deploy the same way, as `~/.claude/skills/<name>`, and host profiles keep listing bare names. `cfg` looks for a name in `config/skills/` first, then in `config/vendored-skills/`, and refuses a name found in both.
- ruff skips `config/vendored-skills/` as one directory.
