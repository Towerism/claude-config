User policy for this skill. Apply it; loading the skill isn't enough.

Applies to any change to `.github/workflows/*`, `.gitlab-ci.yml`, deploy jobs, or the scripts they call.

Before the pipeline plan:
- read `references/pipeline-design-notes.md` and `references/deployment-gates.md`;
- read `references/github-actions-templates.md` or `references/gitlab-ci-templates.md` for the platform;
- run `scripts/stack_detector.py` against the repo, so stages come from detected stack signals, not guesswork.

The plan must visibly state:
- stages and commands traced to detected signals, each command confirmed to exist in the repo;
- gate order lint → test → build → deploy, with deploy requiring green CI and a build artifact;
- cache keys matched to the package manager and lockfile;
- matrix builds only where compatibility requires them, with the reason;
- secrets and env vars listed and documented, never embedded in YAML;
- environment protection: staging vs production, protected branches, manual approval for production;
- a rollback command or procedure for every deploy job;
- each pitfall in `references/pipeline-design-notes.md` addressed or marked N/A;
- how the change is validated before merge (YAML parses, run locally where possible, e.g. `act`), and which items are review checks rather than automated checks.

Load this skill again before writing the pipeline YAML, and add one optimization at a time (cache, matrix, split jobs).
