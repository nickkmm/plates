# Harden GitHub Actions for the public repo

> Issue #61: Harden GitHub Actions for the public repo

## Summary
- Intake accepted the issue as written (high risk, security surface). This spec turns its seven changes into a build list with checks a reviewer can run.
- Production deploys will wait for `test.yml` (reused through `workflow_call`), run only for `main` in `nickkmm/plates`, go through the `production` environment, and deploy the exact tested SHA.
- Every third-party action is pinned to a real upstream commit SHA, and every workflow gets least-privilege `permissions`. This adds Dependabot, CodeQL, CODEOWNERS and SECURITY.md.
- `docs/ARCHITECTURE.md` `## CI/CD` (Spanish) gets the new pipeline and the manual GitHub-settings checklist. The one-line deploy description in `AGENTS.md` is corrected.
- One deliberate change to the issue's text: the `test.yml` concurrency group also includes `github.workflow`, so the deploy's reused test run and the standalone push run on `main` don't cancel each other (see Assumptions).

## Context
- `.github/workflows/test.yml`: triggers on bare `push` and `pull_request`. It has one job `test` with a `postgres:16` service published on host port 5433 (a long comment explains why), job-level `env` (`POSTGRES_USER`, `POSTGRES_PASSWORD`), and the steps checkout (`actions/checkout@v4`), setup-python (`actions/setup-python@v5`, Python 3.11), install `requirements-dev.txt`, `ruff check app/`, `mypy app/` and `pytest --cov=app --cov-report=term-missing`. It has no `permissions`, `concurrency` or timeout.
- `.github/workflows/deploy.yml`: triggers on `push` to `main` and `workflow_dispatch`. It has one job `deploy` using `appleboy/ssh-action@v1` (a moving tag) with `host`/`username`/`key` from secrets `VPS_HOST`/`VPS_USER`/`VPS_SSH_KEY` and `envs: ENV_FILE_B64`. Its script runs `git fetch --depth 1 origin main` + `git reset --hard origin/main`, writes `.env` from base64, then runs `docker compose up -d --build`, `schema_migrate` and `ps`. It has no test dependency, no environment, no host-key check and no `permissions`.
- There is no `.github/dependabot.yml`, `.github/CODEOWNERS`, `SECURITY.md` or `codeql.yml`.
- `docs/ARCHITECTURE.md` lines 86-103 (`## CI/CD`): a two-row workflow table, then a paragraph (lines 93-97) saying deploy doesn't re-run tests, that no branch protection enforces it, and that the repo is "(privado)". After that comes a paragraph about `schema_migrate` that stays.
- `AGENTS.md` line 17-18 says the app is "deployed to a VPS by `.github/workflows/deploy.yml` on every push to `main`".
- `requirements.txt` exists at the root and `requirements-dev.txt` includes it with `-r requirements.txt`. `web.Dockerfile` is `FROM python:3.11-slim`. `init.sql`, `app/migrations/`, `scripts/`, `factory/`, `.claude/` and `.agents/` all exist.
- `factory/checks.sh` and `factory/config.json` are protected paths and must not be touched.

## Requirements
1. **`test.yml` triggers**: `pull_request` (no branch filter), `push` with `branches: [main]`, and `workflow_call`. The bare every-branch `push` is gone.
2. **`test.yml` hardening**: top-level `permissions: contents: read`. Top-level `concurrency` with `group: test-${{ github.workflow }}-${{ github.ref }}` and `cancel-in-progress: true`. Job id stays `test` with `timeout-minutes: 20`.
3. **`test.yml` pinning**: `actions/checkout` and `actions/setup-python` are pinned to the full 40-hex commit SHA of a real upstream release tag (the latest release of the current major or newer), each with a trailing `# vX.Y.Z` comment naming that exact tag. Setup-python gets `cache: pip` and `cache-dependency-path` listing `requirements.txt` and `requirements-dev.txt`.
4. **`test.yml` unchanged parts**: the Postgres service block (including the 5433 comment), the job `env` block and its comment, the Python 3.11 comment, and every step's command stay byte-identical apart from the `uses:` lines and the added `with:` cache keys.
5. **`deploy.yml` top level**: triggers stay `push: branches: [main]` and `workflow_dispatch`. Add top-level `permissions: contents: read` and `concurrency: { group: deploy-production, cancel-in-progress: false }`.
6. **`deploy.yml` jobs**:
   - Job `test` is `uses: ./.github/workflows/test.yml`.
   - Job `deploy` has `needs: test`, `if: github.ref == 'refs/heads/main' && github.repository == 'nickkmm/plates'`, `environment: production`, `timeout-minutes: 15` and `runs-on: ubuntu-latest`.
7. **`deploy.yml` SSH action**:
   - Pin `appleboy/ssh-action` to the full commit SHA of its latest `v1.x.y` release, with a `# v1.x.y` comment.
   - Add `fingerprint: ${{ secrets.VPS_SSH_FINGERPRINT }}`. Above it, add a comment saying that, per the action's README, an empty `fingerprint` skips host-key verification, so the deploy keeps working until the owner creates the secret. The builder must check this in the README of the pinned version before writing the comment. If the README says otherwise, stop and report it in the build notes instead of shipping a deploy that breaks.
8. **`deploy.yml` exact-SHA deploy**:
   - `envs` becomes `ENV_FILE_B64,DEPLOY_SHA`, and the step `env` adds `DEPLOY_SHA: ${{ github.sha }}`.
   - In the script, `git fetch --depth 1 origin main` becomes `git fetch --depth 1 origin "$DEPLOY_SHA"` and `git reset --hard origin/main` becomes `git reset --hard FETCH_HEAD`. Every other script line stays as it is and in the same order.
9. **`.github/dependabot.yml`** (`version: 2`) has three `updates` entries, `pip`, `github-actions` and `docker`, each with `directory: "/"`, `schedule.interval: weekly` and `open-pull-requests-limit: 5`. Each entry has one `groups` entry whose `update-types` are `minor` and `patch`.
10. **`.github/workflows/codeql.yml`**:
    - Triggers: `pull_request` to `main`, `push` to `main` and a weekly `schedule` cron.
    - Top-level `permissions` is exactly `contents: read`, `security-events: write` and `actions: read`.
    - A matrix over `language: [python, actions]` with `build-mode: none`.
    - `actions/checkout`, `github/codeql-action/init` and `github/codeql-action/analyze` are pinned to full SHAs of real release tags with `# vX.Y.Z` comments. Init and analyze use the same SHA.
11. **`.github/CODEOWNERS`** lists `@nickkmm` as owner of `/.github/`, `/factory/`, `/.claude/`, `/.agents/`, `/docker-compose.yml`, `/web.Dockerfile`, `/init.sql`, `/app/migrations/`, `/scripts/`, `/AGENTS.md` and `/CLAUDE.md`, and of nothing else. No other account appears.
12. **`SECURITY.md`** sits at the repo root and is in English. It says:
    - this is the production app of a real business;
    - vulnerabilities are reported privately through GitHub's "Report a vulnerability" button (private vulnerability reporting) and never in a public issue, PR or discussion;
    - only `main` is supported;
    - nobody may test against the production site or its infrastructure.
13. **`docs/ARCHITECTURE.md` `## CI/CD`** (Spanish prose):
    - Update the table: the new `test.yml` triggers (PR, push to `main`, `workflow_call`), the new `deploy.yml` flow (reuses `test.yml` → `environment: production` → fetch/reset to the exact SHA → `.env` → compose → `schema_migrate`), and new rows for `codeql.yml`. Mention Dependabot in a sentence or a row.
    - Remove the paragraph that says nothing gates the deploy and that the repo is private. Replace it with a short paragraph explaining the new gate.
    - Keep the `schema_migrate` paragraph.
    - Add `### Configuración manual en GitHub`, a checklist (`- [ ]`) with every item from the issue:
      - a ruleset on `main` (PR required, required check `test`, code-owner review, block force pushes and deletion);
      - secret scanning + push protection;
      - Dependabot alerts and security updates;
      - private vulnerability reporting;
      - Actions: require approval for workflows from outside collaborators, and require SHA-pinned actions;
      - an environment `production` restricted to `main`, holding `VPS_HOST`, `VPS_USER`, `VPS_SSH_KEY`, `ENV_FILE_B64` and `VPS_SSH_FINGERPRINT` (obtained with `ssh-keyscan -t ed25519 <host> | ssh-keygen -lf -`), with the repo-level copies of those secrets deleted;
      - a non-root deploy user on the VPS.
14. **`AGENTS.md`**: change "on every push to `main`" in the Project paragraph so it says the deploy runs on pushes to `main` once `test.yml` passes. Make no other change to `AGENTS.md`.

## Acceptance criteria
1. Every action is pinned. `cd` to the worktree and run `grep -nE "uses: [^.].*@" .github/workflows/*.yml | grep -vE "@[0-9a-f]{40}"`. It prints nothing.
2. The pins are real. For each pinned `uses:` line, `git ls-remote https://github.com/<owner>/<repo> refs/tags/<tag>` (and `refs/tags/<tag>^{}` for annotated tags) resolves the tag named in the line's `# vX.Y.Z` comment to that exact SHA.
3. Every workflow declares permissions. `grep -L "^permissions:" .github/workflows/*.yml` prints nothing.
4. The workflows parse. `python -c "import yaml,glob; [yaml.safe_load(open(f)) for f in glob.glob('.github/**/*.yml', recursive=True)]"` exits 0 (install PyYAML in a throwaway venv if needed; do not add it to the requirements files). If `actionlint` is available, it reports no errors on `.github/workflows/`.
5. `test.yml` has the right triggers and settings:
   - `on` has exactly the keys `pull_request`, `push` and `workflow_call`, with `push.branches == ["main"]`;
   - `permissions == {"contents": "read"}`;
   - `concurrency.cancel-in-progress` is true and `concurrency.group` contains `github.workflow` and `github.ref`;
   - `jobs.test.timeout-minutes == 20`;
   - the setup-python step has `cache: pip`.
   
   `git diff origin/main -- .github/workflows/test.yml` shows no change to the service, env or `run:` lines.
6. `deploy.yml` is wired up correctly:
   - `jobs.test.uses == "./.github/workflows/test.yml"`;
   - `jobs.deploy` has `needs: test`, `environment: production`, `timeout-minutes: 15` and the exact `if` from Requirement 6;
   - `concurrency` is `deploy-production` with `cancel-in-progress: false`;
   - the step has `fingerprint: ${{ secrets.VPS_SSH_FINGERPRINT }}` with the README comment above it;
   - `envs` includes `DEPLOY_SHA`, and the step env sets `DEPLOY_SHA: ${{ github.sha }}`.
7. The deploy script changed only where it should. `git diff origin/main -- .github/workflows/deploy.yml` shows that the only changed script lines are the fetch line (now `git fetch --depth 1 origin "$DEPLOY_SHA"`) and the reset line (now `git reset --hard FETCH_HEAD`).
8. `.github/dependabot.yml` loads with `pip`, `github-actions` and `docker`. Each has directory `/`, a weekly interval, a limit of 5, and a group with minor and patch update types.
9. `codeql.yml` is correct:
   - its triggers are a PR to `main`, a push to `main` and a `schedule`;
   - its `permissions` mapping has exactly the three keys from Requirement 10;
   - its matrix languages are `python` and `actions`.
10. `.github/CODEOWNERS` contains exactly the 11 paths from Requirement 11, each owned by `@nickkmm`. `grep -i meyi .github/CODEOWNERS` prints nothing.
11. `SECURITY.md` exists at the root and covers private reporting via "Report a vulnerability", no public issues, `main`-only support, and no testing against production.
12. `docs/ARCHITECTURE.md`:
    - contains `### Configuración manual en GitHub` with all checklist items from Requirement 13, including the `ssh-keyscan -t ed25519 <host> | ssh-keygen -lf -` command;
    - `grep -n "privado" docs/ARCHITECTURE.md` no longer matches the CI/CD paragraph;
    - the table lists `test.yml`, `deploy.yml` and `codeql.yml` with their new triggers.
13. `grep -n "every push to" AGENTS.md` prints nothing, and the Project paragraph says the deploy waits for `test.yml`.
14. `git diff --stat origin/main` touches only:
    - `.github/workflows/test.yml`, `.github/workflows/deploy.yml` and `.github/workflows/codeql.yml`;
    - `.github/dependabot.yml` and `.github/CODEOWNERS`;
    - `SECURITY.md`, `docs/ARCHITECTURE.md` and `AGENTS.md`;
    - the factory's own `docs/factory-history/` record, if present.

## Out of scope
- Changing any GitHub setting, secret, environment, ruleset or branch protection, through the API, `gh` or otherwise. The owner applies the checklist by hand.
- Any change to application code (`app/`, `tests/`), `docker-compose.yml`, `web.Dockerfile`, `init.sql`, `scripts/`, requirements files or the VPS.
- `README.md`, including its CI/CD lines (a separate task rewrites it), and `PLAN_ARQUITECTURA_Y_POS.md`.
- Rewriting git history.
- `factory/checks.sh` and `factory/config.json` (protected), and any other `factory/` file.
- Extra workflows, scanners (e.g. Scorecard, zizmor), or `harden-runner` steps the issue does not list. Also: changing `test.yml` steps (e.g. dropping `pip install --upgrade pip`), adding a `pull_request_target` trigger, or adding a Dependabot auto-merge.
- Making `fingerprint` mandatory, or failing the deploy when the secret is missing.

## Assumptions
- **The `test.yml` concurrency group is `test-${{ github.workflow }}-${{ github.ref }}`, not the issue's `test-${{ github.ref }}`.** A push to `main` starts both `test.yml` and `deploy.yml`, and `deploy.yml` reuses `test.yml`. With the literal group, both runs share `test-refs/heads/main` and cancel each other, which could cancel the deploy's `test` job and skip production deploys. In a called workflow, `github.workflow` is the caller's name, which keeps the two groups apart. This is a technical fix that keeps the issue's intent.
- **The pip cache keys on both `requirements.txt` and `requirements-dev.txt`.** The installed set comes from both files, so a change to either should refresh the cache.
- **Action versions**: "latest release of the current major or newer" for checkout, setup-python and codeql-action. The issue only asks for SHA pins with version comments, and the newest release gets the latest fixes. For `appleboy/ssh-action`, the issue explicitly says the latest v1.
- **CodeQL uses `build-mode: none`**. Python and Actions need no build step, so no autobuild is added.
- **AGENTS.md gets a one-phrase update.** Its "deployed on every push to `main`" sentence becomes false after this change, and the spec rules require keeping AGENTS.md accurate. Intake did not list this edit, but it is a direct consequence of the change.
- **If the pinned ssh-action README shows that an empty `fingerprint` does *not* skip verification**, the builder stops and reports it rather than inventing a workaround. The issue's safety condition, that the deploy keeps working, would otherwise be broken, and how to proceed then is the owner's call.
