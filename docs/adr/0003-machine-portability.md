# ADR-0003: Machine portability (runtime resolution first, one substitution variable, per-host component profiles)

**Status:** Accepted by the owner on 2026-10-16 (proposed 2026-10-15). Amended 2026-10-20 by #17 (spec approved by the owner): synced skills are per machine; see [Amendment for #17](#amendment-for-17-2026-10-20).

Code works out its paths when it runs. The only deploy-time substitution is a single placeholder, `{claude_home}`, used in the hook commands of the settings fragment. Each machine has a host profile, a small JSON file that selects which whole files and skills it gets. Terms used here are defined in the [architecture overview](../architecture/README.md#terms).

## Context

Several files carry absolute paths under `/home/mfracker/`:

- the three hook commands in `settings.json`;
- `install_v2.py`, in its scratch, baseline and staging constants;
- `eval/run_eval.py`, for the real config directory, a temporary directory under `~/.claude/jobs/`, the baseline path, and a plugin path pinned to one version for the quality rubric;
- the lifecycle skill's `run_behaviour_evals.py`, which points at the scratch deliverable.

The hooks themselves already resolve their paths at run time, from `CLAUDE_HOME` or `~/.claude` and from `installed_plugins.json`.

The owner's second machine is a work machine. It gets the same skills as the personal machine and differs only in its default lifecycle: Jira instead of GitHub ([ADR-0011](0011-tracker-agnostic-lifecycle.md)).

The rendered hook commands also have to equal the live ones byte for byte. If they do, the first deploy on a machine that already has the config leaves the hook entries in `settings.json` unchanged.

## Options

| Option | For | Against |
|---|---|---|
| Templating, with Jinja or chezmoi templates across files | Expressive | A templated CLAUDE.md or overlay can't be linted, token-counted or evaluated as the file the model actually reads. Adds a template engine as a dependency. |
| Per-host override directories (`hosts/work/config/…` replacing files) | Each machine's files are concrete | Duplicated files drift apart, and every change has to be made twice |
| Runtime path resolution only (`$HOME` or `~` in the hook commands, with code resolving the rest) | No render step | Hook commands would depend on shell expansion and would no longer equal the live absolute commands, so the first deploy would have to rewrite `settings.json` |
| Hybrid (chosen): runtime resolution in all code, exactly one deploy-time substitution (`{claude_home}`) in the settings fragment, and host profiles that select whole files | Files are concrete everywhere, the first deploy leaves the hook entries in `settings.json` alone, and a second machine needs only a JSON file | Variant CLAUDE.md files, when they come, are whole-file copies |

## Decision

1. Code resolves paths at run time.
   - The hooks stay as they are.
   - The checker and the eval harness take paths from the repo root, the `CLAUDE_CONFIG_DIR` or `CLAUDE_HOME` environment variables, or `installed_plugins.json`. The rubric path comes from the active `claude-md-management` install path instead of being pinned to version 1.0.0.
   - Temporary directories come from `tempfile.mkdtemp(dir="/tmp")`. That keeps the eval runs' working directory outside `$HOME`, which the v2 mechanism check found necessary for isolating the old and new configs (v2 spec FR-18a).
   - The lifecycle skill's eval switches to repo-relative paths. The lifecycle skill has a spec, so this change goes through plan issue 4.
2. There is one substitution variable. `{claude_home}` appears only in `config/settings.fragment.json`. `cfg` renders it to the target config directory: `~/.claude` by default, or `CLAUDE_CONFIG_DIR` or `--target` if given. On the owner's machine the result is exactly `python3 -I /home/mfracker/.claude/hooks/skill_overlay.py`, the live string. No other file is rendered, so deployed files are byte-identical to the repo.
3. Host profiles in `hosts/<name>.json` select components and never rewrite content:

```json
{
  "name": "personal",
  "claudeMd": "config/CLAUDE.global.md",
  "overlays": "all",
  "hooks": ["skill_overlay.py", "merge_confirm.py"],
  "skills": ["github-issue-lifecycle", "linux-troubleshooting", "plain-prose", "writing-docs"],
  "plugins": ["engineering-skills@claude-code-skills", "engineering-advanced-skills@claude-code-skills", "claude-md-management@claude-plugins-official"]
}
```

The host name is given explicitly with `--host` on the first deploy and stored in the deploy state. It is not guessed from the machine's hostname. `cfg check` validates every profile: skill names must resolve against that profile's plugins, and the token budgets apply to that profile's CLAUDE.md.

The work machine gets `hosts/work.json` (plan issue 17). It lists the same skills as the personal profile and sets `defaultLifecycle` to `jira-ticket-lifecycle`. Both machines share the one global CLAUDE.md, which names no tracker ([ADR-0011](0011-tracker-agnostic-lifecycle.md)), so no `CLAUDE.work.md` is needed.

## Consequences

A test renders the personal profile into a temporary `CLAUDE_CONFIG_DIR` and asserts that the hook commands equal the live ones. That is the precondition for the first deploy leaving the hook entries in `settings.json` alone. A second test renders a minimal second profile into a temporary directory, to prove the mechanism before any real work profile exists.

Two CLAUDE.md variants have no way to include shared rules from a common file. To make sure a duplicated rule is checked in both, the clause inventory (the list of every CLAUDE.md rule and the file that now holds it) gains a per-file target.

## Revisit when

- The work CLAUDE.md shares most of its lines with the personal one, and making every edit twice starts to cost real effort. Consider generating both from a common source, with a check that the generated files are committed.
- Claude Code documents support for expanding `${CLAUDE_CONFIG_DIR}` in user hook commands. The substitution could then go, as long as every machine deploys again.

## Amendment for #17 (2026-10-20)

The skills claude.ai syncs into `~/.claude/skills/synced/` belong to the account a machine signs in with, and the work machine uses a different account from the personal one. So synced skills are a fact of each machine, like its plugin cache: host profiles never list them, and nothing the machines share (the CLAUDE.md, the overlays, the settings fragment, the plugin lock) may name or depend on one. A skill both machines need is installed as a plugin listed in both profiles, or kept as a repo skill. [ADR-0005's amendment](0005-verification-and-ci.md#amendment-for-17-2026-10-20) says how the lock and the checker enforce this.
