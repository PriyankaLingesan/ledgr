"""Mint an API key.

    python -m app.scripts.create_api_key <organization name> <label> [ROLE]

The key belongs to the named organization (ROLE: ADMIN, FINANCE, OPERATIONS or
AUDITOR; default ADMIN). Keys can also be managed in the console under
Administration > API keys.

Prints the plaintext key exactly once. Only its SHA-256 hash is ever
persisted (see `app.services.auth`), so if it's lost, minting a new one is
the only recovery - there is no "forgot my key" flow, deliberately: a
server that could hand back a lost key would have to store it recoverably.
"""

import sys

from sqlalchemy import select

from app.db.session import session_scope
from app.models.enums import UserRole
from app.models.user import Organization
from app.services import auth as auth_service


def main() -> int:
    if len(sys.argv) not in (3, 4):
        print(
            "usage: python -m app.scripts.create_api_key <organization name> <label> "
            "[ADMIN|FINANCE|OPERATIONS|AUDITOR]",
            file=sys.stderr,
        )
        return 1

    org_name, label = sys.argv[1], sys.argv[2]
    try:
        role = UserRole(sys.argv[3].upper()) if len(sys.argv) == 4 else UserRole.ADMIN
    except ValueError:
        print(f"unknown role {sys.argv[3]!r}", file=sys.stderr)
        return 1
    with session_scope(unscoped=True) as db:
        org = (
            db.execute(select(Organization).where(Organization.name == org_name)).scalars().first()
        )
        if org is None:
            print(f"no organization named {org_name!r}", file=sys.stderr)
            return 1
        _, raw_key = auth_service.create_api_key(
            db, label=label, role=role, organization_id=org.id, created_by="cli"
        )

    print(f"Created {role.value} API key '{label}' for organization '{org_name}':")
    print(raw_key)
    print("\nThis is shown once. Store it now - it cannot be recovered later.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
