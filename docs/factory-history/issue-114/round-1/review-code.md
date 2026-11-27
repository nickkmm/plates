VERDICT: PASS

## Summary
- The customers, pricing and inventory routes and repositories moved into `app/customers/`, `app/pricing/` and `app/inventory/`. The moves are git renames and the change is otherwise limited to imports, call prefixes and docstrings.
- The 30 re-exports are gone from `app/core/db/__init__.py`. Callers go through the repository modules, using the `customers_repository`, `pricing_repository` and `inventory_repository` aliases outside each domain.
- The import cycle is avoided with a module import in the customers repository and leaf imports in `plates.py`, as the spec describes.
- The strict mypy override, the docs and the `CLAUDE.md` bullet are updated. The checks pass with 1844 tests, the same count as main.
- I re-ran the spec's rg checks 2, 3 and 6 and all three found nothing.

## Findings
None.

## Notes
- `app/core/db/plates.py`: the first docstring line now runs past the neighbouring line width. This is cosmetic and ruff passes.
- Acceptance criterion 13 (page rendering) was checked over HTTP by diffing the HTML from the branch against main, which is an acceptable substitute.
