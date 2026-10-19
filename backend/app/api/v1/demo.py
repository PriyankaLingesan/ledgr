"""Demo Learning Guide endpoints (only for a demonstration organization)."""

from fastapi import APIRouter, Depends
from fastapi.responses import Response

from app.api.deps import CurrentPrincipal, DbSession, require
from app.core.permissions import Permission
from app.demo import guide as guide_service
from app.schemas.demo import GuideOut, VerifyOut, VerifyRequest

router = APIRouter(prefix="/demo", tags=["demo"])


@router.get(
    "/guide", response_model=GuideOut, dependencies=[Depends(require(Permission.DASHBOARD_READ))]
)
def get_guide(db: DbSession, principal: CurrentPrincipal) -> dict:
    """Progress and per-exercise facts. 404 unless this organization is the Meridian demo."""
    return guide_service.guide(db, principal)


@router.get(
    "/guide/sample-statement.csv",
    dependencies=[Depends(require(Permission.DASHBOARD_READ))],
)
def sample_statement(db: DbSession, principal: CurrentPrincipal) -> Response:
    """The synthetic statement-2 bank file, in the layout the import wizard maps."""
    csv = guide_service.sample_statement_csv(db, principal)
    return Response(
        content=csv,
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": 'attachment; filename="meridian_bank_statement_2.csv"'},
    )


@router.post(
    "/guide/{exercise_id}/verify",
    response_model=VerifyOut,
    dependencies=[Depends(require(Permission.INVESTIGATIONS_WRITE))],
)
def verify_exercise(
    exercise_id: str, payload: VerifyRequest, db: DbSession, principal: CurrentPrincipal
) -> VerifyOut:
    """Check the exercise against the real state of the books. Records completion only on a pass."""
    verdict = guide_service.verify(db, principal, exercise_id, payload.answer)
    return VerifyOut(exercise_id=exercise_id, passed=verdict.passed, message=verdict.message)
