"""Role -> permission matrix.

The single place that says what each role may do. Every protected endpoint
declares the permission it needs (`app.api.deps.require`) and the server
checks it on every request; the frontend hides controls using the same
matrix (served from `/auth/me`) purely as a convenience - hiding is never the
enforcement.

AUDITOR is strictly read-only: it holds no permission that creates, approves,
posts or changes anything.
"""

from enum import StrEnum

from app.models.enums import UserRole


class Permission(StrEnum):
    # ledger & accounts
    ACCOUNTS_READ = "accounts:read"
    ACCOUNTS_WRITE = "accounts:write"
    TRANSACTIONS_READ = "transactions:read"
    TRANSACTIONS_POST = "transactions:post"  # direct posting / reversal / correction
    # maker-checker
    DRAFTS_READ = "drafts:read"
    DRAFTS_CREATE = "drafts:create"
    DRAFTS_APPROVE = "drafts:approve"
    # investigations
    INVESTIGATIONS_READ = "investigations:read"
    INVESTIGATIONS_WRITE = "investigations:write"
    # reconciliation
    RECONCILIATION_READ = "reconciliation:read"
    RECONCILIATION_RUN = "reconciliation:run"
    # oversight
    AUDIT_READ = "audit:read"
    ALERTS_READ = "alerts:read"
    SYSTEM_READ = "system:read"
    DASHBOARD_READ = "dashboard:read"
    # administration
    USERS_MANAGE = "users:manage"
    API_KEYS_MANAGE = "api_keys:manage"
    ORG_MANAGE = "org:manage"
    # read-only AI helpers (never financial decisions)
    AI_USE = "ai:use"


P = Permission

_READ_ALL = {
    P.ACCOUNTS_READ,
    P.TRANSACTIONS_READ,
    P.DRAFTS_READ,
    P.INVESTIGATIONS_READ,
    P.RECONCILIATION_READ,
    P.AUDIT_READ,
    P.ALERTS_READ,
    P.SYSTEM_READ,
    P.DASHBOARD_READ,
}

ROLE_PERMISSIONS: dict[UserRole, frozenset[Permission]] = {
    # Everything. The organization's creator; can grant any role, including OWNER.
    UserRole.OWNER: frozenset(Permission),
    # Everything operationally, including user/key/org administration - but see
    # `can_manage_role`: an ADMIN can only grant or change roles *below* ADMIN,
    # so there is no lateral or upward privilege escalation.
    UserRole.ADMIN: frozenset(Permission),
    # Moves money: creates/submits drafts, posts (subject to the approval
    # threshold), approves others' drafts, reconciles.
    UserRole.FINANCE: frozenset(
        _READ_ALL
        | {
            P.ACCOUNTS_WRITE,
            P.TRANSACTIONS_POST,
            P.DRAFTS_CREATE,
            P.DRAFTS_APPROVE,
            P.INVESTIGATIONS_WRITE,
            P.RECONCILIATION_RUN,
            P.AI_USE,
        }
    ),
    # Investigates and reconciles; never posts or approves money movement.
    UserRole.OPERATIONS: frozenset(
        _READ_ALL
        | {
            P.INVESTIGATIONS_WRITE,
            P.RECONCILIATION_RUN,
            P.AI_USE,
        }
    ),
    # Read-only across ledger, investigations, reconciliation and audit logs.
    UserRole.AUDITOR: frozenset(_READ_ALL),
}


def permissions_for(role: UserRole) -> frozenset[Permission]:
    return ROLE_PERMISSIONS[role]


def has_permission(role: UserRole, permission: Permission) -> bool:
    return permission in ROLE_PERMISSIONS[role]


# Roles an ADMIN may grant, change or deactivate. OWNER may manage every role.
_ADMIN_MANAGEABLE = frozenset({UserRole.FINANCE, UserRole.OPERATIONS, UserRole.AUDITOR})


def can_manage_role(actor: UserRole, target: UserRole) -> bool:
    """May `actor` grant `target` to someone, or change/deactivate a holder of it?"""
    if actor is UserRole.OWNER:
        return True
    if actor is UserRole.ADMIN:
        return target in _ADMIN_MANAGEABLE
    return False


# API keys never carry the OWNER role (ownership belongs to people).
ASSIGNABLE_KEY_ROLES = frozenset(
    {UserRole.ADMIN, UserRole.FINANCE, UserRole.OPERATIONS, UserRole.AUDITOR}
)
