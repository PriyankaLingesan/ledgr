"""Investigation notes, reconciliation decisions, match suggestions and case reports.

Nothing here posts, reverses or edits a financial record. Notes and decisions
are append-only annotations (who concluded what, when, and why); suggestions are
deterministic candidate lists a person must review; the case report is a
read-only assembly of real data. Every query is tenant-scoped by the session.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core import money
from app.core.errors import NotFound, ValidationFailed
from app.models.audit import AuditEvent
from app.models.casework import InvestigationNote, ReconciliationDecision
from app.models.enums import (
    MatchDecision,
    ReconciliationStatus,
    TransactionKind,
    TransactionStatus,
)
from app.models.transaction import Transaction
from app.services import audit, investigation_cases, reconciliation
from app.services.auth import Principal

# --------------------------------------------------------------------------
# investigation notes
# --------------------------------------------------------------------------


def add_note(
    db: Session,
    case_id: uuid.UUID,
    *,
    principal: Principal,
    body: str,
    request_id: str | None,
) -> InvestigationNote:
    body = body.strip()
    if not body:
        raise ValidationFailed("a note cannot be empty", details={"field": "body"})
    case = investigation_cases.get_case(db, case_id)  # 404 for another tenant's case
    note = InvestigationNote(
        id=uuid.uuid4(),
        investigation_id=case.id,
        author=principal.label,
        author_ref=principal.ref,
        body=body,
    )
    db.add(note)
    db.flush()
    audit.record(
        db,
        event_type="investigation.note_added",
        resource_type="investigation",
        resource_id=str(case.id),
        actor=principal.label,
        request_id=request_id,
        payload={"case_number": case.case_number, "note_id": str(note.id)},
    )
    db.commit()
    return note


def list_notes(db: Session, case_id: uuid.UUID) -> list[InvestigationNote]:
    investigation_cases.get_case(db, case_id)
    return list(
        db.execute(
            select(InvestigationNote)
            .where(InvestigationNote.investigation_id == case_id)
            .order_by(InvestigationNote.created_at)
        )
        .scalars()
        .all()
    )


# --------------------------------------------------------------------------
# reconciliation: suggestions and decisions
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class MatchSuggestion:
    transaction: Transaction
    score: int
    reasons: list[str] = field(default_factory=list)


def suggest_matches(db: Session, result_id: uuid.UUID, *, limit: int = 5) -> list[MatchSuggestion]:
    """Candidate ledger transactions for a result, ranked by transparent rules.

    These are *suggestions for a person to review*: the deterministic engine's
    verdict is never changed by them, and nothing is posted. The score is a
    sum of explicit, listed reasons - there is no model and no fuzzy matching.
    """
    result = reconciliation.get_result(db, result_id)
    external = result.external_transaction
    if external is None:
        return []

    candidates = (
        db.execute(
            select(Transaction).where(
                Transaction.status == TransactionStatus.POSTED,
                Transaction.kind == TransactionKind.STANDARD,
                Transaction.currency == external.currency,
            )
        )
        .scalars()
        .all()
    )

    ext_id = external.external_id.lower()
    window = timedelta(days=3)
    scored: list[MatchSuggestion] = []
    for txn in candidates:
        reasons: list[str] = []
        score = 0
        if txn.total_debits_minor == external.amount_minor:
            score += 3
            reasons.append(
                f"amount equals {money.format_amount(external.amount_minor, external.currency)} "
                f"{external.currency}"
            )
        haystacks = [txn.reference, txn.description, txn.external_reference or ""]
        if ext_id and any(ext_id in h.lower() for h in haystacks if h):
            score += 3
            reasons.append(f"mentions the external id {external.external_id}")
        if abs(txn.effective_at - external.transaction_timestamp) <= window:
            score += 1
            reasons.append("effective within 3 days of the external timestamp")
        if score >= 3:  # an amount or an identifier match, not mere proximity
            scored.append(MatchSuggestion(txn, score, reasons))

    scored.sort(key=lambda s: (-s.score, s.transaction.posted_at))
    return scored[:limit]


def record_decision(
    db: Session,
    result_id: uuid.UUID,
    *,
    principal: Principal,
    decision: MatchDecision,
    internal_transaction_id: uuid.UUID | None,
    reason: str,
    request_id: str | None,
) -> ReconciliationDecision:
    """Record a person's conclusion about a result. Never alters the result,
    the ledger or any balance - it is an annotation with an author and a reason."""
    reason = reason.strip()
    if not reason:
        raise ValidationFailed("a reason is required", details={"field": "reason"})
    result = reconciliation.get_result(db, result_id)
    if result.status is ReconciliationStatus.MATCHED:
        raise ValidationFailed("this record already matched; there is nothing to decide")

    if decision is MatchDecision.CONFIRMED_MATCH:
        if internal_transaction_id is None:
            raise ValidationFailed(
                "choose the ledger transaction you are confirming",
                details={"field": "internal_transaction_id"},
            )
        txn = db.get(Transaction, internal_transaction_id)
        if txn is None:
            raise NotFound(f"transaction {internal_transaction_id} not found")
        external = result.external_transaction
        if external is not None and txn.currency != external.currency:
            raise ValidationFailed(
                "currency of the chosen transaction differs from the external record",
                details={"expected": external.currency, "actual": txn.currency},
            )
        if txn.status is not TransactionStatus.POSTED:
            raise ValidationFailed("only a live (non-reversed) transaction can be matched")
    else:
        internal_transaction_id = None

    row = ReconciliationDecision(
        id=uuid.uuid4(),
        result_id=result.id,
        internal_transaction_id=internal_transaction_id,
        decision=decision,
        reason=reason,
        decided_by=principal.label,
        decided_by_ref=principal.ref,
    )
    db.add(row)
    db.flush()
    audit.record(
        db,
        event_type="reconciliation.decision_recorded",
        resource_type="reconciliation_result",
        resource_id=str(result.id),
        actor=principal.label,
        request_id=request_id,
        payload={
            "decision": decision.value,
            "internal_transaction_id": (
                str(internal_transaction_id) if internal_transaction_id else None
            ),
            "reason": reason,
            "result_status": result.status.value,
        },
    )
    db.commit()
    return row


def list_decisions(db: Session, result_id: uuid.UUID) -> list[ReconciliationDecision]:
    reconciliation.get_result(db, result_id)
    return list(
        db.execute(
            select(ReconciliationDecision)
            .where(ReconciliationDecision.result_id == result_id)
            .order_by(ReconciliationDecision.created_at)
        )
        .scalars()
        .all()
    )


def latest_decisions(
    db: Session, result_ids: list[uuid.UUID]
) -> dict[uuid.UUID, ReconciliationDecision]:
    if not result_ids:
        return {}
    rows = (
        db.execute(
            select(ReconciliationDecision)
            .where(ReconciliationDecision.result_id.in_(result_ids))
            .order_by(ReconciliationDecision.created_at)
        )
        .scalars()
        .all()
    )
    return {r.result_id: r for r in rows}  # later rows overwrite earlier ones


# --------------------------------------------------------------------------
# case report (deterministic; no AI)
# --------------------------------------------------------------------------


def audit_events_for(db: Session, resource_ids: list[str]) -> list[AuditEvent]:
    return list(
        db.execute(
            select(AuditEvent)
            .where(AuditEvent.resource_id.in_(resource_ids))
            .order_by(AuditEvent.seq.asc())
            .limit(200)
        )
        .scalars()
        .all()
    )


def render_case_report_markdown(db: Session, case_id: uuid.UUID) -> str:
    """A downloadable evidence report assembled only from real records."""
    evidence = investigation_cases.case_evidence(db, case_id)
    case = evidence.case
    notes = list_notes(db, case_id)

    lines = [
        f"# {case.case_number}: {case.title}",
        "",
        f"- Status: {case.status.value}",
        f"- Priority: {case.priority.value}",
        f"- Source: {case.source.value}",
        f"- Opened by {case.created_by} at {case.created_at.isoformat()}",
    ]
    if case.account is not None:
        lines.append(f"- Account: {case.account.code} ({case.account.name})")
    if case.resolved_at:
        lines.append(f"- Resolved at {case.resolved_at.isoformat()}")
    lines += ["", "## Issue", "", case.issue_summary, ""]

    rr = evidence.reconciliation_result
    if rr is not None:
        lines += ["## Reconciliation evidence", ""]
        lines.append(f"- Result: {rr.status.value}")
        lines.append(f"- Explanation: {rr.explanation}")
        if rr.external_transaction is not None:
            ext = rr.external_transaction
            lines.append(
                f"- External record: {ext.source_system} / {ext.external_id}, "
                f"{money.format_amount(ext.amount_minor, ext.currency)} {ext.currency}"
            )
        if rr.internal_transaction is not None:
            it = rr.internal_transaction
            lines.append(
                f"- Internal transaction: {it.reference}, "
                f"{money.format_amount(it.total_debits_minor, it.currency)} {it.currency}"
            )
        if rr.difference_minor is not None:
            lines.append(f"- Difference (minor units): {rr.difference_minor}")
        lines.append("")

    lines += ["## Ledger integrity checks (at report time)", ""]
    for check in evidence.integrity:
        lines.append(f"- [{'PASS' if check.passed else 'FAIL'}] {check.name}: {check.detail}")

    if evidence.duplicate_candidates:
        lines += ["", "## Potential duplicates", ""]
        for group in evidence.duplicate_candidates:
            refs = ", ".join(e.transaction.reference for e in group.entries)
            lines.append(f"- {refs} ({'; '.join(group.reasons)})")

    lines += ["", "## Investigator notes", ""]
    if notes:
        for n in notes:
            lines.append(f"- {n.created_at.isoformat()} - {n.author}: {n.body}")
    else:
        lines.append("_No notes recorded._")

    if case.resolution_note:
        lines += ["", "## Resolution", "", case.resolution_note]

    lines += ["", "## Audit trail", ""]
    for event in evidence.audit_events:
        lines.append(f"- {event.created_at.isoformat()} {event.event_type} by {event.actor}")
    lines += [
        "",
        "_Generated from LEDGR records. No figure in this report is estimated or AI-written._",
        "",
    ]
    return "\n".join(lines)


@dataclass(frozen=True)
class RelatedToTransaction:
    results: list
    cases: list
    decisions: list[ReconciliationDecision]


def related_to_transaction(db: Session, transaction_id: uuid.UUID) -> RelatedToTransaction:
    """Every record that points at one transaction: reconciliation results,
    investigation cases and reviewer decisions. 404 for another tenant's id."""
    from sqlalchemy import or_

    from app.models.investigation import Investigation
    from app.models.reconciliation import ReconciliationResult
    from app.services import queries

    queries.get_transaction(db, transaction_id)
    results = (
        db.execute(
            select(ReconciliationResult)
            .where(ReconciliationResult.internal_transaction_id == transaction_id)
            .order_by(ReconciliationResult.created_at.desc())
            .limit(20)
        )
        .scalars()
        .all()
    )
    cases = (
        db.execute(
            select(Investigation)
            .where(
                or_(
                    Investigation.source_reference == str(transaction_id),
                    Investigation.correction_transaction_id == transaction_id,
                )
            )
            .order_by(Investigation.created_at.desc())
            .limit(20)
        )
        .scalars()
        .all()
    )
    decisions = (
        db.execute(
            select(ReconciliationDecision)
            .where(ReconciliationDecision.internal_transaction_id == transaction_id)
            .order_by(ReconciliationDecision.created_at)
        )
        .scalars()
        .all()
    )
    return RelatedToTransaction(list(results), list(cases), list(decisions))
