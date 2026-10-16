# CLAUDE.md v2: implementation (stopped at the FR-22 review stop; nothing installed)

| Path | What |
|---|---|
| `deliverable/CLAUDE.md` | New global CLAUDE.md (54 lines, 1,399 est. tokens; baseline 2,742) |
| `deliverable/skill-overlays/*.md` | 7 overlays (106–361 tokens each; spec-driven-workflow now 247) |
| `deliverable/hooks/skill_overlay.py` | PostToolUse(Skill) + UserPromptExpansion injector (FR-19–21) |
| `deliverable/hooks/merge_confirm.py` | PreToolUse(Bash) merge "ask" guard (FR-24) |
| `check_config.py` | Checker (AC-1, AC-2, AC-4 lint, budgets) |
| `install.sh`, `install_v2.py` | Install + non-destructive restore (FR-23); restore.sh is written into the backup dir |
| `tests/` | pytest suite: `python3 -I -m pytest -q tests` |
| `eval/clauses.json` | FR-1 clause inventory (76 clauses from `claude-md-rewrite/baseline/CLAUDE.md.orig`, sha256 = live file) |
| `eval/scenarios.json`, `eval/run_eval.py` | AC-6 scenario eval + AC-4 rubric (isolated headless arms) |
| `eval/results/` | All evidence (checker JSON, timings, AC-7/8/9 streams, AC-6 results) |
| `FR-18a-results.md` | Mechanism check results |

## Install (only after the user's go-ahead at FR-22)

`bash /home/mfracker/dev/scratch/claude-md-v2/impl/install.sh` — prints the backup dir; undo with `<backup>/restore.sh`.

## Deviations from the spec's wording (no behaviour change)

- FR-23 step 3 says hook entries are added "via `update-config`" (a Claude skill for editing settings by hand). The installer merges the same three entries into `settings.json` programmatically, and restore removes exactly those entries by command path. Equivalent result; scripted so it's testable.
- Restore also removes the installed overlays and hook scripts using a second manifest (`deliverable-manifest.sha256`, same hash-or-snapshot rule as the bundle manifest), so an unchanged install restores `~/.claude` byte-for-byte. Edited files are moved to the snapshot, never deleted.
- Install additionally aborts (before any change) if `~/.claude/hooks/skill_overlay.py`, `merge_confirm.py` or `~/.claude/skill-overlays` already exist.
