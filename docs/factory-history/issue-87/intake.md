DECISION: ACCEPT
RISK: high
SURFACES: security
RESCOPED: no

## Summary
- Accepted as written: the issue is a complete, self-contained spec for one PR (part of #86).
- Adds a `permissions` table seeded with the 13 codes from `app/core/permissions.py::PERMISSIONS`, and makes `role_permissions.permission` a foreign key to it.
- Risk is high because it is a migration that deletes rows and adds a constraint on the table that decides route access. Access itself does not change.
- No screen changes. Routes keep checking the Python codes.

## Scope
Build exactly what issue #87 specifies:
- Migration `0017_permissions.sql`, which can be re-run safely. It creates `permissions(code PK, label UNIQUE, sort_order)` and seeds the 13 code/label pairs in `PERMISSIONS` order (sort_order 1 to 13, labels copied byte for byte). It deletes any `role_permissions` row whose code is not in the catalog. Then it adds `role_permissions_permission_fkey` (ON DELETE CASCADE ON UPDATE CASCADE) inside a guarded `DO` block that filters by `conname` and `conrelid`, the same pattern as 0015.
- `init.sql`: create `permissions` with the same definition and seed before `role_permissions`, and declare the FK inline under the same constraint name. A fresh database and a migrated database must produce the same schema (the existing invariant test). Update the comments that still say the codes are validated only in Python.
- `app/core/db/roles.py`: add `PermissionRecord(code, label)` and `list_permissions(conn)`, ordered by `sort_order`. Export both from `app/core/db/__init__.py` (both the imports and `__all__`, in alphabetical order). Keep `_validated` and everything in `app/core/permissions.py` unchanged apart from the docstrings.
- Tests:
  - Add `permissions` to `_APP_TABLES`.
  - The migration can be applied twice without error.
  - In a throwaway schema, a row with an unknown code is deleted, every other row is kept, and the FK is created exactly once.
  - The table matches the `PERMISSIONS` tuple exactly (drift guard).
  - `list_permissions` returns the 13 records in order.
  - Inserting an unknown code directly in SQL raises `ForeignKeyViolation`.
- Docs, in Spanish prose:
  - `docs/DATABASE.md`: a new `permissions` section, the updated `role_permissions` section, and the mermaid diagram edge.
  - `docs/CORE.md`: the new function and dataclass.

Left out, as the issue says: `user_roles` and `users.role`, reading the catalog from the table in `permission_required`, `effective_permissions` or the navigation, and any screen, new code or change to which route needs which code.

## Why
The issue matches the code. `PERMISSIONS` in `app/core/permissions.py` has exactly the 13 codes and labels it lists. The next free migration number is 0017. `0015_roles_and_permissions.sql` gives the guarded-FK pattern and only seeds codes that are in the catalog, so adding the FK cannot fail on seeded data. Deleting rows with unknown codes changes no access, because `effective_permissions` already intersects stored codes with `PERMISSION_CODES`.

## Questions
