"""Apply the Meridian scenario to the database - through LEDGR's own services.

Nothing here writes a ledger row directly. Postings go through `posting`, large
ones through the maker-checker `drafts` service with two different people,
reversals through `reverse_transaction`, bank records through
`ingest_external_transactions`, matching through `run_reconciliation`, decisions
through `casework.record_decision`, and cases through `investigation_cases`. So
every ledger invariant, the approval rules and the audit trail apply exactly as
they do for a real organization.

Safety:
* Only an organization carrying `demo_scenario == 'meridian-v1'` *and* the Meridian
  name is ever written to. An unmarked organization that happens to share the name
  stops the seeder; it is never adopted.
* Nothing is deleted, reset or overwritten. Every step looks for what it would create
  (by transaction reference, external id, account code, case source reference) and
  skips it if present, so running it again changes nothing.
* The persona users are created *inactive with an unusable password*; `configure_users`
  is the only way to give them a password, and it refuses to run in production.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import UTC, date, datetime

from sqlalchemy import func, select

from app.core.config import settings
from app.core.errors import LedgrError
from app.db.session import session_scope
from app.demo import meridian_scenario as scn
from app.models.account import Account
from app.models.audit import AuditEvent
from app.models.draft import TransactionDraft
from app.models.enums import (
    DraftStatus,
    EntryDirection,
    InvestigationPriority,
    InvestigationSource,
    InvestigationStatus,
    MatchDecision,
    UserRole,
)
from app.models.investigation import Investigation
from app.models.reconciliation import ExternalTransaction, ReconciliationResult, ReconciliationRun
from app.models.transaction import Transaction
from app.models.user import Organization, User
from app.schemas.transaction import EntryInput, TransactionCreate
from app.services import accounts as account_service
from app.services import audit, casework, drafts, investigation_cases, posting, reconciliation
from app.services import auth as auth_service
from app.services.auth import Principal

UNUSABLE_HASH = "!demo-persona-no-password-set"


class SeedError(RuntimeError):
    """The seeder refused to proceed (and changed nothing it should not have)."""


@dataclass
class SeedReport:
    organization_id: uuid.UUID
    as_of: date
    created: dict[str, int] = field(default_factory=dict)
    skipped: dict[str, int] = field(default_factory=dict)

    def add(self, what: str, created: bool) -> None:
        bucket = self.created if created else self.skipped
        bucket[what] = bucket.get(what, 0) + 1


# --------------------------------------------------------------------------
# organization and people
# --------------------------------------------------------------------------


def find_organization() -> Organization | None:
    """The Meridian demo organization, proven by marker and name; None if absent."""
    with session_scope(unscoped=True) as db:
        marked = list(
            db.execute(
                select(Organization).where(
                    Organization.demo_scenario == scn.SCENARIO_KEY,
                    Organization.name == scn.ORG_NAME,
                )
            )
            .scalars()
            .all()
        )
        if len(marked) > 1:
            raise SeedError("more than one Meridian demo organization exists; refusing to choose")
        if marked:
            db.expunge(marked[0])
            return marked[0]
        clash = db.execute(select(Organization.id).where(Organization.name == scn.ORG_NAME)).first()
        if clash is not None:
            raise SeedError(
                f"an organization named {scn.ORG_NAME!r} exists but is not marked as the Meridian "
                "demo, so it is not this script's to modify; nothing was changed"
            )
    return None


def _recorded_as_of(org_id: uuid.UUID) -> date | None:
    with session_scope(org_id) as db:
        event = (
            db.execute(
                select(AuditEvent)
                .where(
                    AuditEvent.event_type == "demo.stage_completed",
                    AuditEvent.resource_id == "organization",
                )
                .order_by(AuditEvent.seq)
            )
            .scalars()
            .first()
        )
        if event is None:
            return None
        return date.fromisoformat(event.payload["as_of"])


def recorded_as_of(org_id: uuid.UUID) -> date | None:
    return _recorded_as_of(org_id)


def ensure_organization(as_of: date, report: SeedReport | None = None) -> tuple[uuid.UUID, date]:
    existing = find_organization()
    if existing is not None:
        anchored = _recorded_as_of(existing.id)
        if anchored is not None and as_of != anchored:
            raise SeedError(
                f"this organization was seeded as of {anchored}; history is append-only, so the "
                f"timeline cannot be moved to {as_of}"
            )
        _ensure_personas(existing.id)
        return existing.id, anchored or as_of

    with session_scope(unscoped=True) as db:
        taken = [
            e
            for e in (p.email for p in scn.PERSONAS.values())
            if db.execute(select(User.id).where(func.lower(User.email) == e.lower())).first()
        ]
        if taken:
            raise SeedError(
                f"these demo e-mail addresses already belong to an existing user: {', '.join(taken)}; "
                "nothing was changed"
            )
        org = Organization(
            id=uuid.uuid4(),
            name=scn.ORG_NAME,
            base_currency=scn.CURRENCY,
            approval_threshold_minor=scn.APPROVAL_THRESHOLD_MINOR,
            demo_scenario=scn.SCENARIO_KEY,
        )
        db.add(org)
        db.flush()
        for persona in scn.PERSONAS.values():
            db.add(
                User(
                    id=uuid.uuid4(),
                    organization_id=org.id,
                    email=persona.email,
                    full_name=persona.full_name,
                    password_hash=UNUSABLE_HASH,
                    role=UserRole(persona.role),
                    is_active=False,
                )
            )
        db.commit()
        org_id = org.id
    # The audit event is written from a session scoped to the new organization (an
    # unscoped maintenance session would leave it without a tenant, invisible to the org).
    with session_scope(org_id) as db:
        _mark_stage(
            db,
            "organization",
            {
                "scenario": scn.SCENARIO_KEY,
                "as_of": as_of.isoformat(),
                "note": "FICTIONAL DEMONSTRATION - synthetic data, no real company, bank or customer",
            },
        )
    if report:
        report.add("organization", True)
    return org_id, as_of


def _ensure_personas(org_id: uuid.UUID) -> None:
    """Add any missing persona to the existing organization (never touch an existing user)."""
    with session_scope(unscoped=True) as db:
        have = {
            u.email.lower()
            for u in db.execute(select(User).where(User.organization_id == org_id)).scalars()
        }
        for persona in scn.PERSONAS.values():
            if persona.email.lower() in have:
                continue
            if db.execute(
                select(User.id).where(func.lower(User.email) == persona.email.lower())
            ).first():
                raise SeedError(f"{persona.email} belongs to a user in another organization")
            db.add(
                User(
                    id=uuid.uuid4(),
                    organization_id=org_id,
                    email=persona.email,
                    full_name=persona.full_name,
                    password_hash=UNUSABLE_HASH,
                    role=UserRole(persona.role),
                    is_active=False,
                )
            )
        db.commit()


def _principals(org_id: uuid.UUID) -> dict[str, Principal]:
    with session_scope(unscoped=True) as db:
        users = {
            u.email.lower(): u
            for u in db.execute(select(User).where(User.organization_id == org_id)).scalars()
        }
    out: dict[str, Principal] = {}
    for key, persona in scn.PERSONAS.items():
        u = users[persona.email.lower()]
        out[key] = Principal(
            kind="user", id=u.id, label=u.email, role=u.role, organization_id=org_id
        )
    return out


def configure_users(password: str, only: list[str] | None = None) -> dict[str, str]:
    """Give the demo personas a password and activate them. LOCAL DEMO ONLY.

    * Refuses in a production environment.
    * Never overwrites a real password: a persona that already has one is left alone.
    * The password is never logged, printed or stored in the clear.
    """
    if settings.environment.lower() == "production":
        raise SeedError("refusing to create demo logins when ENVIRONMENT=production")
    org = find_organization()
    if org is None:
        raise SeedError("the Meridian organization does not exist yet; run the seed first")
    auth_service.validate_password_strength(password)
    wanted = set(only) if only else set(scn.PERSONAS)
    unknown = wanted - set(scn.PERSONAS)
    if unknown:
        raise SeedError(f"unknown persona(s): {', '.join(sorted(unknown))}")
    result: dict[str, str] = {}
    with session_scope(unscoped=True) as db:
        users = {
            u.email.lower(): u
            for u in db.execute(select(User).where(User.organization_id == org.id)).scalars()
        }
        activated: list[tuple[uuid.UUID, str, str]] = []
        for key in scn.PERSONAS:
            if key not in wanted:
                continue
            u = users[scn.PERSONAS[key].email.lower()]
            if u.password_hash != UNUSABLE_HASH:
                result[key] = "already configured (left unchanged)"
                continue
            u.password_hash = auth_service.hash_password(password)
            u.is_active = True
            activated.append((u.id, u.email, u.role.value))
            result[key] = "activated"
        db.commit()
    with session_scope(org.id) as db:
        for user_id, email, role in activated:
            audit.record(
                db,
                event_type="demo.persona_activated",
                resource_type="user",
                resource_id=str(user_id),
                actor="demo-seed",
                payload={"email": email, "role": role},
            )
        db.commit()
    return result


# --------------------------------------------------------------------------
# stages
# --------------------------------------------------------------------------


def _stage_done(db, name: str) -> bool:
    return (
        db.execute(
            select(AuditEvent.id).where(
                AuditEvent.event_type == "demo.stage_completed", AuditEvent.resource_id == name
            )
        ).first()
        is not None
    )


def _mark_stage(db, name: str, payload: dict | None = None) -> None:
    audit.record(
        db,
        event_type="demo.stage_completed",
        resource_type="demo_scenario",
        resource_id=name,
        actor="demo-seed",
        payload=payload or {},
    )
    db.commit()


def _ensure_accounts(org_id: uuid.UUID, report: SeedReport) -> dict[str, Account]:
    out: dict[str, Account] = {}
    with session_scope(org_id) as db:
        for code, name, kind, allow_negative, description in scn.CHART_OF_ACCOUNTS:
            try:
                out[code] = account_service.get_account_by_code(db, code)
                report.add("accounts", False)
            except LedgrError:
                out[code] = account_service.create_account(
                    db,
                    code=code,
                    name=name,
                    account_type=_account_type(kind),
                    currency=scn.CURRENCY,
                    description=description,
                    allows_negative_balance=allow_negative,
                    metadata={"simulated": True, "scenario": scn.SCENARIO_KEY},
                    actor=scn.PERSONAS["owner"].email,
                )
                report.add("accounts", True)
        db.expunge_all()
    return out


def _account_type(kind: str):
    from app.models.enums import AccountType

    return AccountType(kind)


def _txn_exists(db, reference: str) -> Transaction | None:
    return db.execute(
        select(Transaction).where(Transaction.reference == reference)
    ).scalar_one_or_none()


def _entries(item: scn.Posting, ids: dict[str, uuid.UUID]) -> list[posting.EntryCommand]:
    return [
        posting.EntryCommand(
            account_id=ids[leg.account],
            direction=EntryDirection.DEBIT if leg.side == "D" else EntryDirection.CREDIT,
            amount_minor=leg.amount,
            memo=leg.memo,
        )
        for leg in item.legs
    ]


def _apply_event(
    db,
    item: scn.Posting | scn.Reversal,
    ids: dict[str, uuid.UUID],
    people: dict[str, Principal],
    report: SeedReport,
) -> None:
    if isinstance(item, scn.Reversal):
        if _txn_exists(db, item.ref) is not None:
            report.add("reversals", False)
            return
        original = _txn_exists(db, item.original_ref)
        if original is None:
            raise SeedError(f"cannot reverse {item.original_ref}: it has not been posted")
        posting.reverse_transaction(
            db,
            original.id,
            reason=item.reason,
            reference=item.ref,
            effective_at=item.at,
            actor=people[item.actor].label,
            posted_at=item.at,
        )
        report.add("reversals", True)
        return

    if item.mode in ("direct", "draft"):
        if _txn_exists(db, item.ref) is not None:
            report.add("transactions", False)
            return
        if item.mode == "direct":
            posting.post_transaction(
                db,
                posting.PostCommand(
                    description=item.description,
                    currency=scn.CURRENCY,
                    entries=_entries(item, ids),
                    reference=item.ref,
                    effective_at=item.at,
                    external_reference=item.external_reference,
                    metadata=item.metadata,
                    actor=people[item.actor].label,
                    posted_at=item.at,
                ),
            )
        else:
            draft = _create_draft(db, item, ids, people, submit=True)
            drafts.approve_draft(
                db,
                draft.id,
                principal=people["checker"],
                note="Approved: checked against the supplier/customer document and the bank advice.",
                request_id=None,
                posted_at=item.at,
            )
        report.add("transactions", True)
        return

    # pending / rejected: only a draft exists, never a ledger row
    existing = db.execute(
        select(TransactionDraft.id).where(TransactionDraft.reference == item.ref)
    ).first()
    if existing is not None:
        report.add("drafts", False)
        return
    draft = _create_draft(db, item, ids, people, submit=True)
    if item.mode == "rejected":
        drafts.reject_draft(
            db,
            draft.id,
            principal=people["checker"],
            note=item.note or "Rejected.",
            request_id=None,
        )
    report.add("drafts", True)


def _create_draft(db, item: scn.Posting, ids, people, *, submit: bool) -> TransactionDraft:
    payload = TransactionCreate(
        description=item.description,
        currency=scn.CURRENCY,
        entries=[
            EntryInput(
                account_id=ids[leg.account],
                direction=EntryDirection.DEBIT if leg.side == "D" else EntryDirection.CREDIT,
                amount_minor=leg.amount,
                memo=leg.memo,
            )
            for leg in item.legs
        ],
        reference=item.ref,
        effective_at=item.at,
        external_reference=item.external_reference,
        metadata=item.metadata,
    )
    # The maker is never the checker: a posting attributed to the checker persona was, in the
    # story, prepared by the maker and released by the checker.
    maker = "maker" if item.actor == "checker" else item.actor
    return drafts.create_draft(db, payload, principal=people[maker], submit=submit, request_id=None)


def _ingest(db, records: list[scn.BankRecord]) -> reconciliation.IngestResult:
    return reconciliation.ingest_external_transactions(
        db,
        source_system=scn.STATEMENT_SOURCE,
        records=[
            reconciliation.ExternalRecord(
                external_id=r.external_id,
                amount_minor=r.amount,
                currency=scn.CURRENCY,
                direction=EntryDirection.CREDIT
                if r.direction == "CREDIT"
                else EntryDirection.DEBIT,
                transaction_timestamp=r.at,
                account_reference="DEMO NATIONAL BANK ..0142",
                status="SETTLED",
                description=r.description,
            )
            for r in records
        ],
    )


def _result_id(db, run_id: uuid.UUID, ref: tuple[str, str]):
    kind, value = ref
    if kind == "external":
        row = db.execute(
            select(ReconciliationResult.id)
            .join(
                ExternalTransaction,
                ExternalTransaction.id == ReconciliationResult.external_transaction_id,
            )
            .where(ReconciliationResult.run_id == run_id, ExternalTransaction.external_id == value)
        ).first()
    else:
        txn = _txn_exists(db, value)
        row = (
            db.execute(
                select(ReconciliationResult.id).where(
                    ReconciliationResult.run_id == run_id,
                    ReconciliationResult.internal_transaction_id == txn.id,
                    ReconciliationResult.external_transaction_id.is_(None),
                )
            ).first()
            if txn is not None
            else None
        )
    if row is None:
        raise SeedError(f"reconciliation result for {ref} was not found in the seeded run")
    return row[0]


def _first_run_id(db) -> uuid.UUID:
    row = db.execute(select(ReconciliationRun.id).order_by(ReconciliationRun.seq)).first()
    if row is None:
        raise SeedError("the seeded reconciliation run is missing")
    return row[0]


def _casework(
    org_id: uuid.UUID,
    sc: scn.Scenario,
    ids: dict[str, uuid.UUID],
    people: dict[str, Principal],
    report: SeedReport,
) -> None:
    with session_scope(org_id) as db:
        run_id = _first_run_id(db)
        for spec in sc.decisions:
            result_id = _result_id(db, run_id, spec.result)
            if casework.list_decisions(db, result_id):
                report.add("decisions", False)
                continue
            internal = _txn_exists(db, spec.internal_ref) if spec.internal_ref else None
            casework.record_decision(
                db,
                result_id,
                principal=people[spec.actor],
                decision=MatchDecision(spec.decision),
                internal_transaction_id=internal.id if internal else None,
                reason=spec.reason,
                request_id=None,
            )
            report.add("decisions", True)

        for case in sc.cases:
            exists = db.execute(
                select(Investigation.id).where(
                    Investigation.source_reference == case.source_reference
                )
            ).first()
            if exists is not None:
                report.add("cases", False)
                continue
            result_id = _result_id(db, run_id, case.result) if case.result else None
            account = (
                account_service.get_account_by_code(db, case.account) if case.account else None
            )
            created = investigation_cases.create_case(
                db,
                title=case.title,
                issue_summary=case.summary,
                source=InvestigationSource(case.source),
                source_reference=case.source_reference,
                account_id=account.id if account else None,
                priority=InvestigationPriority(case.priority),
                reconciliation_result_id=result_id,
                created_by=people[case.creator].label,
            )
            for persona, text in case.notes:
                casework.add_note(
                    db, created.id, principal=people[persona], body=text, request_id=None
                )
            if case.status == "RESOLVED":
                investigation_cases.update_status(
                    db,
                    created.id,
                    status=InvestigationStatus.INVESTIGATING,
                    actor=people["ops"].label,
                )
                correction = _txn_exists(db, case.correction_ref) if case.correction_ref else None
                investigation_cases.resolve_case(
                    db,
                    created.id,
                    resolution_note=case.resolution_note or "Resolved.",
                    correction_transaction_id=correction.id if correction else None,
                    actor=people["ops"].label,
                )
            elif case.key == "dupext":
                investigation_cases.update_status(
                    db,
                    created.id,
                    status=InvestigationStatus.INVESTIGATING,
                    actor=people["ops"].label,
                )
            report.add("cases", True)


# --------------------------------------------------------------------------
# public entry points
# --------------------------------------------------------------------------


def seed(as_of: date | None = None, *, log=print) -> SeedReport:
    """Create (or complete) the Meridian organization. Safe to run repeatedly."""
    requested = as_of or datetime.now(UTC).date()
    org_id, anchored = ensure_organization(requested)
    report = SeedReport(organization_id=org_id, as_of=anchored)
    sc = scn.build_scenario(anchored)
    people = _principals(org_id)
    ids = {code: acct.id for code, acct in _ensure_accounts(org_id, report).items()}
    log(f"organization {scn.ORG_NAME} ({org_id}) as of {anchored}")

    with session_scope(org_id) as db:
        _apply_event(db, sc.opening, ids, people, report)
        pre_done = _stage_done(db, "ledger_pre")
    if not pre_done:
        with session_scope(org_id) as db:
            for item in sc.pre:
                _apply_event(db, item, ids, people, report)
            _mark_stage(db, "ledger_pre", {"events": len(sc.pre)})
        log(f"posted {len(sc.pre)} events up to the first reconciliation")

    with session_scope(org_id) as db:
        run_done = _stage_done(db, "reconciliation_run_1")
    if not run_done:
        with session_scope(org_id) as db:
            ingested = _ingest(db, sc.s1())
            run = reconciliation.run_reconciliation(db, actor=people["ops"].label)
            _mark_stage(
                db,
                "reconciliation_run_1",
                {"run_number": run.seq, "imported": ingested.imported, "statement": "S1"},
            )
        report.add("reconciliation_runs", True)
        log(f"imported {ingested.imported} bank records of statement 1 and ran reconciliation")

    with session_scope(org_id) as db:
        cw_done = _stage_done(db, "casework")
    if not cw_done:
        with session_scope(org_id) as db:
            for item in sc.casework:
                _apply_event(db, item, ids, people, report)
        _casework(org_id, sc, ids, people, report)
        with session_scope(org_id) as db:
            _mark_stage(db, "casework")
        log("recorded corrections, decisions and investigation cases")

    with session_scope(org_id) as db:
        post_done = _stage_done(db, "ledger_post")
    if not post_done:
        with session_scope(org_id) as db:
            for item in sc.post:
                _apply_event(db, item, ids, people, report)
            _mark_stage(db, "ledger_post", {"events": len(sc.post)})
        log(f"posted {len(sc.post)} events for the most recent weeks")

    with session_scope(org_id) as db:
        if not _stage_done(db, "seed_completed"):
            _mark_stage(
                db,
                "seed_completed",
                {
                    "transactions": db.execute(select(func.count(Transaction.id))).scalar_one(),
                    "as_of": anchored.isoformat(),
                },
            )
    return report


def status() -> dict | None:
    org = find_organization()
    if org is None:
        return None
    with session_scope(org.id) as db:
        pending = db.execute(
            select(func.count(TransactionDraft.id)).where(
                TransactionDraft.status == DraftStatus.PENDING_APPROVAL
            )
        ).scalar_one()
        cases = dict(
            db.execute(
                select(Investigation.status, func.count(Investigation.id)).group_by(
                    Investigation.status
                )
            ).all()
        )
        out = {
            "organization_id": str(org.id),
            "as_of": (recorded_as_of(org.id) or date.min).isoformat(),
            "accounts": db.execute(select(func.count(Account.id))).scalar_one(),
            "transactions": db.execute(select(func.count(Transaction.id))).scalar_one(),
            "external_records": db.execute(select(func.count(ExternalTransaction.id))).scalar_one(),
            "reconciliation_runs": db.execute(
                select(func.count(ReconciliationRun.id))
            ).scalar_one(),
            "cases": {k.value: v for k, v in cases.items()},
            "pending_approvals": pending,
        }
    with session_scope(unscoped=True) as db:
        users = db.execute(select(User).where(User.organization_id == org.id)).scalars().all()
        out["users"] = {u.email: ("active" if u.is_active else "not configured") for u in users}
    return out
