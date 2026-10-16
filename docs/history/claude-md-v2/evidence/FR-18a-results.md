# FR-18a mechanism check — Claude Code 2.1.299, 2026-10-15

All runs headless `claude -p` with hooks supplied via `--settings <tmp json>`; nothing in `~/.claude` was changed. Probe scripts and raw outputs: `impl/probe/`.

| Check | Result | Evidence |
|---|---|---|
| (a) `PostToolUse` matcher `Skill` fires — main session | **Yes** | `probe.log` payload: `tool_name: "Skill"`, `tool_input: {"skill": "engineering-skills:senior-qa"}`, `tool_response: {"success": true, "commandName": …}` |
| (a) … fires inside a subagent | **Yes** | payload carries `agent_id`, `agent_type: "general-purpose"`; Skill tool_use had a `parent_tool_use_id` (`run-sub.jsonl`) |
| (b) `hookSpecificOutput.additionalContext` reaches the model | **Yes**, main and subagent | both runs quoted `PROBE-CANARY-7731 from PostToolUse` |
| `UserPromptExpansion` on a user-typed `/engineering-skills:senior-qa …` | **Fires**; payload has `expansion_type: "slash_command"`, `command_name: "engineering-skills:senior-qa"`, `command_args`, `command_source: "plugin"`; its additionalContext reaches the model | `run-prompt.jsonl` |
| `UserPromptSubmit` on the same prompt | Also fires (raw `prompt`) | so FR-20 uses **UserPromptExpansion** only (it carries the skill name; avoids double injection) |
| PreToolUse `ask` under `--permission-mode default` / `auto` / `bypassPermissions` (with the command pre-allowed, `--permission-prompts none`) | **Honoured in all three**: the matching call lands in `permission_denials`; the non-matching call runs | `ask-*.json` |
| Hook payload `permission_mode` | Reported `default` in all three runs (does not affect the result) | `ask.log` |
| `--setting-sources project,local` excludes the user CLAUDE.md | **No.** It drops user plugins (109 → 19 skills) but `~/.claude/CLAUDE.md` still loads | model quoted its heading with no tools |
| Why: | `~/.claude/CLAUDE.md` is *also* picked up by ancestor-directory discovery (`/home/mfracker/.claude/CLAUDE.md` = `<ancestor>/.claude/CLAUDE.md`) for any cwd under `/home/mfracker`, even with a temp `CLAUDE_CONFIG_DIR` | iso run listed both files |
| Isolation that works | temp `CLAUDE_CONFIG_DIR` (arm's CLAUDE.md + copied `settings.json` + symlinked `plugins/` + credentials removed in a trap) **and** cwd outside `/home/mfracker` (e.g. `mktemp -d /tmp/...`) | model listed only the arm's CLAUDE.md and reported NO for the user heading |
| Skill list for AC-8 | `system`/`init` event of `--output-format stream-json --verbose`, key `skills` (list of names) | `run-post.jsonl` |

**Decision (no spec change needed):** deterministic injection works. Injection events: `PostToolUse` (matcher `Skill`) + `UserPromptExpansion` (FR-20's preferred event). FR-24's `ask` is honoured even under `bypassPermissions`.

**Note for the user (FR-22):** in real sessions under `~`, `~/.claude/CLAUDE.md` is loaded twice (as user memory and as an ancestor project file); this is pre-existing harness behaviour, unaffected by this change, but it doubles the always-loaded cost — one more reason to keep the file lean.
