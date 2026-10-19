"""Explicit ORM -> API serializers.

Written by hand rather than via `from_attributes` because every monetary field
has to be rendered against its own currency exponent, and because the API shape
should be free to diverge from the storage shape (`extra` -> `metadata`).
"""

import hashlib
from datetime import UTC, datetime

from app.models.account import Account
from app.models.audit import AuditEvent
from app.models.enums import EntryDirection, NormalBalance
from app.models.investigation import Investigation
from app.models.ledger_entry import LedgerEntry
from app.models.reconciliation import ExternalTransaction, ReconciliationResult, ReconciliationRun
from app.models.transaction import Transaction
from app.schemas.account import (
    AccountBalanceOut,
    AccountOut,
    AccountSummary,
    BalanceCacheOut,
)
from app.schemas.common import Money
from app.schemas.investigation import (
    AccountBalanceAtOut,
    BalanceChangeOut,
    ControlExceptionOut,
    CorrectionSimulationOut,
    DuplicateEntryOut,
    DuplicateGroupOut,
    IntegrityCheckOut,
    InvestigationEventOut,
    SimulatedAccountImpactOut,
    TransactionLineageOut,
    TransactionNodeOut,
)
from app.schemas.investigation_case import InvestigationCaseEvidenceOut, InvestigationCaseOut
from app.schemas.reconciliation import (
    ExternalTransactionOut,
    ReconciliationResultOut,
    ReconciliationRunOut,
    ReconciliationRunSummaryOut,
)
from app.schemas.system import AuditEventOut
from app.schemas.transaction import (
    LedgerEntryOut,
    TransactionOut,
    TransactionSummary,
)
from app.services.investigation import (
    AccountBalanceAt,
    BalanceChange,
    ControlException,
    CorrectionSimulation,
    DuplicateGroup,
    IntegrityCheck,
    LedgerEvent,
    TransactionLineage,
)
from app.services.investigation_cases import CaseEvidence


def oriented_balance(normal_balance: NormalBalance, debits: int, credits: int) -> int:
    """Balance in the account's own normal orientation.

    A liability with 500 credited and 100 debited reads as 400, not -400.
    """
    if normal_balance is NormalBalance.DEBIT:
        return debits - credits
    return credits - debits


def account_summary(account: Account) -> AccountSummary:
    return AccountSummary(
        id=account.id,
        code=account.code,
        name=account.name,
        type=account.type,
        normal_balance=account.normal_balance,
        currency=account.currency,
        status=account.status,
    )


def account_out(account: Account) -> AccountOut:
    cache = account.balance_cache
    debits = cache.debits_minor if cache else 0
    credits = cache.credits_minor if cache else 0
    balance = oriented_balance(account.normal_balance, debits, credits)
    return AccountOut(
        id=account.id,
        code=account.code,
        name=account.name,
        type=account.type,
        normal_balance=account.normal_balance,
        currency=account.currency,
        status=account.status,
        description=account.description,
        allows_negative_balance=account.allows_negative_balance,
        metadata=account.extra or {},
        created_at=account.created_at,
        updated_at=account.updated_at,
        balance=Money.of(balance, account.currency),
        debits=Money.of(debits, account.currency),
        credits=Money.of(credits, account.currency),
        entry_count=cache.entry_count if cache else 0,
    )


def account_balance_out(
    account: Account,
    *,
    derived_debits: int,
    derived_credits: int,
    derived_entry_count: int,
    derived_last_seq: int | None,
) -> AccountBalanceOut:
    cache = account.balance_cache
    cached_debits = cache.debits_minor if cache else 0
    cached_credits = cache.credits_minor if cache else 0
    currency = account.currency
    balance = oriented_balance(account.normal_balance, derived_debits, derived_credits)

    return AccountBalanceOut(
        account_id=account.id,
        account_code=account.code,
        currency=currency,
        normal_balance=account.normal_balance,
        balance=Money.of(balance, currency),
        signed_balance=Money.of(derived_debits - derived_credits, currency),
        debits=Money.of(derived_debits, currency),
        credits=Money.of(derived_credits, currency),
        entry_count=derived_entry_count,
        last_entry_seq=derived_last_seq,
        derived_at=datetime.now(UTC),
        cache=BalanceCacheOut(
            balance=Money.of(
                oriented_balance(account.normal_balance, cached_debits, cached_credits), currency
            ),
            debits=Money.of(cached_debits, currency),
            credits=Money.of(cached_credits, currency),
            entry_count=cache.entry_count if cache else 0,
            last_entry_seq=cache.last_entry_seq if cache else None,
            updated_at=cache.updated_at if cache else None,
        ),
        cache_consistent=(cached_debits == derived_debits and cached_credits == derived_credits),
    )


def transaction_summary(transaction: Transaction) -> TransactionSummary:
    return TransactionSummary(
        id=transaction.id,
        reference=transaction.reference,
        description=transaction.description,
        status=transaction.status,
        kind=transaction.kind,
        currency=transaction.currency,
        posted_at=transaction.posted_at,
        effective_at=transaction.effective_at,
    )


def ledger_entry_out(
    entry: LedgerEntry,
    *,
    with_account: bool = True,
    with_transaction: bool = False,
) -> LedgerEntryOut:
    return LedgerEntryOut(
        id=entry.id,
        seq=entry.seq,
        transaction_id=entry.transaction_id,
        account_id=entry.account_id,
        direction=entry.direction,
        amount=Money.of(entry.amount_minor, entry.currency),
        entry_index=entry.entry_index,
        memo=entry.memo,
        created_at=entry.created_at,
        account=account_summary(entry.account) if with_account else None,
        transaction=transaction_summary(entry.transaction) if with_transaction else None,
    )


def transaction_out(transaction: Transaction, *, with_entries: bool = True) -> TransactionOut:
    entries = list(transaction.entries) if with_entries else []

    # Trust nothing: recompute the balance assertion from the stored entries so
    # the detail view proves the invariant instead of echoing a cached total.
    debits = sum(e.amount_minor for e in entries if e.direction is EntryDirection.DEBIT)
    credits = sum(e.amount_minor for e in entries if e.direction is EntryDirection.CREDIT)
    balanced = (
        debits == credits == transaction.total_debits_minor
        if with_entries
        else transaction.total_debits_minor == transaction.total_credits_minor
    )

    reversed_by = getattr(transaction, "reversed_by", None)

    return TransactionOut(
        id=transaction.id,
        seq=transaction.seq,
        reference=transaction.reference,
        description=transaction.description,
        currency=transaction.currency,
        status=transaction.status,
        kind=transaction.kind,
        total_debits=Money.of(transaction.total_debits_minor, transaction.currency),
        total_credits=Money.of(transaction.total_credits_minor, transaction.currency),
        balanced=balanced,
        entry_count=transaction.entry_count,
        effective_at=transaction.effective_at,
        posted_at=transaction.posted_at,
        reversed_at=transaction.reversed_at,
        reverses_transaction_id=transaction.reverses_transaction_id,
        reversed_by_transaction_id=reversed_by.id if reversed_by is not None else None,
        actor=transaction.actor,
        request_id=transaction.request_id,
        idempotency_key=transaction.idempotency_key,
        external_reference=transaction.external_reference,
        metadata=transaction.extra or {},
        entries=[
            ledger_entry_out(entry, with_account=True, with_transaction=False) for entry in entries
        ],
    )


# --------------------------------------------------------------------------
# investigation
# --------------------------------------------------------------------------


def account_balance_at_out(result: AccountBalanceAt) -> AccountBalanceAtOut:
    account = result.account
    balance = oriented_balance(
        account.normal_balance, result.derived.debits_minor, result.derived.credits_minor
    )
    return AccountBalanceAtOut(
        account_id=account.id,
        account_code=account.code,
        account_name=account.name,
        currency=account.currency,
        normal_balance=account.normal_balance,
        as_of=result.as_of,
        balance=Money.of(balance, account.currency),
        debits=Money.of(result.derived.debits_minor, account.currency),
        credits=Money.of(result.derived.credits_minor, account.currency),
        entry_count=result.derived.entry_count,
    )


def investigation_event_out(event: LedgerEvent, *, currency: str) -> InvestigationEventOut:
    entry, transaction = event.entry, event.transaction
    return InvestigationEventOut(
        entry_id=entry.id,
        seq=entry.seq,
        transaction_id=transaction.id,
        reference=transaction.reference,
        description=transaction.description,
        direction=entry.direction,
        amount=Money.of(entry.amount_minor, currency),
        effect=Money.of(event.effect_minor, currency),
        running_balance=Money.of(event.running_balance_minor, currency),
        created_at=entry.created_at,
        transaction_status=transaction.status,
        transaction_kind=transaction.kind,
        reverses_transaction_id=transaction.reverses_transaction_id,
        reversed_by_transaction_id=(
            transaction.reversed_by.id if transaction.reversed_by is not None else None
        ),
    )


def balance_change_out(change: BalanceChange) -> BalanceChangeOut:
    account = change.account
    currency = account.currency
    return BalanceChangeOut(
        account_id=account.id,
        account_code=account.code,
        account_name=account.name,
        currency=currency,
        normal_balance=account.normal_balance,
        from_=change.from_ts,
        to=change.to_ts,
        opening_balance=Money.of(change.opening_balance_minor, currency),
        closing_balance=Money.of(change.closing_balance_minor, currency),
        net_change=Money.of(change.net_change_minor, currency),
        event_count=len(change.events),
        events=[investigation_event_out(e, currency=currency) for e in change.events],
        reconciled=change.reconciled,
    )


def duplicate_group_out(group: DuplicateGroup) -> DuplicateGroupOut:
    return DuplicateGroupOut(
        reasons=group.reasons,
        entries=[
            DuplicateEntryOut(
                transaction_id=entry.transaction_id,
                reference=entry.transaction.reference,
                description=entry.transaction.description,
                account_code=entry.account.code,
                direction=entry.direction,
                amount=Money.of(entry.amount_minor, entry.currency),
                created_at=entry.created_at,
            )
            for entry in group.entries
        ],
    )


def integrity_check_out(check: IntegrityCheck) -> IntegrityCheckOut:
    return IntegrityCheckOut(name=check.name, passed=check.passed, detail=check.detail)


# --------------------------------------------------------------------------
# transaction lineage
# --------------------------------------------------------------------------


def transaction_node_out(transaction: Transaction) -> TransactionNodeOut:
    return TransactionNodeOut(
        id=transaction.id,
        reference=transaction.reference,
        description=transaction.description,
        status=transaction.status,
        kind=transaction.kind,
        currency=transaction.currency,
        total_debits=Money.of(transaction.total_debits_minor, transaction.currency),
        total_credits=Money.of(transaction.total_credits_minor, transaction.currency),
        posted_at=transaction.posted_at,
    )


def transaction_lineage_out(lineage: TransactionLineage) -> TransactionLineageOut:
    return TransactionLineageOut(
        root=transaction_node_out(lineage.root),
        accounts=[account_summary(a) for a in lineage.accounts],
        reverses=transaction_node_out(lineage.reverses) if lineage.reverses else None,
        reversed_by=transaction_node_out(lineage.reversed_by) if lineage.reversed_by else None,
        corrects=transaction_node_out(lineage.corrects) if lineage.corrects else None,
        corrected_by=transaction_node_out(lineage.corrected_by) if lineage.corrected_by else None,
    )


# --------------------------------------------------------------------------
# what-if correction simulator
# --------------------------------------------------------------------------


def correction_simulation_out(simulation: CorrectionSimulation) -> CorrectionSimulationOut:
    return CorrectionSimulationOut(
        original_id=simulation.original.id,
        original_reference=simulation.original.reference,
        impacts=[
            SimulatedAccountImpactOut(
                account_id=impact.account.id,
                account_code=impact.account.code,
                account_name=impact.account.name,
                currency=impact.account.currency,
                current_balance=Money.of(impact.current_balance_minor, impact.account.currency),
                simulated_balance=Money.of(impact.simulated_balance_minor, impact.account.currency),
                delta=Money.of(impact.delta_minor, impact.account.currency),
            )
            for impact in simulation.impacts
        ],
    )


# --------------------------------------------------------------------------
# control exceptions
# --------------------------------------------------------------------------


def control_exception_out(exc: ControlException) -> ControlExceptionOut:
    fingerprint_src = f"{exc.kind}|{exc.title}|{sorted(str(t) for t in exc.transaction_ids)}"
    exc_id = hashlib.sha256(fingerprint_src.encode("utf-8")).hexdigest()[:16]
    return ControlExceptionOut(
        id=exc_id,
        kind=exc.kind,
        title=exc.title,
        detail=exc.detail,
        account_id=exc.account_id,
        transaction_ids=exc.transaction_ids,
        detected_at=exc.detected_at,
    )


# --------------------------------------------------------------------------
# investigation cases
# --------------------------------------------------------------------------


def investigation_case_out(case: Investigation) -> InvestigationCaseOut:
    return InvestigationCaseOut(
        id=case.id,
        case_number=case.case_number,
        title=case.title,
        issue_summary=case.issue_summary,
        status=case.status,
        source=case.source,
        source_reference=case.source_reference,
        priority=case.priority,
        reconciliation_result_id=case.reconciliation_result_id,
        account=account_summary(case.account) if case.account is not None else None,
        window_from=case.window_from,
        window_to=case.window_to,
        resolved_at=case.resolved_at,
        resolution_note=case.resolution_note,
        correction_transaction_id=case.correction_transaction_id,
        created_by=case.created_by,
        created_at=case.created_at,
        updated_at=case.updated_at,
    )


def investigation_case_evidence_out(evidence: CaseEvidence) -> InvestigationCaseEvidenceOut:
    return InvestigationCaseEvidenceOut(
        case=investigation_case_out(evidence.case),
        balance_change=(
            balance_change_out(evidence.balance_change) if evidence.balance_change else None
        ),
        duplicate_candidates=[duplicate_group_out(g) for g in evidence.duplicate_candidates],
        integrity=[integrity_check_out(c) for c in evidence.integrity],
        lineage=transaction_lineage_out(evidence.lineage) if evidence.lineage else None,
        reconciliation=(
            reconciliation_result_out(evidence.reconciliation_result)
            if evidence.reconciliation_result
            else None
        ),
        audit_events=[audit_event_out(e) for e in evidence.audit_events],
    )


# --------------------------------------------------------------------------
# external reconciliation
# --------------------------------------------------------------------------


def external_transaction_out(external: ExternalTransaction) -> ExternalTransactionOut:
    return ExternalTransactionOut(
        id=external.id,
        source_system=external.source_system,
        external_id=external.external_id,
        account_reference=external.account_reference,
        amount=Money.of(external.amount_minor, external.currency),
        direction=external.direction,
        status=external.status,
        transaction_timestamp=external.transaction_timestamp,
        description=external.description,
    )


def reconciliation_result_out(result: ReconciliationResult) -> ReconciliationResultOut:
    external = result.external_transaction
    internal = result.internal_transaction
    currency = external.currency if external else (internal.currency if internal else None)
    return ReconciliationResultOut(
        id=result.id,
        status=result.status,
        explanation=result.explanation,
        difference=(
            Money.of(result.difference_minor, currency)
            if result.difference_minor is not None and currency
            else None
        ),
        external_transaction=external_transaction_out(external) if external else None,
        internal_transaction_id=internal.id if internal else None,
        internal_reference=internal.reference if internal else None,
        internal_amount=(
            Money.of(internal.total_debits_minor, internal.currency) if internal else None
        ),
    )


def reconciliation_run_summary_out(run: ReconciliationRun) -> ReconciliationRunSummaryOut:
    return ReconciliationRunSummaryOut(
        id=run.id,
        run_number=run.seq,
        created_at=run.created_at,
        external_count=run.external_count,
        matched_count=run.matched_count,
        mismatch_count=run.mismatch_count,
        missing_internal_count=run.missing_internal_count,
        missing_external_count=run.missing_external_count,
        ambiguous_count=run.ambiguous_count,
    )


def reconciliation_run_out(run: ReconciliationRun) -> ReconciliationRunOut:
    return ReconciliationRunOut(
        **reconciliation_run_summary_out(run).model_dump(),
        results=[reconciliation_result_out(r) for r in run.results],
    )


def audit_event_out(e: AuditEvent) -> AuditEventOut:
    return AuditEventOut(
        id=e.id,
        seq=e.seq,
        event_type=e.event_type,
        resource_type=e.resource_type,
        resource_id=e.resource_id,
        actor=e.actor,
        request_id=e.request_id,
        payload=e.payload,
        created_at=e.created_at,
    )
