ESCALATE

## Summary
- Docs-only job: `README.md` is rewritten in English as a portfolio page with the eleven sections the issue asks for, and the stale intro of `docs/README.md` is replaced with Spanish prose.
- The deploy secrets table, the SSH user, the `/opt/...` path and the "no admin UI" claim are gone. The banned-strings grep prints nothing.
- Gate round 1: `GATE: PASS` (1836 tests passed, no protected paths changed). The code review is `VERDICT: PASS` with only wording notes.
- No code, migration, env, Compose, Dockerfile, CI, auth or pricing change. The branch touches only `README.md` and `docs/README.md`.
- Escalated because a person merges every PR in this project. Recommendation: merge.

## Why
Every acceptance criterion is met, and I re-ran criteria 2, 3 and 4 myself. The change stays inside intake's Scope and hits none of the escalation triggers. The README is public copy about the business, but the issue author specified its content, so it is not an Assumption the factory made. The review notes are wording suggestions, not defects.

## Acceptance criteria
1. Sections in order, title followed by the pitch: met. The README has `# plates`, the pitch, then Features, How it works, Tech stack, Quality, CI/CD, Local development, Software factory, Documentation, Security, License / status.
2. Every relative link resolves: met. I re-ran the `test -e` loop and all 12 targets printed ok. The only link in the `docs/README.md` diff is unchanged.
3. Banned-strings grep (`root`, VPS secret names, `ENV_FILE_B64`, `/opt/`, IPv4): met. I re-ran it and it printed nothing.
4. Routes and files exist: met. I checked `/declaracion`, `/declaraciones`, `/ingest`, `/ingest/pdf`, `/cuadre`, `/cuadre/pdf`, `/records`, `/produccion`, `/produccion/<order_id>`, `/usuarios`, `/usuarios/roles`, `/precios`, `/customers`, `/inventory` and `/review` against the blueprint decorators. I also confirmed that the `AUTH_USERS`/`AUTH_ROLES` backfill exists in `app/schema_migrate.py`, that port 5433 matches `docker-compose.yml`, and that the labels «Perdí mis placas», *Falta autenticación* and *Documento distinto* appear in the code.
5. Exactly one mermaid block, starting with `flowchart TD`, labels quoted: met. Nobody has viewed it rendered on GitHub yet.
6. The "no admin UI" text is gone and `/usuarios` is mentioned: met.
7. The `docs/README.md` diff is a single hunk that only replaces the intro. It is Spanish, no longer says read-only, and covers the per-role permissions and the production flow: met.
8. Only `README.md` and `docs/README.md` changed: met (`git diff --stat`).

## For the human
Recommendation: merge

Reasons: this is a docs-only change. It meets every criterion, the gate and review passed, and it changes nothing that deploys or runs. Before you merge, check these three things:

1. **The pitch and the License / status wording in `README.md`.** This is the public description of your business. The last line says "No license is granted: all rights reserved." The spec only asked for "no license granted", so "all rights reserved" is an extra legal claim. Keep it or drop it, whichever you prefer.
2. **The mermaid diagram in `README.md` (How it works).** Open PR #102 on GitHub and confirm it renders. Also note that the edge label "Approved, plates worn" simplifies the rule in `app/core/db/declarations.py` (`declaration_stage`). The real rule is "approved, and no lost-plates card still waiting for notarization". You could relabel it "Approved, no notarization pending" if you want the diagram to match the code exactly.
3. **The new intro in `docs/README.md` (lines 3-9).** Confirm the Spanish wording reads well to you.

Unrelated to the change: `build.md` gives a wrong local test count (648 passed / 1188 skipped). The gate's real result is 1836 passed.
