"""Mint an API key.

    python -m app.scripts.create_api_key <label>

Prints the plaintext key exactly once. Only its SHA-256 hash is ever
persisted (see `app.services.auth`), so if it's lost, minting a new one is
the only recovery - there is no "forgot my key" flow, deliberately: a
server that could hand back a lost key would have to store it recoverably.
"""

import sys

from app.db.session import session_scope
from app.services import auth as auth_service


def main() -> int:
    if len(sys.argv) != 2:
        print("usage: python -m app.scripts.create_api_key <label>", file=sys.stderr)
        return 1

    label = sys.argv[1]
    with session_scope() as db:
        _, raw_key = auth_service.create_api_key(db, label=label)

    print(f"Created API key for '{label}':")
    print(raw_key)
    print("\nThis is shown once. Store it now - it cannot be recovered later.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
