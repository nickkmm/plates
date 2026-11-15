VERDICT: PASS

## Summary
- Deploys now run only after the reused `test.yml` job passes, only for `main` of `nickkmm/plates`, through the `production` environment, and they deploy the exact tested SHA (`github.sha` -> `FETCH_HEAD`).
- All four third-party actions are pinned to full commit SHAs. I re-checked each one with `git ls-remote` and each matches the tag in its `# vX.Y.Z` comment (codeql-action v4.39.3 through the peeled `^{}` ref).
- Every workflow declares least-privilege `permissions`. There is no `pull_request_target` and no `secrets: inherit`, so fork PRs only get a read-only token and no secrets.
- CODEOWNERS (owned by `@nickkmm` only), SECURITY.md (private reporting, no testing against production), Dependabot and CodeQL were added. The docs checklist leaks no hosts, IPs, fingerprints or secrets.
- No application code, requirements, `.env`, keys or business data were touched or committed.

## Findings
None.

## Notes
- `.github/workflows/deploy.yml:36` Host-key verification stays off until the owner creates `VPS_SSH_FINGERPRINT`. In the meantime the deploy can still be attacked by someone in the middle of the SSH connection (MITM), as it could before this change. The spec requires this, and the workflow comment and the docs checklist both say so. The owner should create the secret soon. A follow-up could make the deploy fail when the secret is missing.
- `appleboy/ssh-action` is a composite action that downloads a `drone-ssh` binary when it runs. The SHA pin fixes `action.yml` and the binary version it names, but the binary itself comes from a GitHub release, not from the pinned commit. This is normal for this action and not made worse by the change.
- `actions/checkout` keeps the default `persist-credentials: true` in `test.yml` and `codeql.yml`. The token can only read `contents`, and only first-party steps run afterwards, so the risk is low. Setting `persist-credentials: false` would be a small optional hardening step later (the spec puts it out of scope here).
- `workflow_dispatch` from a non-`main` ref still runs the `test` job, but `deploy` is skipped by the `if`. Restricting the `production` environment to `main` (checklist item) adds a second guard on the secrets.
- The pip cache in `test.yml` is safe from PR poisoning: GitHub scopes caches written by PRs to the PR ref, so runs on `main` don't restore them.
