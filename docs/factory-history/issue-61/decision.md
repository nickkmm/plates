ESCALATE

## Summary
- Issue #61 hardens CI/CD now that the repo is public. Deploys now reuse `test.yml` and wait for it (`needs: test`), run only for `main` of `nickkmm/plates`, go through `environment: production`, and deploy the exact tested SHA.
- All four third-party actions are pinned to real commit SHAs, and every workflow now declares least-privilege `permissions`. The PR also adds CodeQL, Dependabot, CODEOWNERS (`@nickkmm` only) and SECURITY.md, and documents the manual GitHub-settings checklist in Spanish in `docs/ARCHITECTURE.md`.
- Round 1: `GATE: PASS`, and the code and security reviews both say `VERDICT: PASS` with no findings. All 14 spec criteria are met, and I re-ran the pin check myself.
- I'm escalating because the change rewrites the deploy and CI config (`deploy.yml`, `test.yml`). Every push to `main` deploys to production, so a person has to check it.

## Why
The work matches the spec and stays inside intake's Scope. The only extra edit is a one-phrase `AGENTS.md` fix, which the spec justifies. The gate and both reviews are clean. Still, the change edits deploy and CI configuration, and the factory rules require a human in that case. The first real test of the new deploy job will be the merge into `main` itself.

## Acceptance criteria
1. All actions pinned: met. I re-ran `grep -nE "uses: [^.].*@" .github/workflows/*.yml | grep -vE "@[0-9a-f]{40}"` in the worktree and it printed nothing.
2. Pins are real: met. I checked two myself with `git ls-remote`. `appleboy/ssh-action` `v1.2.5` resolves to `0ff4204d...`, which is also the latest v1 tag, and `actions/checkout` `v7.0.1` resolves to `3d3c42e5...`. The build and the security review checked all four.
3. Every workflow has top-level `permissions`: met (seen in the diff for test, deploy and codeql).
4. YAML parses and actionlint passes: met according to build.md (PyYAML load, actionlint 1.7.12 with no errors).
5. `test.yml` triggers and settings: met. The diff shows PR, push to `main` and `workflow_call`, `contents: read`, the workflow+ref concurrency group with cancel-in-progress, a 20-minute timeout and the pip cache. The service, env and `run:` lines are untouched.
6. `deploy.yml` wiring: met. The diff shows `needs: test`, the exact `if`, `environment: production`, a 15-minute timeout, the `deploy-production` concurrency group without cancel, `fingerprint` with its comment, and `DEPLOY_SHA` in both `envs` and `env`.
7. Deploy script changes limited to the fetch and reset lines: met (diff).
8. Dependabot config: met (pip, github-actions and docker; `/`; weekly; limit 5; minor and patch grouped).
9. CodeQL: met (triggers, exactly three permissions, python and actions matrix, init and analyze on the same SHA).
10. CODEOWNERS: met. It lists the 11 paths, all owned by `@nickkmm`, with no other account.
11. SECURITY.md: met. It is in English and covers private reporting, no public issues, `main`-only support and no testing against production.
12. ARCHITECTURE.md: met. It has the new table and the gate paragraph, `privado` is gone, and the `### Configuración manual en GitHub` checklist has all 7 items including the `ssh-keyscan` command.
13. AGENTS.md: met. "every push to" is gone and the sentence now says the deploy waits for `test.yml`.
14. Files touched: met. The diff touches only the 8 allowed files.

## For the human
Recommendation: merge

Reasons:
- The change matches the issue and the spec line by line. The gate passed, both reviews passed with no findings, and the pins I checked are real upstream tags.
- I'm escalating only because this changes deploy and CI config (`.github/workflows/deploy.yml`, `.github/workflows/test.yml`). In this project a push to `main` deploys to production and runs the migrations.

What to check before or right after merging:
1. Read `.github/workflows/deploy.yml` line by line. It has the reusable `test` job, `needs: test`, the `main`/repo `if`, `environment: production`, the `fingerprint` input, and the fetch/reset to `"$DEPLOY_SHA"` / `FETCH_HEAD`.
2. After the merge, watch the first "Deploy" run in Actions. The `test` job should pass, then `deploy` should run, and the VPS should end up on the merge commit (`git -C /opt/plates rev-parse HEAD`). If the `production` environment doesn't exist yet, GitHub creates it on that first run. The secrets still work while they live at repo level.
3. Then apply the manual checklist in `docs/ARCHITECTURE.md` under `### Configuración manual en GitHub`. Until you create `VPS_SSH_FINGERPRINT`, the deploy does **not** verify the VPS host key, the same as today. The value is the `SHA256:...` field from `ssh-keyscan -t ed25519 <host> | ssh-keygen -lf -`. Once you move the secrets into the `production` environment, delete the repo-level copies.

Optional follow-ups the security review suggested (not blockers): make the deploy fail when the fingerprint secret is missing, and set `persist-credentials: false` on checkout.

Files to look at: `.github/workflows/deploy.yml`, `.github/workflows/test.yml`, `.github/workflows/codeql.yml`, `.github/dependabot.yml`, `.github/CODEOWNERS`, `SECURITY.md`, `docs/ARCHITECTURE.md`, `AGENTS.md`.
