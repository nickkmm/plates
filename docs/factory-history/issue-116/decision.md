ESCALATE

## Summary
- Phase 1 move of the orders domain: the ingest, records and review blueprints, ingestion (now `app/orders/service.py`), the pedido parsers and the orders/plates SQL went into a new `app/orders/` package with `git mv`. Git shows all nine files as renames.
- `relink_plates` and its helper (now public as `card_id_for_order_plate`) moved into `app/customers/repository/ownership_cards.py` with their bodies unchanged, so dependencies only point orders -> customers.
- `app/core/db` no longer re-exports the orders/plates names. Callers use `orders_repository.<name>`, strict mypy covers the new modules, and the docs point at the new paths.
- Gate round 1: `GATE: PASS` (ruff, mypy, 1844 tests, the same count as main). The code review is `VERDICT: PASS` with no findings.
- No migration, env var, Docker, CI, dependency, template or permission change. This is escalated only because a person always merges in this project.

## Why
Every acceptance criterion is met and I re-ran several of them myself. The final round's gate and review both pass, and the only Note is a cosmetic docstring line length. The change stays inside intake's Scope. It touches no protected area as a behavior change: the permission decorators, URL rules, SQL and price re-snapshot logic were moved word for word, and only import lines and call prefixes changed.

## Acceptance criteria
1. Old paths gone, 12 new files: met. build.md lists them, and the diff stat shows the nine renames and the new `__init__.py` files.
2. No dotted-name references to the old modules: met. I re-ran the `rg` over app, tests, docs, README.md and CLAUDE.md and it found nothing.
3. No file-path references to the old paths: met per build.md, and the reviewer saw only the allowed historical or mapping-table hits.
4. `app/core/db` stops re-exporting orders/plates: met. I ran the `hasattr` check (no `insert_order`, no `relink_plates`) and it passed. The diff removes the imports and the `__all__` entries.
5. One-way dependencies: met. I re-ran both `rg` checks (`app/customers` has no `app.orders`/`app.core.db`, and service, repository and parsers have no forbidden imports) and both were empty.
6. Modules import in fresh processes: met per build.md. I imported `app.web.app` myself and it worked.
7. Same URL map as origin/main: met per build.md (the branch and `git archive` outputs were identical). The route diffs I read change only imports and the `orders_repository.` prefix. The decorators and rules are unchanged.
8. Renames shown, and no templates/migrations/init.sql/navigation.py diff: met. The diff stat shows `=>` renames, and none of those paths appear in it.
9. `relink_plates`, `card_id_for_order_plate` and `TODO(phase 4)` are in ownership_cards.py: met. I read the diff, and the bodies match the originals.
10. Strict mypy override lists the six `app.orders.*` entries: met. I checked the pyproject.toml diff.
11. Same test count, no deleted tests, test diff limited to imports, prefixes and monkeypatch targets: met. The count is 1844 on both, and the test files show only small prefix and import changes.
12. Docs mention `app/orders`: met per build.md (CORE 20, WEB 6, ARCHITECTURE 4, README 1, CLAUDE.md 1).
13. Pages behave as on main: met through the allowed test fallback. Those suites gave 275 passed against `plates_test_21`, and the full gate passed.

## For the human
Recommendation: merge

- The change is mechanical. Every check that would block it passes, and it touches nothing on the review-first list: no migration, env var, `docker-compose.yml`/`web.Dockerfile`, login, roles or permissions, Turnstile form, tariff logic or cuadre totals. Behavior is unchanged.
- Merging deploys to production. The point of sale (`/ingest`) is the most critical flow and was covered by tests, not a browser check. If you want extra confidence, save one order on `/ingest` and edit one record on `/records/<id>` locally before merging.
- Worth a look:
  - `app/customers/repository/ownership_cards.py`: the new `relink_plates` and `card_id_for_order_plate`.
  - `app/orders/repository/plates.py`: now calls `card_id_for_order_plate`.
  - `app/orders/repository/__init__.py`: the re-exports.
  - `app/core/db/__init__.py`.
- Optional cleanup for later:
  - In `app/orders/routes/records.py`, the comment in `record_save` still says "ingestion.py's own duplicate-detection helper" and "no SQL ... outside app/core/". Both are out of date after the move.
  - The long docstring line in `app/customers/__init__.py` that the reviewer noted.
