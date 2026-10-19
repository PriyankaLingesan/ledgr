"""Transaction endpoints.

Posting and reversal are the only money-moving operations in the system. Both
run through the same idempotency wrapper so a retried write is answered from
the stored response instead of being executed twice.
"""

import csv
import io
import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, Query, Response, status
from fastapi.responses import JSONResponse

from app.api.deps import (
    Actor,
    CurrentPrincipal,
    DbSession,
    IdempotencyKey,
    OrgId,
    PaginationParams,
    RequestId,
    require,
)
from app.core import money
from app.core.permissions import Permission
from app.models.enums import TransactionKind, TransactionStatus
from app.schemas.casework import DecisionOut, RelatedCaseOut, RelatedExternalOut, RelatedOut
from app.schemas.common import Page
from app.schemas.investigation import (
    CorrectionSimulationOut,
    SimulateCorrectionRequest,
    TransactionCorrectionOut,
    TransactionCorrectionRequest,
    TransactionLineageOut,
)
from app.schemas.serialize import (
    correction_simulation_out,
    transaction_lineage_out,
    transaction_out,
)
from app.schemas.transaction import (
    TransactionCreate,
    TransactionOut,
    TransactionReverse,
)
from app.services import (
    audit,
    casework,
    drafts,
    idempotency,
    investigation,
    posting,
    queries,
)

router = APIRouter(prefix="/transactions", tags=["transactions"])


def _replay(
    replay: idempotency.Replay,
    *,
    org_id: uuid.UUID,
    key: str,
    actor: str,
    request_id: str,
    operation: str,
) -> JSONResponse:
    """Answer a retry from the stored response - and remember that it
    happened, so a client stuck in a retry loop shows up in the audit trail
    (and in the alerts built from it). Nothing is re-executed."""
    audit.record_independent(
        organization_id=org_id,
        event_type="idempotency.replay",
        resource_type="idempotency_key",
        resource_id=key[:64],
        actor=actor,
        request_id=request_id,
        payload={"key": key[:120], "operation": operation},
    )
    return JSONResponse(
        content=replay.body,
        status_code=replay.status_code,
        headers={"Idempotent-Replay": "true"},
    )


@router.post(
    "",
    response_model=TransactionOut,
    status_code=status.HTTP_201_CREATED,
    responses={
        409: {"description": "Duplicate reference, or an identical request is still running"},
        422: {"description": "Unbalanced, wrong currency, inactive account or invalid amount"},
    },
    dependencies=[Depends(require(Permission.TRANSACTIONS_POST))],
)
def post_transaction(
    payload: TransactionCreate,
    db: DbSession,
    actor: Actor,
    org_id: OrgId,
    request_id: RequestId,
    key: IdempotencyKey,
    response: Response,
):
    """Post a balanced double-entry transaction.

    Send an `Idempotency-Key` header: a retry after a timeout then returns the
    original result rather than moving money twice.
    """
    body = payload.model_dump(mode="json")

    if key:
        replay = idempotency.claim(
            organization_id=org_id,
            scope=idempotency.SCOPE_POST_TRANSACTION,
            key=key,
            request_hash=idempotency.fingerprint(body),
            request_id=request_id,
        )
        if replay is not None:
            return _replay(
                replay,
                org_id=org_id,
                key=key,
                actor=actor,
                request_id=request_id,
                operation="post_transaction",
            )

    cmd = posting.PostCommand(
        description=payload.description,
        currency=payload.currency,
        entries=[
            posting.EntryCommand(
                account_id=e.account_id,
                direction=e.direction,
                amount_minor=e.amount_minor,
                memo=e.memo,
            )
            for e in payload.entries
        ],
        reference=payload.reference,
        effective_at=payload.effective_at,
        external_reference=payload.external_reference,
        metadata=payload.metadata,
        actor=actor,
        request_id=request_id,
        idempotency_key=key,
    )

    try:
        # Organization policy: large amounts must go through maker-checker.
        drafts.enforce_direct_posting_policy(
            db, total_minor=sum(e.amount_minor for e in cmd.entries if e.direction.value == "DEBIT")
        )
        transaction = posting.post_transaction(db, cmd)
    except Exception:
        if key:
            # The write failed, so the key never produced a financial record:
            # let the caller retry with it.
            idempotency.release(
                organization_id=org_id, scope=idempotency.SCOPE_POST_TRANSACTION, key=key
            )
        raise

    out = transaction_out(transaction)
    if key:
        idempotency.complete(
            organization_id=org_id,
            scope=idempotency.SCOPE_POST_TRANSACTION,
            key=key,
            status_code=status.HTTP_201_CREATED,
            body=out.model_dump(mode="json"),
            transaction_id=transaction.id,
        )
    response.headers["Location"] = f"/api/v1/transactions/{transaction.id}"
    return out


@router.get(
    "",
    response_model=Page[TransactionOut],
    dependencies=[Depends(require(Permission.TRANSACTIONS_READ))],
)
def list_transactions(
    db: DbSession,
    page: PaginationParams,
    q: str | None = Query(default=None, description="Match reference or description"),
    transaction_status: TransactionStatus | None = Query(default=None, alias="status"),
    kind: TransactionKind | None = Query(default=None),
    currency: str | None = Query(default=None, min_length=3, max_length=3),
    account_id: uuid.UUID | None = Query(default=None),
    posted_from: datetime | None = Query(default=None),
    posted_to: datetime | None = Query(default=None),
) -> Page[TransactionOut]:
    items, total = queries.list_transactions(
        db,
        query=q,
        status=transaction_status,
        kind=kind,
        currency=currency,
        account_id=account_id,
        posted_from=posted_from,
        posted_to=posted_to,
        limit=page.limit,
        offset=page.offset,
    )
    return Page[TransactionOut](
        items=[transaction_out(t) for t in items],
        total=total,
        limit=page.limit,
        offset=page.offset,
    )


_EXPORT_LIMIT = 10_000
_CSV_DANGEROUS = ("=", "+", "-", "@", "\t", "\r")


def _csv_cell(value: object) -> str:
    """Neutralise spreadsheet formula injection: a cell that starts like a
    formula is prefixed so Excel/Sheets treat it as text."""
    text = "" if value is None else str(value)
    return "'" + text if text.startswith(_CSV_DANGEROUS) else text


@router.get(
    "/export",
    dependencies=[Depends(require(Permission.TRANSACTIONS_READ))],
    responses={200: {"content": {"text/csv": {}}}},
)
def export_transactions(
    db: DbSession,
    principal: CurrentPrincipal,
    request_id: RequestId,
    q: str | None = Query(default=None),
    transaction_status: TransactionStatus | None = Query(default=None, alias="status"),
    kind: TransactionKind | None = Query(default=None),
    currency: str | None = Query(default=None, min_length=3, max_length=3),
    account_id: uuid.UUID | None = Query(default=None),
    posted_from: datetime | None = Query(default=None),
    posted_to: datetime | None = Query(default=None),
) -> Response:
    """CSV of the caller's own transactions matching the same filters as the
    list endpoint, capped at 10,000 rows (`X-Export-Truncated` says if it was).
    Exports are audited, and cells that look like spreadsheet formulas are
    neutralised."""
    items, total = queries.list_transactions(
        db,
        query=q,
        status=transaction_status,
        kind=kind,
        currency=currency,
        account_id=account_id,
        posted_from=posted_from,
        posted_to=posted_to,
        limit=_EXPORT_LIMIT,
        offset=0,
    )
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(
        [
            "reference",
            "status",
            "kind",
            "currency",
            "amount",
            "amount_minor",
            "posted_at",
            "effective_at",
            "actor",
            "description",
            "external_reference",
            "idempotency_key",
        ]
    )
    for t in items:
        writer.writerow(
            [
                _csv_cell(v)
                for v in (
                    t.reference,
                    t.status.value,
                    t.kind.value,
                    t.currency,
                    money.format_amount(t.total_debits_minor, t.currency),
                    t.total_debits_minor,
                    t.posted_at.isoformat(),
                    t.effective_at.isoformat(),
                    t.actor,
                    t.description,
                    t.external_reference,
                    t.idempotency_key,
                )
            ]
        )
    audit.record(
        db,
        event_type="export.transactions",
        resource_type="transaction",
        actor=principal.label,
        request_id=request_id,
        payload={"rows": len(items), "matching": total, "truncated": total > len(items)},
    )
    db.commit()
    headers = {"Content-Disposition": 'attachment; filename="ledgr-transactions.csv"'}
    if total > len(items):
        headers["X-Export-Truncated"] = "true"
    return Response(
        content=buffer.getvalue(), media_type="text/csv; charset=utf-8", headers=headers
    )


@router.get(
    "/by-reference/{reference}",
    response_model=TransactionOut,
    dependencies=[Depends(require(Permission.TRANSACTIONS_READ))],
)
def get_transaction_by_reference(reference: str, db: DbSession) -> TransactionOut:
    return transaction_out(queries.get_transaction_by_reference(db, reference))


@router.get(
    "/{transaction_id}",
    response_model=TransactionOut,
    dependencies=[Depends(require(Permission.TRANSACTIONS_READ))],
)
def get_transaction(transaction_id: uuid.UUID, db: DbSession) -> TransactionOut:
    return transaction_out(queries.get_transaction(db, transaction_id))


@router.post(
    "/{transaction_id}/reverse",
    response_model=TransactionOut,
    status_code=status.HTTP_201_CREATED,
    responses={
        409: {"description": "Already reversed"},
        422: {"description": "Reversals cannot themselves be reversed"},
    },
    dependencies=[Depends(require(Permission.TRANSACTIONS_POST))],
)
def reverse_transaction(
    transaction_id: uuid.UUID,
    payload: TransactionReverse,
    db: DbSession,
    actor: Actor,
    org_id: OrgId,
    request_id: RequestId,
    key: IdempotencyKey,
):
    """Reverse a posted transaction by posting its mirror image.

    Returns the *reversal* transaction. The original keeps every entry it was
    posted with and is marked REVERSED.
    """
    body = payload.model_dump(mode="json") | {"transaction_id": str(transaction_id)}

    if key:
        replay = idempotency.claim(
            organization_id=org_id,
            scope=idempotency.SCOPE_REVERSE_TRANSACTION,
            key=key,
            request_hash=idempotency.fingerprint(body),
            request_id=request_id,
        )
        if replay is not None:
            return _replay(
                replay,
                org_id=org_id,
                key=key,
                actor=actor,
                request_id=request_id,
                operation="reverse_transaction",
            )

    try:
        reversal = posting.reverse_transaction(
            db,
            transaction_id,
            reason=payload.reason,
            reference=payload.reference,
            effective_at=payload.effective_at,
            actor=actor,
            request_id=request_id,
            idempotency_key=key,
        )
    except Exception:
        if key:
            idempotency.release(
                organization_id=org_id, scope=idempotency.SCOPE_REVERSE_TRANSACTION, key=key
            )
        raise

    out = transaction_out(reversal)
    if key:
        idempotency.complete(
            organization_id=org_id,
            scope=idempotency.SCOPE_REVERSE_TRANSACTION,
            key=key,
            status_code=status.HTTP_201_CREATED,
            body=out.model_dump(mode="json"),
            transaction_id=reversal.id,
        )
    return out


@router.post(
    "/{transaction_id}/correct",
    response_model=TransactionCorrectionOut,
    status_code=status.HTTP_201_CREATED,
    responses={
        404: {"description": "Transaction not found"},
        409: {"description": "Already reversed, or an identical request is still running"},
        422: {"description": "Reversals cannot be corrected, or the new entries are unbalanced"},
    },
    dependencies=[Depends(require(Permission.TRANSACTIONS_POST))],
)
def correct_transaction(
    transaction_id: uuid.UUID,
    payload: TransactionCorrectionRequest,
    db: DbSession,
    actor: Actor,
    org_id: OrgId,
    request_id: RequestId,
    key: IdempotencyKey,
):
    """Guided correction: reverse the original, then post the fixed version.

    Not a new posting mechanism - this is `POST .../reverse` immediately
    followed by `POST /transactions`, both already fully validated, committed
    together as one atomic write (see `investigation.correct_transaction`).
    The original transaction is never edited; it is left exactly as posted,
    marked REVERSED, with its reversal and the new corrected transaction both
    linked back to it via metadata.

    Send an `Idempotency-Key` header, exactly as `POST /transactions` and
    `POST .../reverse` already require: a retried correction request (a
    double-click, a network retry) then replays the original result instead
    of reversing an already-corrected transaction a second time.
    """
    body = payload.model_dump(mode="json") | {"transaction_id": str(transaction_id)}

    if key:
        replay = idempotency.claim(
            organization_id=org_id,
            scope=idempotency.SCOPE_CORRECT_TRANSACTION,
            key=key,
            request_hash=idempotency.fingerprint(body),
            request_id=request_id,
        )
        if replay is not None:
            return _replay(
                replay,
                org_id=org_id,
                key=key,
                actor=actor,
                request_id=request_id,
                operation="correct_transaction",
            )

    try:
        # A correction posts a new transaction, so the same maker-checker
        # policy that gates direct posting applies to the corrected amount.
        drafts.enforce_direct_posting_policy(
            db,
            total_minor=sum(
                e.amount_minor for e in payload.entries if e.direction.value == "DEBIT"
            ),
        )
        correction = investigation.correct_transaction(
            db,
            transaction_id,
            entries=[
                posting.EntryCommand(
                    account_id=e.account_id,
                    direction=e.direction,
                    amount_minor=e.amount_minor,
                    memo=e.memo,
                )
                for e in payload.entries
            ],
            reason=payload.reason,
            description=payload.description,
            reference=payload.reference,
            actor=actor,
            request_id=request_id,
        )
    except Exception:
        if key:
            # The write failed atomically - the original is untouched, still
            # POSTED - so the key never produced a financial record: let the
            # caller retry with it.
            idempotency.release(
                organization_id=org_id, scope=idempotency.SCOPE_CORRECT_TRANSACTION, key=key
            )
        raise

    out = TransactionCorrectionOut(
        original_id=correction.original.id,
        original_reference=correction.original.reference,
        reversal_id=correction.reversal.id,
        reversal_reference=correction.reversal.reference,
        corrected_id=correction.corrected.id,
        corrected_reference=correction.corrected.reference,
    )
    if key:
        idempotency.complete(
            organization_id=org_id,
            scope=idempotency.SCOPE_CORRECT_TRANSACTION,
            key=key,
            status_code=status.HTTP_201_CREATED,
            body=out.model_dump(mode="json"),
            transaction_id=correction.corrected.id,
        )
    return out


@router.get(
    "/{transaction_id}/lineage",
    response_model=TransactionLineageOut,
    dependencies=[Depends(require(Permission.TRANSACTIONS_READ))],
)
def get_transaction_lineage(transaction_id: uuid.UUID, db: DbSession) -> TransactionLineageOut:
    """Where this financial event came from, what it touched, and what
    happened to it afterward - assembled from real foreign-key and metadata
    relationships already on the schema, never inferred.
    """
    return transaction_lineage_out(investigation.transaction_lineage(db, transaction_id))


@router.post(
    "/{transaction_id}/simulate-correction",
    response_model=CorrectionSimulationOut,
    responses={
        404: {"description": "Transaction not found, or a proposed entry names an unknown account"},
        409: {"description": "Already reversed"},
        422: {"description": "Reversals cannot be corrected, or the new entries are unbalanced"},
    },
    dependencies=[Depends(require(Permission.TRANSACTIONS_READ))],
)
def simulate_transaction_correction(
    transaction_id: uuid.UUID,
    payload: SimulateCorrectionRequest,
    db: DbSession,
) -> CorrectionSimulationOut:
    """What CORRECT would produce - current vs. simulated account balances -
    without writing anything. This handler issues no commit, so discarding
    the result leaves the ledger exactly as it was; only `POST .../correct`
    ever changes it.
    """
    simulation = investigation.simulate_correction(
        db,
        transaction_id,
        entries=[
            posting.EntryCommand(
                account_id=e.account_id,
                direction=e.direction,
                amount_minor=e.amount_minor,
                memo=e.memo,
            )
            for e in payload.entries
        ],
    )
    return correction_simulation_out(simulation)


@router.get(
    "/{transaction_id}/related",
    response_model=RelatedOut,
    dependencies=[Depends(require(Permission.TRANSACTIONS_READ))],
)
def get_transaction_related(transaction_id: uuid.UUID, db: DbSession) -> RelatedOut:
    """Reconciliation results, investigation cases and reviewer decisions that
    point at this transaction - the links an investigator follows."""
    related = casework.related_to_transaction(db, transaction_id)
    return RelatedOut(
        reconciliation=[
            RelatedExternalOut(
                result_id=r.id,
                run_number=r.run.seq,
                status=r.status,
                source_system=r.external_transaction.source_system
                if r.external_transaction
                else None,
                external_id=r.external_transaction.external_id if r.external_transaction else None,
                explanation=r.explanation,
            )
            for r in related.results
        ],
        investigations=[
            RelatedCaseOut(
                id=c.id,
                case_number=c.case_number,
                title=c.title,
                status=c.status.value,
                priority=c.priority.value,
            )
            for c in related.cases
        ],
        decisions=[
            DecisionOut(
                id=d.id,
                decision=d.decision,
                internal_transaction_id=d.internal_transaction_id,
                reason=d.reason,
                decided_by=d.decided_by,
                created_at=d.created_at,
            )
            for d in related.decisions
        ],
    )
