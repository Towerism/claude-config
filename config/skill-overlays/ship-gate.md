User policy for this skill.

- Run it before any merge you make yourself, and merge only if it allows the merge.
- If it blocks or raises issues: fix them on the PR branch; if the changeset changed meaningfully, give it one adversarial pass; then run ship-gate again. Repeat until it allows the merge.
- Never merge past a ship-gate block.
- A pass is not merge authorization: merge only when the user has authorized merging.
