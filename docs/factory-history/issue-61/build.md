STATUS: READY

# Build: Harden GitHub Actions for the public repo

## Summary
- Deploys now reuse `test.yml` (`needs: test`), run only for `main` of `nickkmm/plates`, go through `environment: production`, and deploy the exact tested SHA.
- Every third-party action is pinned to a verified upstream commit SHA. Every workflow declares least-privilege `permissions`.
- Added CodeQL (python + actions), Dependabot (pip, github-actions, docker), CODEOWNERS (`@nickkmm` only) and SECURITY.md.
- `docs/ARCHITECTURE.md` `## CI/CD` describes the new pipeline and has the manual GitHub-settings checklist. The deploy sentence in AGENTS.md is fixed.
- Draft PR: https://github.com/nickkmm/plates/pull/71

## Changes
- `.github/workflows/test.yml`: triggers `pull_request`, `push: branches: [main]`, `workflow_call`. Top-level `permissions: contents: read`. Concurrency `test-${{ github.workflow }}-${{ github.ref }}` with cancel-in-progress. `timeout-minutes: 20`. `actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1 # v7.0.1`, `actions/setup-python@6d4fd22b20465ccc0f7354476588e8df7c9ca0e0 # v7.1.0` with `cache: pip` and `cache-dependency-path` (requirements.txt, requirements-dev.txt). Service, env, comments and run steps are unchanged.
- `.github/workflows/deploy.yml`: `permissions: contents: read`. Concurrency `deploy-production` with no cancel. Job `test` is `uses: ./.github/workflows/test.yml`. Job `deploy` has `needs: test`, the main/repo `if`, `environment: production`, `timeout-minutes: 15`. `appleboy/ssh-action@0ff4204d59e8e51228ff73bce53f80d53301dee2 # v1.2.5` with `fingerprint: ${{ secrets.VPS_SSH_FINGERPRINT }}` and the README comment above it. `envs: ENV_FILE_B64,DEPLOY_SHA` and `DEPLOY_SHA: ${{ github.sha }}`. Fetch/reset now use `"$DEPLOY_SHA"` / `FETCH_HEAD`. Every other script line is unchanged.
- `.github/workflows/codeql.yml` (new): PR/push to `main` plus weekly cron. Permissions are exactly contents read, security-events write and actions read. Matrix `language: [python, actions]` with `build-mode: none`. Checkout v7.0.1 and codeql-action init/analyze `@33d7b85da1a04171e211befe2c739303b9a4c066 # v4.39.3` (same SHA for both).
- `.github/dependabot.yml` (new): pip, github-actions and docker. Each has directory `/`, weekly schedule, limit 5, and one group with minor and patch update types.
- `.github/CODEOWNERS` (new): the 11 paths from the spec, all owned by `@nickkmm`.
- `SECURITY.md` (new, English): production app of a real business. Report privately through "Report a vulnerability" and never in a public issue, PR or discussion. Only `main` is supported. No testing against production or its infrastructure.
- `docs/ARCHITECTURE.md`: new CI/CD table (test/deploy/codeql), a permissions/pinning/Dependabot paragraph, and a deploy-gate paragraph that replaces the "no vuelve a correr los tests … (privado)" paragraph. The `schema_migrate` paragraph is kept. New `### Configuración manual en GitHub` checklist with all 7 items.
- `AGENTS.md`: "on every push to `main`" became "on pushes to `main`, once `test.yml` passes".

## Acceptance criteria
1. [x] Every action is pinned: `grep -nE "uses: [^.].*@" .github/workflows/*.yml | grep -vE "@[0-9a-f]{40}"` printed nothing.
2. [x] Pins are real. `git ls-remote` results: checkout `refs/tags/v7.0.1` = 3d3c42e5… (lightweight); setup-python `refs/tags/v7.1.0` = 6d4fd22b… (lightweight); codeql-action `refs/tags/v4.39.3^{}` = 33d7b85d… (annotated, tag object f3bbb056…); ssh-action `refs/tags/v1.2.5` = 0ff4204d… (lightweight). Each is the latest semver tag of its repo: checkout v7 and setup-python v7 are newer majors than the old v4/v5, and v1.2.5 is the latest v1 of ssh-action.
3. [x] `grep -L "^permissions:" .github/workflows/*.yml` printed nothing.
4. [x] The spec's `yaml.safe_load` one-liner exits 0 (PyYAML is already in the venv through the dev requirements; no requirement file changed). actionlint 1.7.12 (Docker image `rhysd/actionlint`) on `.github/workflows/*.yml`: exit 0, no errors.
5. [x] A scratch assertion script confirmed: `on` keys are {pull_request, push, workflow_call} with push.branches == ["main"]; permissions == {contents: read}; concurrency cancel-in-progress true and the group has github.workflow and github.ref; timeout 20; setup-python `cache: pip`. `git diff origin/main -- .github/workflows/test.yml` changes only triggers/permissions/concurrency/timeout, the two `uses:` lines and the cache keys. No service, env or `run:` line changed.
6. [x] Same script: `jobs.test.uses == "./.github/workflows/test.yml"`; deploy has needs test, environment production, timeout 15 and the exact `if`; concurrency {deploy-production, false}; fingerprint is set with the comment above it; envs `ENV_FILE_B64,DEPLOY_SHA`; step env `DEPLOY_SHA: ${{ github.sha }}`.
7. [x] `git diff origin/main -- .github/workflows/deploy.yml`: the only script lines that changed are the fetch line (`git fetch --depth 1 origin "$DEPLOY_SHA"`) and the reset line (`git reset --hard FETCH_HEAD`).
8. [x] Script: dependabot `version: 2` with ecosystems docker, github-actions and pip. Each has `/`, weekly, limit 5, and a single group with `[minor, patch]`.
9. [x] Script: codeql has PR to main, push to main and a schedule; permissions are exactly the 3 keys; matrix languages are [python, actions].
10. [x] CODEOWNERS has the 11 paths, each `@nickkmm`. `grep -i meyi .github/CODEOWNERS` printed nothing.
11. [x] SECURITY.md at the root covers private reporting via "Report a vulnerability", no public issue/PR/discussion, `main`-only support, and no testing against production.
12. [x] ARCHITECTURE.md has `### Configuración manual en GitHub` with all 7 items, including `ssh-keyscan -t ed25519 <host> | ssh-keygen -lf -`. `grep -n "privado" docs/ARCHITECTURE.md` prints nothing. The table lists test.yml, deploy.yml and codeql.yml with their new triggers.
13. [x] `grep -n "every push to" AGENTS.md` printed nothing. The Project paragraph says the deploy runs "once `test.yml` passes".
14. [x] `git diff --stat origin/main` lists only the 8 allowed files (codeql/deploy/test workflows, dependabot.yml, CODEOWNERS, SECURITY.md, ARCHITECTURE.md, AGENTS.md).

## Validation
- `bash factory/checks.sh` from the worktree: passed (577 passed, 930 DB tests skipped locally because no Postgres was running; shell and factory script checks OK).
- Pin check grep, permissions grep, PyYAML load and the structural assertion script: all passed.
- actionlint 1.7.12 via Docker: no errors.
- `git ls-remote` for all four pinned tags: SHAs match.

## Log
### Round 1
- Set up `.venv` with `requirements-dev.txt`. Resolved the latest release tags with `git ls-remote`.
- Fingerprint check (spec requirement 7): the README of ssh-action v1.2.5 lists `fingerprint` as optional with no default and presents host-fingerprint verification as an opt-in step. It does not state the empty-value behaviour word for word. I confirmed it in the source: action.yml passes `INPUT_FINGERPRINT` to drone-ssh, and easyssh-proxy uses `ssh.InsecureIgnoreHostKey()` unless `config.Fingerprint != ""`. So an empty or missing secret skips verification and the deploy keeps working. The workflow comment says this and names both sources. The README does not contradict the spec, so I did not block.
- The docs checklist says the secret is only the `SHA256:...` field of the `ssh-keyscan | ssh-keygen -lf -` output, because easyssh-proxy compares it to `ssh.FingerprintSHA256` (format `SHA256:<base64>`).
- Made four commits (workflows; CodeQL+Dependabot; CODEOWNERS+SECURITY; docs) and pushed. Opened draft PR #71. Following CLAUDE.md, the PR body has no "Generated with" line.
