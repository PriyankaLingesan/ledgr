"""The Demo Learning Guide: exercises whose completion is checked against real state.

The teaching text lives in the console. What lives here is the part a browser must
not be trusted with: the expected answers and the checks. Two kinds of exercise:

* **state** exercises ask you to *do* something (import a statement, record a bank
  fee, resolve a case, complete an approval). They pass only when the database shows
  the result - opening a page counts for nothing.
* **answer** exercises ask you to *find* something (a reference, a balance, a count).
  The expected answer is computed from the live data when you submit, never stored in
  the page, so it stays right if you have changed the books since.

A passed exercise is recorded as an audit event (`demo.exercise_completed`), which is
how progress persists - and it is visible in the audit explorer like everything else.

Only organizations marked as the Meridian demonstration have a guide; everyone else
gets a plain 404, exactly as for an unknown record.
"""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, time, timedelta
from functools import lru_cache
from typing import Any

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.core.errors import NotFound, ValidationFailed
from app.demo import meridian_scenario as scn
from app.models.account import Account
from app.models.audit import AuditEvent
from app.models.casework import ReconciliationDecision
from app.models.draft import TransactionDraft
from app.models.enums import (
    DraftStatus,
    EntryDirection,
    InvestigationStatus,
    MatchDecision,
    TransactionKind,
    TransactionStatus,
)
from app.models.investigation import Investigation
from app.models.ledger_entry import LedgerEntry
from app.models.reconciliation import ExternalTransaction, ReconciliationResult
from app.models.transaction import Transaction
from app.models.user import Organization, User
from app.services import investigation
from app.services.ai import provider as ai_provider
from app.services.auth import Principal

EXERCISE_IDS = [f"ex{n}" for n in range(1, 13)]
KIND = {
    "ex1": "answer",
    "ex2": "answer",
    "ex3": "answer",
    "ex4": "state",
    "ex5": "state",
    "ex6": "state",
    "ex7": "state",
    "ex8": "state",
    "ex9": "state",
    "ex10": "answer",
    "ex11": "answer",
    "ex12": "state",
}
COMPLETED_EVENT = "demo.exercise_completed"


@dataclass(frozen=True)
class Verdict:
    passed: bool
    message: str


# --------------------------------------------------------------------------
# context
# --------------------------------------------------------------------------


def require_demo_organization(db: Session, organization_id: uuid.UUID) -> Organization:
    org = db.get(Organization, organization_id)
    if org is None or org.demo_scenario != scn.SCENARIO_KEY:
        raise NotFound("this organization has no demonstration guide")
    return org


def _as_of(db: Session):
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
        raise NotFound("the demonstration has not been seeded")
    from datetime import date

    return date.fromisoformat(event.payload["as_of"])


@lru_cache(maxsize=4)
def _scenario(as_of_iso: str) -> scn.Scenario:
    from datetime import date

    return scn.build_scenario(date.fromisoformat(as_of_iso))


def scenario_for(db: Session) -> scn.Scenario:
    return _scenario(_as_of(db).isoformat())


# --------------------------------------------------------------------------
# the guide
# --------------------------------------------------------------------------


def _completed_by(db: Session, actor: str) -> set[str]:
    rows = db.execute(
        select(AuditEvent.resource_id).where(
            AuditEvent.event_type == COMPLETED_EVENT, AuditEvent.actor == actor
        )
    ).scalars()
    return {r for r in rows if r}


def facts(db: Session, sc: scn.Scenario) -> dict[str, dict[str, Any]]:
    f = sc.facts
    window_to = sc.as_of
    window_from = sc.as_of - timedelta(days=60)
    return {
        "ex1": {"opening_reference": sc.opening.ref, "account_code": "INV.STOCK"},
        "ex2": {
            "customer": "Sundaram Hardware & Tools Pvt Ltd",
            "amount_minor": scn.inr(295_000),
        },
        "ex3": {
            "term": "SwiftHaul",
            "from": window_from.isoformat(),
            "to": window_to.isoformat(),
        },
        "ex4": {"accounts": ["CASH.PETTY", "BANK.DNB.OPS"]},
        "ex5": {"record_count": len(sc.s2()), "source_system": scn.STATEMENT_SOURCE},
        "ex6": {"fee_external_id": f["fee_s2_ext"], "amount_minor": scn.inr(590)},
        "ex7": {
            "external_id": f["ambig_ext"],
            "candidate_references": [f["ambig_original"], f["ambig_duplicate"]],
        },
        "ex8": {"external_id": f["discount_ext"], "ledger_reference": f["discount_payment"]},
        "ex9": {
            "threshold_minor": scn.APPROVAL_THRESHOLD_MINOR,
            "pending_reference": next(
                (p.ref for p in sc.postings if getattr(p, "mode", "") == "pending"), None
            ),
        },
        "ex10": {"reversal_reference": f["reversal_demo_ref"]},
        "ex11": {},
        "ex12": {"transaction_reference": f["ambig_duplicate"]},
    }


def guide(db: Session, principal: Principal) -> dict[str, Any]:
    org = require_demo_organization(db, principal.organization_id)
    sc = scenario_for(db)
    done = _completed_by(db, principal.label)
    people = {
        u.email.lower(): u
        for u in db.execute(select(User).where(User.organization_id == org.id)).scalars()
    }
    all_facts = facts(db, sc)
    return {
        "scenario": scn.SCENARIO_KEY,
        "organization_name": org.name,
        "as_of": sc.as_of,
        "approval_threshold_minor": scn.APPROVAL_THRESHOLD_MINOR,
        "statement": {
            "source_system": scn.STATEMENT_SOURCE,
            "record_count": len(sc.s2()),
            "filename": "meridian_bank_statement_2.csv",
        },
        "ai_available": ai_provider.get_provider() is not None,
        "personas": [
            {
                "key": p.key,
                "email": p.email,
                "full_name": p.full_name,
                "role": p.role,
                "title": p.title,
                "responsibilities": p.responsibilities,
                "active": bool(people.get(p.email.lower()) and people[p.email.lower()].is_active),
            }
            for p in scn.PERSONAS.values()
        ],
        "exercises": [
            {
                "id": ex,
                "kind": KIND[ex],
                "completed": ex in done,
                "facts": all_facts[ex],
            }
            for ex in EXERCISE_IDS
        ],
        "completed_count": len([e for e in EXERCISE_IDS if e in done]),
        "total_count": len(EXERCISE_IDS),
    }


def sample_statement_csv(db: Session, principal: Principal) -> str:
    require_demo_organization(db, principal.organization_id)
    return scn.statement_csv(scenario_for(db).s2())


# --------------------------------------------------------------------------
# verification
# --------------------------------------------------------------------------


def _number(text: str) -> float | None:
    cleaned = re.sub(r"[^0-9.\-]", "", text or "")
    if cleaned in ("", "-", ".", "-."):
        return None
    try:
        return float(cleaned)
    except ValueError:
        return None


def _tx_by_story(db: Session, story: str) -> Transaction | None:
    return (
        db.execute(select(Transaction).where(Transaction.extra["story"].as_string() == story))
        .scalars()
        .first()
    )


def _txn_by_ref(db: Session, ref: str) -> Transaction | None:
    return db.execute(select(Transaction).where(Transaction.reference == ref)).scalar_one_or_none()


def _check_ex1(db, sc, answer) -> Verdict:
    expected = db.execute(
        select(LedgerEntry.amount_minor)
        .join(Account, Account.id == LedgerEntry.account_id)
        .where(Account.code == "INV.STOCK", LedgerEntry.direction == EntryDirection.DEBIT)
        .order_by(LedgerEntry.seq)
        .limit(1)
    ).scalar_one_or_none()
    value = _number(answer or "")
    if value is None:
        return Verdict(False, "Enter the amount in rupees, for example 1234567.")
    if expected is not None and round(value * 100) == expected:
        return Verdict(True, "Correct: that is the Inventory debit on the opening transaction.")
    return Verdict(
        False,
        "Not quite. Open the opening-balances transaction and read the debit on "
        "'Inventory - Trading Stock' (not the total, and not the current balance).",
    )


def _check_ex2(db, sc, answer) -> Verdict:
    txn = _tx_by_story(db, "receipt_sundaram")
    if txn is None:
        return Verdict(False, "The receipt this exercise refers to is missing from the ledger.")
    if (answer or "").strip().upper() == txn.reference.upper():
        return Verdict(True, "Correct. It debits the bank and credits Accounts Receivable.")
    return Verdict(
        False,
        "That is not the reference of Sundaram's Rs 2,95,000 receipt. Search transactions for "
        "'Sundaram' and look for a customer receipt for exactly that amount.",
    )


def _check_ex3(db, sc, answer) -> Verdict:
    start = datetime.combine(sc.as_of - timedelta(days=60), time.min, tzinfo=UTC)
    end = datetime.combine(sc.as_of, time.max, tzinfo=UTC)
    expected = db.execute(
        select(func.count(Transaction.id)).where(
            Transaction.description.ilike("%SwiftHaul%"),
            Transaction.status == TransactionStatus.POSTED,
            Transaction.posted_at >= start,
            Transaction.posted_at <= end,
        )
    ).scalar_one()
    value = _number(answer or "")
    if value is not None and int(value) == expected and float(value).is_integer():
        return Verdict(
            True, f"Correct: {expected} posted transactions mention SwiftHaul in that window."
        )
    return Verdict(
        False,
        "Not the number the ledger shows. Use the search box for 'SwiftHaul', set the two dates, "
        "and read the count under the title (only POSTED transactions).",
    )


def _check_ex4(db, sc, principal, answer) -> Verdict:
    found = db.execute(
        select(AuditEvent.id).where(
            AuditEvent.event_type == "idempotency.replay", AuditEvent.actor == principal.label
        )
    ).first()
    if found:
        return Verdict(
            True, "The audit log shows you retried a request and the ledger replayed the original."
        )
    return Verdict(
        False,
        "No retried request from you was found. Use the Idempotency lab below to send the same "
        "posting twice with the same key.",
    )


def _check_ex5(db, sc, answer) -> Verdict:
    ids = [r.external_id for r in sc.s2()]
    have = db.execute(
        select(func.count(ExternalTransaction.id)).where(
            ExternalTransaction.source_system == scn.STATEMENT_SOURCE,
            ExternalTransaction.external_id.in_(ids),
        )
    ).scalar_one()
    if have == len(ids):
        return Verdict(True, f"All {have} statement-2 records are imported.")
    return Verdict(
        False,
        f"{have} of {len(ids)} statement-2 records are imported. Import the whole file "
        f"(source system {scn.STATEMENT_SOURCE}).",
    )


def _check_ex6(db, sc, answer) -> Verdict:
    ext = sc.facts["fee_s2_ext"]
    txns = (
        db.execute(
            select(Transaction).where(
                Transaction.external_reference == ext,
                Transaction.status == TransactionStatus.POSTED,
                Transaction.kind == TransactionKind.STANDARD,
            )
        )
        .scalars()
        .all()
    )
    mine = [t for t in txns if not t.extra.get("simulated")]
    if not mine:
        return Verdict(
            False,
            f"No ledger transaction carries the bank reference {ext} yet. Record the fee "
            "(Dr Bank Charges, Cr the bank account) and put the bank's reference in External reference.",
        )
    t = mine[0]
    entries = db.execute(
        select(LedgerEntry.direction, Account.code)
        .join(Account, Account.id == LedgerEntry.account_id)
        .where(LedgerEntry.transaction_id == t.id)
    ).all()
    debits = {code for d, code in entries if d is EntryDirection.DEBIT}
    credits = {code for d, code in entries if d is EntryDirection.CREDIT}
    if (
        t.total_debits_minor != scn.inr(590)
        or debits != {"EXP.BANKFEES"}
        or credits != {"BANK.DNB.OPS"}
    ):
        return Verdict(
            False,
            "A transaction carries the reference, but it is not the fee: expected Rs 590 debited to "
            "Bank Charges and credited to the operating bank account.",
        )
    return Verdict(True, "Correct. Run reconciliation again and this record will show as matched.")


def _check_ex7(db, sc, answer) -> Verdict:
    ext = sc.facts["ambig_ext"]
    original = _txn_by_ref(db, sc.facts["ambig_original"])
    rows = (
        db.execute(
            select(ReconciliationDecision)
            .join(ReconciliationResult, ReconciliationResult.id == ReconciliationDecision.result_id)
            .join(
                ExternalTransaction,
                ExternalTransaction.id == ReconciliationResult.external_transaction_id,
            )
            .where(
                ExternalTransaction.external_id == ext,
                ReconciliationDecision.decision == MatchDecision.CONFIRMED_MATCH,
            )
        )
        .scalars()
        .all()
    )
    if not rows:
        return Verdict(
            False,
            "No decision has been recorded on the ambiguous result yet. Open it in the reconciliation "
            "workbench, review both candidates, and confirm one with a reason.",
        )
    if original is not None and any(r.internal_transaction_id == original.id for r in rows):
        return Verdict(
            True,
            "Correct: the original receipt is the real one. (Next: the duplicate should be reversed - see exercise 10.)",
        )
    return Verdict(
        False,
        "You confirmed a match, but to the entry described as re-keyed/duplicate. Weigh the evidence: read both descriptions "
        "and dates again: the engine's two candidates are not equally likely.",
    )


def _check_ex8(db, sc, answer) -> Verdict:
    ext = sc.facts["discount_ext"]
    cases = (
        db.execute(
            select(Investigation)
            .join(
                ReconciliationResult,
                ReconciliationResult.id == Investigation.reconciliation_result_id,
            )
            .join(
                ExternalTransaction,
                ExternalTransaction.id == ReconciliationResult.external_transaction_id,
            )
            .where(
                ExternalTransaction.external_id == ext,
                # a case the learner opened, not one of the seeded MCP-CASE-nn cases
                or_(
                    Investigation.source_reference.is_(None),
                    Investigation.source_reference.not_like("MCP-CASE-%"),
                ),
            )
        )
        .scalars()
        .all()
    )
    if not cases:
        return Verdict(
            False,
            "No case of yours is linked to that amount mismatch. Open the exception in the reconciliation "
            "workbench and use 'Open investigation'.",
        )
    resolved = [c for c in cases if c.status is InvestigationStatus.RESOLVED]
    if not resolved:
        return Verdict(
            False,
            "The case exists but is not resolved yet. Add notes, then resolve it with a reasoned note.",
        )
    if not any(len((c.resolution_note or "").strip()) >= 20 for c in resolved):
        return Verdict(
            False,
            "Resolved - but the resolution note is too short to be a reasoned explanation (20+ characters).",
        )
    return Verdict(
        True,
        "Correct: a case you opened is resolved with a written reason, on the right exception.",
    )


def _check_ex9(db, sc, answer) -> Verdict:
    rows = (
        db.execute(select(TransactionDraft).where(TransactionDraft.status == DraftStatus.POSTED))
        .scalars()
        .all()
    )
    ok = [
        d
        for d in rows
        if not (d.extra or {}).get("simulated") and d.decided_by and d.decided_by != d.created_by
    ]
    if ok:
        return Verdict(
            True,
            "Correct: someone proposed a transaction and a different person approved it into the ledger.",
        )
    pending = [
        d
        for d in db.execute(
            select(TransactionDraft).where(TransactionDraft.status == DraftStatus.PENDING_APPROVAL)
        ).scalars()
        if not (d.extra or {}).get("simulated")
    ]
    if pending:
        return Verdict(
            False,
            "Your draft is waiting. Sign in as a different finance person (not its creator) and approve it.",
        )
    return Verdict(
        False,
        "No draft of yours has been approved yet. Create a draft (as Kavya, or as the owner), submit it, "
        "then approve it as a different person.",
    )


def _check_ex10(db, sc, answer) -> Verdict:
    rv = _txn_by_ref(db, sc.facts["reversal_demo_ref"])
    original = None
    if rv is not None and rv.reverses_transaction_id is not None:
        original = db.get(Transaction, rv.reverses_transaction_id)
    if original is None:
        return Verdict(False, "The reversal this exercise refers to is missing.")
    if (answer or "").strip().upper() == original.reference.upper():
        return Verdict(
            True,
            "Correct. The original is preserved (status REVERSED) and the reversal mirrors every entry.",
        )
    return Verdict(
        False,
        "Not the original. Open the reversal's detail page and follow 'reverses' / the lineage back to its source.",
    )


def _check_ex11(db, sc, answer) -> Verdict:
    checks = investigation.integrity_checklist(db)
    passing = sum(1 for c in checks if c.passed)
    value = _number(answer or "")
    if value is not None and float(value).is_integer() and int(value) == passing:
        note = (
            ""
            if passing == len(checks)
            else f" ({len(checks) - passing} check(s) are not passing - investigate them)."
        )
        return Verdict(
            True, f"Correct: {passing} of {len(checks)} integrity checks pass{note or '.'}"
        )
    return Verdict(
        False, "Run the integrity checklist (System health) and count the checks marked as passing."
    )


def _check_ex12(db, sc, principal, answer) -> Verdict:
    if ai_provider.get_provider() is None:
        return Verdict(
            False,
            "Live AI is not configured on this installation, so this exercise cannot be completed. "
            "Everything else in the guide works without it - see the setup steps below.",
        )
    found = db.execute(
        select(AuditEvent.id).where(
            AuditEvent.event_type == "ai.copilot_used", AuditEvent.actor == principal.label
        )
    ).first()
    if found:
        return Verdict(
            True, "The audit log shows you used the AI copilot on a record of this organization."
        )
    return Verdict(
        False,
        "No copilot request from you was found. Open a transaction and press Explain with AI.",
    )


def verify(db: Session, principal: Principal, exercise_id: str, answer: str | None) -> Verdict:
    require_demo_organization(db, principal.organization_id)
    if exercise_id not in EXERCISE_IDS:
        raise NotFound(f"unknown exercise {exercise_id!r}")
    if answer is not None and len(answer) > 200:
        raise ValidationFailed("the answer is too long", details={"field": "answer"})
    sc = scenario_for(db)
    checks = {
        "ex1": lambda: _check_ex1(db, sc, answer),
        "ex2": lambda: _check_ex2(db, sc, answer),
        "ex3": lambda: _check_ex3(db, sc, answer),
        "ex4": lambda: _check_ex4(db, sc, principal, answer),
        "ex5": lambda: _check_ex5(db, sc, answer),
        "ex6": lambda: _check_ex6(db, sc, answer),
        "ex7": lambda: _check_ex7(db, sc, answer),
        "ex8": lambda: _check_ex8(db, sc, answer),
        "ex9": lambda: _check_ex9(db, sc, answer),
        "ex10": lambda: _check_ex10(db, sc, answer),
        "ex11": lambda: _check_ex11(db, sc, answer),
        "ex12": lambda: _check_ex12(db, sc, principal, answer),
    }
    verdict = checks[exercise_id]()
    if verdict.passed and exercise_id not in _completed_by(db, principal.label):
        from app.services import audit

        audit.record(
            db,
            event_type=COMPLETED_EVENT,
            resource_type="demo_exercise",
            resource_id=exercise_id,
            actor=principal.label,
            payload={"exercise": exercise_id},
        )
        db.commit()
    return verdict
