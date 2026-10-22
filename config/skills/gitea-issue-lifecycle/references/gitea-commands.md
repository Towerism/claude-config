# Gitea calls used by this skill

**These calls are unverified.** They come from Gitea's web API, not from a run against a live server or a real `tea`. Three behaviours are assumed until the owner confirms them on a real server: `tea api` exits non-zero on a non-2xx answer, a timeline entry has the shape in the table below, and `-d @file` sends the file as the request body. Until then, read each answer's fields and treat an error `message` or a missing field as a failed step. If a flag below is rejected, run `tea api --help` and report the difference.

Every call is `tea api --login <login> -X <METHOD> <endpoint> [-d @<file>]`. `tea api` adds the server's `/api/v1` prefix, so endpoints have no leading slash. `<login>` is the entry in `tea login list` whose server host matches the remote's host. Bodies always go through files, never through command-line strings, so issue text is never interpolated into a shell command.

| Operation | Call | Check in the answer |
|---|---|---|
| Who am I | `tea api --login <login> -X GET user` | `login`; stop if it fails |
| Read issue | `tea api --login <login> -X GET repos/<o>/<r>/issues/N` | `number`, `state`, `body`, `labels[].id`, `assignees`; a `pull_request` field means it is a PR |
| Search | `tea api --login <login> -X GET "repos/<o>/<r>/issues?state=open&type=issues&limit=10&q=<words>"` (then `state=all`) | the server may ignore `q`; filter titles yourself |
| Create or file | `tea api --login <login> -X POST repos/<o>/<r>/issues -d @issue.json` | `number` |
| List labels | `tea api --login <login> -X GET repos/<o>/<r>/labels` | follow pages until a short page comes back |
| Create label | `tea api --login <login> -X POST repos/<o>/<r>/labels -d @label.json` | `id`; only for labels missing from the list |
| Assign | `tea api --login <login> -X PATCH repos/<o>/<r>/issues/N -d @assign.json` | `assignees` |
| Add labels | `tea api --login <login> -X POST repos/<o>/<r>/issues/N/labels -d @labels.json` | ids in `{"labels": [..]}` |
| Remove label | `tea api --login <login> -X DELETE repos/<o>/<r>/issues/N/labels/<id>` | |
| Label provenance | `tea api --login <login> -X GET repos/<o>/<r>/issues/N/timeline` | entry with `type` `label`, `label.name`, `user.login`, `body` `1` when added; follow pages |
| Publish spec | `tea api --login <login> -X PATCH repos/<o>/<r>/issues/N -d @patch.json` | `body` |
| Comment | `tea api --login <login> -X POST repos/<o>/<r>/issues/N/comments -d @comment.json` | `id` |
| Repo facts | `tea api --login <login> -X GET repos/<o>/<r>` | `default_branch`, `allow_merge_commits`, `allow_rebase`, `allow_squash_merge` |
| Create PR | `tea api --login <login> -X POST repos/<o>/<r>/pulls -d @pr.json` | `number` |
| Merge | `tea api --login <login> -X POST repos/<o>/<r>/pulls/N/merge -d @merge.json` | only after the chat confirmation; never `"Do": "squash"` |
| After merge | `tea api --login <login> -X GET repos/<o>/<r>/issues/N` | `state` is `closed` |

Request bodies:

```json
{"title": "Add CSV export to the roster page", "body": "..."}
{"name": "in progress", "color": "FBCA04", "description": "Claimed"}
{"labels": [12, 15]}
{"assignees": ["login"]}
{"title": "feat(roster): add CSV export (#42)", "body": "...", "head": "feature/42-add-csv-export", "base": "main"}
{"Do": "merge", "delete_branch_after_merge": true}
```

Gitea has no counterpart of GitHub's `closingIssuesReferences`, so the link from a PR to its issue can't be read back before the merge. The `Closes #N` line in the body does the linking, `pr-body-check` checks that line, and the issue's `state` after the merge confirms it.

| Change type | Branch prefix |
|---|---|
| `feat` | `feature/` |
| `fix` | `fix/` |
| `docs` | `docs/` |
| hotfix issue | `hotfix/` |
| anything else | `chore/` |
