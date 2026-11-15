DECISION: ACCEPT
RISK: high
SURFACES: security
RESCOPED: no

## Summary
- Accepted as written: the issue is a complete, checkable spec for hardening CI/CD now that the repo is public.
- The current state it describes matches the repo: `deploy.yml` runs on every push to `main` with no test gate, uses `appleboy/ssh-action@v1` (a moving tag) with no host-key check, and no workflow declares `permissions`.
- Rated high risk because it changes the pipeline that deploys production. A broken deploy job only shows up after a merge to `main`.
- It fits in one PR: no application code changes, only workflow/config files, `SECURITY.md` and one docs section.

## Scope
Build exactly the seven changes in the issue:

1. `test.yml`: triggers are `pull_request`, `push` to `main` and `workflow_call`. Add top-level `permissions: contents: read`, per-ref concurrency with cancel-in-progress and a 20-minute job timeout. Keep the job id `test`. Pin `actions/checkout` and `actions/setup-python` to full SHAs with a `# vX.Y.Z` comment and turn on pip caching. Leave the Postgres service (port 5433 and its comment), the env and all steps unchanged.
2. `deploy.yml`: same triggers. Add `permissions: contents: read` and a non-cancelling `deploy-production` concurrency group. Add a `test` job that reuses `./.github/workflows/test.yml`. The `deploy` job gets `needs: test`, the `main` + `nickkmm/plates` `if`, `environment: production` and a 15-minute timeout. Pin `appleboy/ssh-action` to the SHA of its latest v1 release. Pass `fingerprint: ${{ secrets.VPS_SSH_FINGERPRINT }}`, and add a comment citing the action's docs showing that an empty value skips verification, so today's deploy keeps working. Pass `DEPLOY_SHA: ${{ github.sha }}` via `envs` and fetch/reset to that exact commit (`git fetch --depth 1 origin "$DEPLOY_SHA"` + `git reset --hard FETCH_HEAD`). Every other script line stays as it is.
3. `.github/dependabot.yml`: weekly updates for `pip`, `github-actions` and `docker` (all at `/`), limit 5 open PRs, minor and patch updates grouped per ecosystem.
4. `.github/workflows/codeql.yml`: CodeQL for `python` and `actions` on PRs to `main`, pushes to `main` and a weekly schedule. Least permissions (`contents: read`, `security-events: write`, `actions: read`). Actions pinned to SHAs.
5. `.github/CODEOWNERS`: `@nickkmm` owns the paths listed in the issue.
6. `SECURITY.md` (English): report vulnerabilities privately via GitHub private vulnerability reporting, never in a public issue; only `main` is supported; do not test against the production site.
7. `docs/ARCHITECTURE.md` `## CI/CD` (Spanish): update the workflow table, remove the "repo privado" and "nothing gates the deploy" wording, and add `### Configuración manual en GitHub` with the full checklist from the issue (including how to get `VPS_SSH_FINGERPRINT`).

The SHAs must come from the real upstream release tags, not be made up, and each needs a matching version comment. Verify against the issue's acceptance criteria (the grep for unpinned `uses:`, YAML load, `actionlint` if available, `factory/checks.sh`).

Left out, as the issue says: any change to GitHub settings, secrets, environments or rulesets (the owner applies the checklist by hand), application code, `docker-compose.yml`, `web.Dockerfile`, the VPS, git history and the README.

## Why
`.github/workflows/deploy.yml` currently deploys `origin/main` on every push without depending on `test.yml`. It uses `appleboy/ssh-action@v1` with no host-key fingerprint, and neither workflow has a `permissions` block. `docs/ARCHITECTURE.md` (lines 93-97) still says the deploy is not gated and the repo is private. The issue settles every design choice and comes with mechanical acceptance checks, so nothing is left for the author to decide. It is high risk only because a mistake in `deploy.yml` (the reusable-workflow call, the `environment`, the fingerprint input or the SHA fetch) would break or weaken production deploys, so reviewers should read that file line by line.
