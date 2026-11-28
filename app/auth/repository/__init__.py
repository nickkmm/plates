"""SQL for the auth domain: the users, roles and refresh_tokens tables, one module per table.

    users.py -- users / user_roles reads/writes (the login flow in
                app/auth/decorators.py and the admin screen).
    roles.py -- roles / role_permissions / permissions reads/writes (the
                permission catalog itself lives in app/auth/permissions.py,
                mirrored in the `permissions` table).
    refresh_tokens.py -- refresh_tokens reads/writes (token hashes only; the
                rules live in app/auth/refresh_tokens.py).

See app/shared/db.py for the connection pool and the transaction contract
every function here follows (it receives an open `conn` and never commits or
rolls back itself). Every public function is re-exported below, explicitly
(not `import *`), so callers write `repository.list_roles(conn)`.
"""

from __future__ import annotations

from app.auth.repository.refresh_tokens import (
    RefreshTokenRecord,
    get_refresh_token_for_update,
    insert_refresh_token,
    revoke_refresh_token,
    revoke_refresh_token_family,
    revoke_user_refresh_tokens,
)
from app.auth.repository.roles import (
    PermissionRecord,
    RoleRecord,
    create_role,
    delete_role,
    get_role,
    get_role_permissions,
    list_permissions,
    list_roles,
    update_role,
)
from app.auth.repository.users import (
    UserRecord,
    assign_role,
    count_active_users_with_role,
    create_user,
    get_user_by_id,
    get_user_by_username,
    get_user_permissions,
    list_users,
    list_users_with_role,
    set_user_password,
    set_user_roles,
    unassign_role,
    update_user,
)

__all__ = [
    "PermissionRecord",
    "RefreshTokenRecord",
    "RoleRecord",
    "UserRecord",
    "assign_role",
    "count_active_users_with_role",
    "create_role",
    "create_user",
    "delete_role",
    "get_refresh_token_for_update",
    "get_role",
    "get_role_permissions",
    "get_user_by_id",
    "get_user_by_username",
    "get_user_permissions",
    "insert_refresh_token",
    "list_permissions",
    "list_roles",
    "list_users",
    "list_users_with_role",
    "revoke_refresh_token",
    "revoke_refresh_token_family",
    "revoke_user_refresh_tokens",
    "set_user_password",
    "set_user_roles",
    "unassign_role",
    "update_role",
    "update_user",
]
