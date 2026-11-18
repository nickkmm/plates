VERDICT: PASS

## Summary
- Docs-only change: `README.md` is rewritten in English with the eleven sections in the order the spec asks for, and the stale intro of `docs/README.md` is replaced with Spanish prose.
- Checked the README's routes, nav labels, declaration stages and flags, production statuses, form rules, CI/CD triggers, tech stack and linked doc sections against the code on this branch. All of them match.
- The deploy secret names, SSH user, host, IP and `/opt/` path are gone, and the word `root` does not appear.
- Link targets resolve, there is one mermaid block starting with `flowchart TD`, and the branch touches only the two spec files.
- The remaining points are wording refinements, none of them behaviour or spec gaps.

## Findings
None.

## Notes
1. `README.md` (License / status): "No license is granted: all rights reserved." adds a legal claim the spec did not ask for. Spec item 10 only says "no license granted". Consider dropping "all rights reserved" unless the owner wants it.
2. `README.md` (How it works, mermaid): the edge `Approved, plates worn` reads as the only route to *Lista para pedido*. The code rule (`app/core/db/declarations.py`, `declaration_stage`) is "approved, and no PÉRDIDA card still awaiting notarization", so any approved declaration without that case goes there. A label such as `Approved, no notarization pending` would match the code exactly. Non-blocking.
3. `README.md` (Quality): "mypy over `app` and `tests`" matches `[tool.mypy] files` in `pyproject.toml`, but `.github/workflows/test.yml` runs `mypy app/` only. The README describes the config, which is accurate, so no change is needed unless you want the README to describe CI.
4. `README.md` (Quality): the spec said "mypy (strict on core)". The README says `disallow_untyped_defs` on `app/core`, which is what the config sets. That is the accurate description, so I would keep it.
5. The build record (`round-1/build.md`, Validation section) says the gate ran "648 passed / 1188 skipped". The gate's own output (`round-1/checks.md`) shows "1836 passed". The README is unaffected, but the build record is wrong and should be corrected in the job's history.
6. Commit `af881b7` has a one-line subject in English, which matches the convention. Its trailer is the builder's and is not reviewed here.
