"""AI orchestration endpoints: agents, runs, findings and proposals.

Every run is a stored record. Nothing here places an order, and the response says
so explicitly via `tradeable` and `blocked_reason`: a UI that showed a bearish
proposal without that context would be implying a capability the platform does
not have yet.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, HTTPException, Query, Request, status
from sqlalchemy import func, select
from sqlalchemy.orm import selectinload

from app.api.deps import CurrentUser, DbSession, StaffUser
from app.core.rate_limit import enforce_rate_limit
from app.models.ai import Agent, AgentFinding, AgentRun, RunStatus, Stance
from app.schemas.ai import (
    AgentRead,
    AIOverview,
    FindingRead,
    RunDetail,
    RunRead,
    RunRequest,
)
from app.services.ai import orchestrator

router = APIRouter(prefix="/ai", tags=["ai"])


def _run_detail(run: AgentRun) -> RunDetail:
    detail = RunDetail.model_validate(run)
    detail.findings = [FindingRead.model_validate(finding) for finding in run.findings]
    return detail


@router.get("/agents", response_model=list[AgentRead])
def list_agents(db: DbSession, user: CurrentUser) -> list[AgentRead]:
    """Registered agents, including the ones Phase 5 has not built.

    `implemented: false` is the honest answer for the fundamental, sentiment and
    quantitative specialists. Hiding them would make the platform look complete.
    """
    rows = list(db.scalars(select(Agent).order_by(Agent.role, Agent.code)))
    if not rows:
        orchestrator.seed_agents(db)
        db.commit()
        rows = list(db.scalars(select(Agent).order_by(Agent.role, Agent.code)))
    return [AgentRead.model_validate(row) for row in rows]


@router.post("/agents/seed", response_model=dict)
def seed_agents_endpoint(db: DbSession, user: StaffUser) -> dict:
    result = orchestrator.seed_agents(db)
    db.commit()
    return result


@router.get("/overview", response_model=AIOverview)
def overview(
    db: DbSession, user: CurrentUser, days: int = Query(default=30, ge=1, le=365)
) -> AIOverview:
    since = datetime.now(UTC) - timedelta(days=days)
    total = int(db.scalar(select(func.count(AgentRun.id)).where(AgentRun.created_at >= since)) or 0)
    completed = int(
        db.scalar(
            select(func.count(AgentRun.id)).where(
                AgentRun.created_at >= since, AgentRun.status == RunStatus.COMPLETED
            )
        )
        or 0
    )
    refused = int(
        db.scalar(
            select(func.count(AgentRun.id)).where(
                AgentRun.created_at >= since, AgentRun.status == RunStatus.REFUSED
            )
        )
        or 0
    )
    partial = int(
        db.scalar(
            select(func.count(AgentRun.id)).where(
                AgentRun.created_at >= since, AgentRun.status == RunStatus.PARTIAL
            )
        )
        or 0
    )
    # A tradeable proposal should be impossible in Phase 5. Counting it makes any
    # future regression visible instead of theoretical.
    tradeable = int(
        db.scalar(
            select(func.count(AgentRun.id)).where(
                AgentRun.created_at >= since, AgentRun.tradeable.is_(True)
            )
        )
        or 0
    )
    agents = list(db.scalars(select(Agent)))
    latest = list(db.scalars(select(AgentRun).order_by(AgentRun.created_at.desc()).limit(8)))
    stances: dict[str, int] = {}
    for value in db.scalars(select(AgentRun.proposal_stance).where(AgentRun.created_at >= since)):
        key = value.value if hasattr(value, "value") else str(value)
        stances[key] = stances.get(key, 0) + 1
    return AIOverview(
        runs_total=total,
        runs_completed=completed,
        runs_refused=refused,
        runs_partial=partial,
        agents_implemented=sum(1 for agent in agents if agent.implemented),
        agents_total=len(agents),
        tradeable_proposals=tradeable,
        latest_runs=[RunRead.model_validate(run) for run in latest],
        stance_breakdown=stances,
    )


@router.get("/runs", response_model=list[RunRead])
def list_runs(
    db: DbSession,
    user: CurrentUser,
    symbol: str | None = None,
    limit: int = Query(default=25, ge=1, le=100),
) -> list[RunRead]:
    statement = select(AgentRun).order_by(AgentRun.created_at.desc()).limit(limit)
    if symbol:
        statement = statement.where(AgentRun.instrument_symbol == symbol)
    return [RunRead.model_validate(run) for run in db.scalars(statement)]


@router.get("/runs/{run_id}", response_model=RunDetail)
def get_run(run_id: str, db: DbSession, user: CurrentUser) -> RunDetail:
    run = db.scalar(
        select(AgentRun)
        .where(AgentRun.id == run_id)
        .options(selectinload(AgentRun.findings).selectinload(AgentFinding.evidence))
    )
    if run is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Run not found")
    return _run_detail(run)


@router.post("/run", response_model=RunDetail, status_code=status.HTTP_201_CREATED)
def create_run(
    payload: RunRequest, request: Request, db: DbSession, user: CurrentUser
) -> RunDetail:
    """Run the specialists over one instrument and store the result."""
    enforce_rate_limit(request, "ai_run", limit=12, window_seconds=60)

    if payload.account_id is not None:
        from app.models.account import TradingAccount

        account = db.get(TradingAccount, payload.account_id)
        if account is None or account.user_id != user.id:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Account not found")

    run = orchestrator.run_agents(
        db,
        symbol=payload.symbol,
        timeframe=payload.timeframe,
        account_id=payload.account_id,
        requested_by=user.id,
        question=payload.question,
    )
    db.commit()
    refreshed = db.scalar(
        select(AgentRun)
        .where(AgentRun.id == run.id)
        .options(selectinload(AgentRun.findings).selectinload(AgentFinding.evidence))
    )
    assert refreshed is not None
    return _run_detail(refreshed)


@router.get("/proposals", response_model=list[RunRead])
def list_proposals(
    db: DbSession,
    user: CurrentUser,
    limit: int = Query(default=20, ge=1, le=100),
) -> list[RunRead]:
    """Runs that reached a directional stance.

    Every one of them carries `tradeable: false`. The endpoint exists so a reader
    can see what the agents would have proposed, and the flag exists so nobody
    mistakes it for an instruction.
    """
    statement = (
        select(AgentRun)
        .where(AgentRun.proposal_stance.in_([Stance.BULLISH, Stance.BEARISH]))
        .order_by(AgentRun.created_at.desc())
        .limit(limit)
    )
    return [RunRead.model_validate(run) for run in db.scalars(statement)]
