VERDICT: PASS

## Summary
- Adds a `permissions` catalog table (migration 0017 and `init.sql`) seeded with the 13 codes from `app/core/permissions.py`.
- `role_permissions.permission` is now an FK, so the database itself rejects unknown codes. This tightens access control and does not loosen it.
- The migration deletes only `role_permissions` rows whose codes are not in the catalog. Those rows already granted nothing, because `effective_permissions` intersects stored codes with the catalog.
- The new read function `list_permissions` uses a static, parameter-free query and is not exposed through any route.
- The change adds no endpoints, user input paths, dependencies or secrets.

## Findings
None.

## Notes
- `ON DELETE CASCADE` from `permissions(code)` means deleting a catalog row silently removes that permission from every role. This can only take access away and never grants it, so it is safe. A future roles screen should still keep the catalog read-only, since the Python tuple is the source of truth.
- Route checks still use the Python codes through `permission_required` and `effective_permissions`, which this branch does not change. The admin bypass and the access of each seeded role are unchanged, and `tests/web/` is untouched.
- The SQL in `app/core/db/roles.py` (`list_permissions`) is a constant string with no interpolation. The migration and `init.sql` contain only static seed values.
- The commits contain no `.env` files, keys, `dozzle_users.yml` or business data.
