# Review note: the `gitea-issue-lifecycle` spec

This note records the single adversarial review of `SPEC.md` for issue [#46](https://github.com/Towerism/claude-config/issues/46), run on 2026-10-22 by a fresh `general-purpose` agent using `adversarial-reviewer` and the owner's overlay for it. The approval mode is adversarial approval, where a pass with no CRITICAL finding and no change to the spec approves it.

**Verdict:** concerns. There were no CRITICAL findings, six warnings and five notes. All six warnings and four of the notes were fixed in the spec, which means the pass does not approve it: the owner sees every change below and gives the go-ahead. Three further observations are accepted with a reason.

## Warnings

**W1, fixed: the chosen login never reached the calls.** The spec matched a login to the repo's server but showed `tea api` without a login flag, so `tea` would use its default login. If that login belonged to another server, the check `tea api user` would still pass and the writes would go to the wrong server. FR-4 now puts `--login <name>` on every call and has the pre-write check compare the login's server host with the remote's host. The API Contracts section shows the flag.

**W2, fixed: three behaviours were assumed and never checked.** Nothing was run against a real server or a real `tea`. The spec relied on `tea api` exiting non-zero on an error answer, on the shape of a timeline entry (the check for who added `approved`), and on `-d @file` sending the file. New FR-4a lists them as assumptions until the real-server check in FR-20 confirms them. Until then the skill reads each answer's fields and treats an error message or a missing field as a failed step, instead of trusting the exit code. `SKILL.md` and the commands reference must say the commands are unverified, and AC-3 checks it. New EC-10 covers an exit code of 0 on an error.

**W3, fixed: the linked git rules tell Claude to run `gh`.** The GitHub skill's `git-workflow.md` has `gh pr create` and `gh pr merge` examples. FR-3 now requires `SKILL.md` to say that FR-16 and FR-17 replace them and not to tell Claude to run `gh`; AC-2 checks it.

**W4, fixed: CI would never run the package test.** `ci.yml` lists each skill's tests by name and the default test run doesn't collect `config/skills/*/tests`. FR-22 now requires a `ci.yml` step, the skills table lists `ci-cd-pipeline-builder` for it, and AC-6 checks it.

**W5, fixed: the trigger accuracy could not be measured.** NFR-T1 named a trigger runner that only the GitHub skill ships. NFR-T1 now says the owner checks it by hand and that nothing in CI measures it; OS-2 already defers a runner to the follow-up.

**W6, fixed: label and timeline reads ignored pagination.** A repo with more than 50 labels would hide some of the seven, and the skill would then try to create labels that exist. FR-11 and FR-12 now follow pages until a short page comes back.

## Notes

- **Fixed (N1):** the follow-up reference in D-3 now says FR-20; FR-16 no longer cites itself; FR-20 says "this work files" the two issues before the PR opens and the PR description names them, since a PR can't file an issue.
- **Fixed (N2):** FR-17 reads the allowed merge styles from the repo answer (`allow_merge_commits`, `allow_rebase`, `allow_squash_merge`).
- **Fixed (N3):** endpoints are written without a leading slash everywhere, and the spec says `tea api` adds the `/api/v1` prefix (an assumption listed in FR-4a).
- **Fixed (N4):** FR-8 filters the returned titles itself, because a server without a search indexer may ignore `q`.
- **Fixed (N5):** FR-22 requires a repo test that fails when a host profile lists the Gitea skill without the GitHub one, because the shared script lives in the GitHub skill.

## Accepted without a spec change

- **The GitHub skill's description isn't edited to exclude Gitea.** Accepted: ADR-0011 picks the lifecycle through the repo's CLAUDE.md and the host default, and the new skill's own description names the Gitea cases. Editing the GitHub skill would reopen its approved spec.
- **No CI-status check before the merge.** Accepted: GitHub spec FR-18 leaves it to `ship-gate` and the repo rule "merge only after `gh pr checks` is green"; the same split applies here.
- **A note in the GitHub skill's `SPEC.md` that this skill consumes its script.** Accepted for the same reason as the first item; the repo test in FR-22 guards the dependency instead.
