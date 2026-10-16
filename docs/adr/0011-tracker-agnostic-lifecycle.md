# ADR-0011: Tracker-agnostic lifecycle (a generic `jira-ticket-lifecycle` skill, a per-machine default, and a CLAUDE.md that names no tracker)

**Status:** Accepted by the owner on 2026-10-16 (proposed 2026-10-16). The Jira access method and the work code host are filled in from a reading of the existing work skill (see the open questions in [plan.md](../architecture/plan.md#open-questions)).

The global CLAUDE.md stops naming `github-issue-lifecycle` and refers instead to "the lifecycle skill for the repo's tracker". A new skill, `jira-ticket-lifecycle`, does for Jira tickets what `github-issue-lifecycle` does for GitHub issues, and holds no project-specific values. Each machine has a default lifecycle: GitHub on the personal machine, Jira on the work machine. A machine-local config file that is never synced can override it, and a repo's own CLAUDE.md overrides both. Terms are defined in the [architecture overview](../architecture/README.md#terms).

## Context

The work machine is almost identical to the personal one. The difference is how work is tracked. Today it uses a skill that combines spec writing with a Jira workflow for one team's Jira project; the rest of this ADR calls it the existing work skill. A copy is kept at `~/.old-claude/work-skills/`, outside this repo.

That skill has the shape the personal machine had before v2. `issue-spec-driven-workflow` mixed a fork of upstream `spec-driven-workflow` with GitHub procedure, and v2 split it into a lifecycle-only skill plus the upstream spec skill. The owner wants the same split for Jira, with two extra conditions. The new skill must not be tied to any Jira project. And the global CLAUDE.md, which both machines share through the same symlinked file, must work whichever lifecycle a repo uses.

Three things in the current config name GitHub directly:
- pipeline steps 2 and 7 in the global CLAUDE.md ("Start: `github-issue-lifecycle`", "then `github-issue-lifecycle` closes out");
- the `spec-driven-workflow` overlay ("In a GitHub repo the spec lives in the issue body");
- the `tdd-guide` overlay ("When the work is on a GitHub issue").

This repo is public ([ADR-0007](0007-visibility-and-security.md)). The work skill is specific to one team's project, so neither it nor any of its project values (project key, site URL, custom field names, workflow states) may be committed here.

## Options

**How Claude knows which lifecycle applies**

| Option | For | Against |
|---|---|---|
| A. A CLAUDE.md line per machine (two CLAUDE.md variants) | Simple to read | Both machines share one CLAUDE.md file by design. Two variants would drift, and each would need its own evals. |
| B. One agnostic CLAUDE.md that tells Claude to read a config file for the default | One file | It relies on Claude remembering to read the file. The prose skills just showed that explicit "load this" instructions get skipped: three subagents were told to load `plain-prose` and none did ([ADR-0010](0010-prose-for-people.md)). |
| C. One agnostic CLAUDE.md, plus a `SessionStart` hook that states the machine's default at the start of every session | Deterministic, like the overlay hook. CLAUDE.md stays identical on both machines. | One more hook, so it must meet the hook review bar in [ADR-0007](0007-visibility-and-security.md). Whether `SessionStart` output reaches the model is unverified. |

**Where the default and its override live**

The default per machine belongs in the host profile (`hosts/personal.json`, `hosts/work.json`), because it is a known, reviewable fact about each machine. The override the owner asked for has to stay unsynced, so it can't live in the repo or in anything `cfg` deploys. That leaves a machine-local file outside both, read at run time.

**Where Jira project settings live**

Project values (site, project key, issue types, workflow transition names, label or field names) could go in the skill, which the owner ruled out. They could go in the machine-local file, which works when one machine serves one project. Or they could go in the repo being worked on, which works when repos map to projects. Both can be supported with a clear order.

## Decision

The repo takes option C.

**Choosing the lifecycle.** For any repo, the lifecycle comes from the first of these that gives an answer:
1. The repo's own `CLAUDE.md`, if it names its tracker. For example: "Work in this repo is tracked in Jira (`jira-ticket-lifecycle`)". This follows the owner's existing rule that a project CLAUDE.md wins on project specifics.
2. The machine-local override: the key `defaultLifecycle` in `~/.config/claude-config/local.json`. The file is outside `~/.claude`, outside the repo and outside anything `cfg` deploys, so it is never committed or synced. `cfg` never writes it.
3. The host profile's `defaultLifecycle`: `github-issue-lifecycle` in `hosts/personal.json`, `jira-ticket-lifecycle` in `hosts/work.json`.

**Delivering the answer.** A new hook script, `config/hooks/lifecycle_default.py`, runs on `SessionStart`. It reads steps 2 and 3. To do that, it finds the host name in `cfg`'s state file and reads that profile through the `current` release link. It then injects one line of context, for example: "On this machine the default lifecycle skill is `jira-ticket-lifecycle` (from the host profile). A repo's CLAUDE.md that names its tracker overrides this." It is standard-library Python, reads only those two files, never blocks, always exits 0, and is held to the same review bar as the other hooks. Step 1 needs no hook, because Claude already loads the repo's CLAUDE.md.

**The agnostic CLAUDE.md.** Pipeline step 2 becomes "Start: the lifecycle skill for the repo's tracker (the repo's CLAUDE.md says which; otherwise the machine default stated at session start)". Step 7 ends with "then the lifecycle skill closes out". The spec location becomes "the ticket or issue body, as the lifecycle skill says". The two overlays get the same wording. The clause inventory rows that quote these lines are updated in the same PR, and the scenario eval gains cases for both trackers. Each case runs with the default line injected the way the hook would inject it.

**The `jira-ticket-lifecycle` skill.** It mirrors `github-issue-lifecycle`, with a spec beside its `SKILL.md`, and leaves spec writing to upstream `spec-driven-workflow`. The design below comes from reading the existing work skill on 2026-10-16, keeping its generic behaviour and turning every team-specific value into configuration.

The skill does the following, in order:
1. Takes a ticket key (a bare number gets the configured project prefix), or searches open tickets and offers to reuse one, create one, or create one and link related tickets.
2. Claims the ticket: assigns it to the current user, optionally adds it to the active sprint, and moves it to the configured in-progress state.
3. Settles and records the approval mode, as the GitHub lifecycle does. The existing skill had no recorded mode; this one records it as a label, with configurable label names.
4. Stores the spec in the ticket description between the `spec:start`/`spec:end` markers. Any original description moves to a comment first. A spec longer than Jira's description limit (32,649 characters in the existing skill) is attached as `KEY-spec.md`, and older copies are removed.
5. Opens a draft PR that references the ticket and posts the PR link on the ticket. It won't overwrite someone else's edits to the PR body. When the PR is marked ready, it moves the ticket to the review state.
6. Merges only under the owner's merge rule and the merge-confirm hook. When configured, it also requires an approved, non-draft PR.
7. Closes out: moves the ticket to the done state, then deletes the branch and worktree. The existing skill had no cleanup step.

**Tools.** Jira is reached through the `jira` command-line tool (`ankitpokhrel/jira-cli`), which reads the server and login from its own config file (`~/.config/.jira/.config.yml`) and the API token from `JIRA_API_TOKEN`. The skill's script calls it with argument lists, never through a shell string. Two operations the CLI lacks go to the Jira REST API: uploading and deleting attachments. Those calls use HTTPS only, with the token used solely to build the auth header. The skill never stores or prints credentials. PRs go to GitHub through `gh`, as the existing skill does, so the merge-confirm hook already covers them.

**Configuration, not code.** The skill holds no project values. Every team-specific item found in the existing skill becomes a setting:
- project key and site URL;
- issue types;
- the in-progress, review and done state names, and their order;
- whether to add tickets to the active sprint;
- the summary format for new tickets;
- label names, and the link type for related tickets.

All work repos use the same Jira project (the owner confirmed this on 2026-10-16), so the settings live in one place: the `jira` section of `~/.config/claude-config/local.json` on the work machine. If a required value is missing, the skill asks instead of guessing.

**Code conventions.** The existing skill follows the team's conventions:
- branches named after the bare ticket key;
- PR titles of the form `KEY: title`;
- the first PR body line linking the ticket;
- squash merges preferred;
- no Conventional Commit check.

The owner decided on 2026-10-16 that work repos follow the global git rules instead: `type/{KEY-N}-{slug}` branches, Conventional Commits with the ticket key at the end of the subject, and no squash merges. The new skill therefore has no convention settings. It applies the global rules everywhere, the same as `github-issue-lifecycle`.

**Fixes over the existing skill.**
- Its instructions build a `zsh -ic "jira issue list … '<terms>' …"` command from search terms, so quotes or `$(…)` in a ticket title or user text would run as shell. The new skill passes all ticket and user text only as arguments to its script, and a test covers that case.
- It wraps every call in `zsh -ic` to pick up the token from the interactive shell, which forces it to leave a worktree before calling Jira. The new skill expects `JIRA_API_TOKEN` in the environment of non-interactive shells; on the work machine it is exported from `~/.zshenv` (agreed by the owner on 2026-10-16). It stops with a clear message if the token is missing, as the GitHub lifecycle does when `gh auth status` fails.
- Ticket text is declared as data, the same as GitHub issue text, and a behaviour eval checks that instructions inside a ticket aren't followed.

**Evals.** The existing skill's fake CLI is the pattern. It is a script installed on `PATH` as both `jira` and `gh`, which logs each call as JSON lines and replays canned replies from a scenario file. REST calls are tested against a local HTTP server. The new skill's trigger and behaviour evals use the same approach with invented project values, so no real project is touched and no team value appears in the repo.

**The existing work skill stays out of this repo.** Its content is team-specific, and this repo is public. It is read only as source material for the new skill's spec, on the owner's machines, and quoted only for generic behaviour. Its project values move into the work machine's `local.json`. `cfg` ignores its directory, as it ignores every skill not listed in the host profile. Once `jira-ticket-lifecycle` is deployed there, the existing skill is removed from the work machine's `~/.claude/skills/` (the owner decided this on 2026-10-16). The copy in `~/.old-claude/work-skills/` stays. On the work machine, the local excluded-terms list ([ADR-0007](0007-visibility-and-security.md)) should include the team's project key and names, so they can't slip into a commit.

**Host profiles.** Both machines deploy the same skills: both lifecycle skills, `linux-troubleshooting`, `plain-prose` and `writing-docs`. They differ only in `defaultLifecycle`. `hosts/work.json` replaces the earlier plan for a separate `CLAUDE.work.md`, which is no longer needed.

## Consequences

- The global CLAUDE.md change touches pipeline wording that the clause inventory guards, so its PR needs updated clause rows and a scenario eval record covering both trackers.
- A third hook joins the hook review bar, and the symlink mechanism check (plan issue 14) also verifies that `SessionStart` context reaches the model, in the main session and in subagents.
- The GitHub lifecycle skill's scripts (`commit-check`, `diff-check`, `pr-body-check`) assume `#N` references. The Jira skill needs its own equivalents for `KEY-N`. Sharing code between them is left for later, once both exist.
- The Jira skill's diff check follows the same rule for requirement IDs as the GitHub lifecycle skill: an ID is allowed in code only when it appears in living documentation, and a spec counts as living when it sits next to the thing it documents ([ADR-0009](0009-spec-id-hygiene-for-eval-data.md)). The owner set this on 2026-10-16.
- The work machine needs nothing machine-specific in the repo beyond `hosts/work.json`.

## Revisit when

- The mechanism check shows `SessionStart` context doesn't reach the model. Fall back to injecting the default line through the `UserPromptSubmit` hook on the first prompt of a session.
- A repo uses a third tracker. Add a third lifecycle skill under the same resolution order.
- Work repos start using more than one Jira project. Add a per-repo settings file (`.jira-lifecycle.json`) that takes precedence over `local.json`.
