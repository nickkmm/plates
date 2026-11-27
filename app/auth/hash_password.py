"""Generate a werkzeug password hash for the AUTH_USERS env var.

Usage:
    python -m app.web.hash_password 'plaintext-password'
    python -m app.web.hash_password           # prompts (input hidden)

Prints a pbkdf2 hash. Combine it with a username for AUTH_USERS, e.g.:
    AUTH_USERS=alice:<printed-hash>
Multiple users are comma-separated:
    AUTH_USERS=alice:<hash-a>,bob:<hash-b>
"""

from __future__ import annotations

import getpass
import sys

from werkzeug.security import generate_password_hash


def main() -> None:
    password = sys.argv[1] if len(sys.argv) > 1 else getpass.getpass("Password: ")
    if not password:
        raise SystemExit("error: empty password")
    print(generate_password_hash(password))


if __name__ == "__main__":
    main()
