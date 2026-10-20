User policy for this skill. Apply it; loading the skill isn't enough.

Before the test plan for any HTTP route, use:
- the "Auth Test Matrix" and "Input Validation Matrix" sections of this skill's SKILL.md;
- its error-code matrix (Core Capabilities: 400/401/403/404/422/500 per route);
- `references/example-test-files.md`.

The plan states which matrix rows apply to each route and which are N/A, with the reason.

Route detection here covers Next.js, Express, FastAPI and Django only. For other frameworks, apply the matrices to the repo's route files yourself (for example Nitro/h3 `server/api/**`).

Load this skill again before writing the tests themselves.
