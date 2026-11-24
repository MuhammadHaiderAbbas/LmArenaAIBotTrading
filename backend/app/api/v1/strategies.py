"""Strategy catalogue and backtest endpoints.

Backtests are read-and-run only. Nothing here creates an order, and validation
(which is the closest thing to deployment) is staff-only and requires a passing
backtest to exist first.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query, Request, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import CurrentUser, DbSession, StaffUser
from app.core.rate_limit import enforce_rate_limit
from app.models.strategy import (
    Backtest,
    BacktestMetrics,
    BacktestStatus,
    Strategy,
    StrategyStatus,
)
from app.schemas.strategy import (
    BacktestDetail,
    BacktestRead,
    BacktestRequest,
    EquityPointRead,
    MetricsRead,
    StrategyOverview,
    StrategyRead,
    TradeRead,
    ValidationRequest,
)
from app.services.strategies import rules
from app.services.strategies import service as strategy_service

router = APIRouter(prefix="/strategies", tags=["strategies"])


@router.get("", response_model=list[StrategyRead])
def list_strategies(db: DbSession, user: CurrentUser) -> list[StrategyRead]:
    items = list(db.scalars(select(Strategy).order_by(Strategy.code)))
    if not items:
        rules.seed_strategies(db)
        db.commit()
        items = list(db.scalars(select(Strategy).order_by(Strategy.code)))
    return [StrategyRead.model_validate(item) for item in items]


@router.get("/overview", response_model=StrategyOverview)
def overview(db: DbSession, user: CurrentUser) -> StrategyOverview:
    if db.scalar(select(Strategy)) is None:
        rules.seed_strategies(db)
        db.commit()
    strategies = list(db.scalars(select(Strategy)))
    backtests = list(db.scalars(select(Backtest)))
    passing = list(db.scalars(select(BacktestMetrics).where(BacktestMetrics.passes_gate.is_(True))))
    edges = [item.edge_over_baseline for item in passing if item.edge_over_baseline is not None]
    return StrategyOverview(
        strategies_total=len(strategies),
        validated=sum(1 for item in strategies if item.status == StrategyStatus.VALIDATED),
        testing=sum(1 for item in strategies if item.status == StrategyStatus.TESTING),
        drafts=sum(1 for item in strategies if item.status == StrategyStatus.DRAFT),
        backtests_total=len(backtests),
        backtests_passing=len(passing),
        best_edge=max(edges) if edges else None,
        live_eligible=sum(1 for item in strategies if item.live_eligible),
    )


def _detail(db: Session, backtest: Backtest) -> BacktestDetail:
    strategy = db.get(Strategy, backtest.strategy_id)
    metrics = db.scalar(select(BacktestMetrics).where(BacktestMetrics.backtest_id == backtest.id))
    detail = BacktestDetail.model_validate(backtest)
    detail.strategy_code = strategy.code if strategy else ""
    detail.metrics = MetricsRead.model_validate(metrics) if metrics else None
    detail.trades = [TradeRead.model_validate(item) for item in backtest.trades]
    detail.equity = [EquityPointRead.model_validate(item) for item in backtest.equity_points]
    exits: dict[str, int] = {}
    for trade in backtest.trades:
        exits[trade.exit_reason.value] = exits.get(trade.exit_reason.value, 0) + 1
    detail.trade_summary = {
        "long": sum(1 for trade in backtest.trades if trade.direction == "long"),
        "short": sum(1 for trade in backtest.trades if trade.direction == "short"),
        "wins": sum(1 for trade in backtest.trades if trade.net_pnl > 0),
        "losses": sum(1 for trade in backtest.trades if trade.net_pnl <= 0),
        "exit_reasons": exits,
    }
    return detail


@router.get("/backtests", response_model=list[BacktestRead])
def list_backtests(
    db: DbSession,
    user: CurrentUser,
    strategy: str | None = None,
    symbol: str | None = None,
    limit: int = Query(default=25, ge=1, le=100),
) -> list[BacktestRead]:
    statement = select(Backtest).order_by(Backtest.created_at.desc()).limit(limit)
    if strategy:
        parent = db.scalar(select(Strategy).where(Strategy.code == strategy))
        if parent is None:
            raise HTTPException(status_code=404, detail="Strategy not found")
        statement = statement.where(Backtest.strategy_id == parent.id)
    if symbol:
        statement = statement.where(Backtest.instrument_symbol == symbol)
    return [BacktestRead.model_validate(item) for item in db.scalars(statement)]


@router.get("/backtests/{backtest_id}", response_model=BacktestDetail)
def get_backtest(backtest_id: str, db: DbSession, user: CurrentUser) -> BacktestDetail:
    backtest = db.get(Backtest, backtest_id)
    if backtest is None:
        raise HTTPException(status_code=404, detail="Backtest not found")
    return _detail(db, backtest)


@router.post("/backtests", response_model=BacktestDetail, status_code=status.HTTP_201_CREATED)
def create_backtest(
    payload: BacktestRequest, request: Request, db: DbSession, user: CurrentUser
) -> BacktestDetail:
    enforce_rate_limit(request, "strategy_backtest", limit=8, window_seconds=60)
    try:
        backtest = strategy_service.run_backtest(
            db,
            strategy_code=payload.strategy_code,
            symbol=payload.symbol,
            timeframe=payload.timeframe,
            limit=payload.limit,
            starting_equity=payload.starting_equity,
            spread_bps=payload.spread_bps,
            commission_bps=payload.commission_bps,
            slippage_bps=payload.slippage_bps,
            financing_bps_per_day=payload.financing_bps_per_day,
            risk_per_trade=payload.risk_per_trade,
            is_out_of_sample=payload.is_out_of_sample,
            requested_by=user.id,
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
        ) from exc
    db.commit()
    db.refresh(backtest)
    return _detail(db, backtest)


@router.post("/{strategy_id}/validate", response_model=StrategyRead)
def validate(
    strategy_id: str, payload: ValidationRequest, db: DbSession, user: StaffUser
) -> StrategyRead:
    """Staff-only. Refused unless a passing backtest already exists."""
    try:
        strategy = strategy_service.validate_strategy(
            db, strategy_id=strategy_id, actor_id=user.id, note=payload.note
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
        ) from exc
    db.commit()
    return StrategyRead.model_validate(strategy)


@router.post("/{strategy_id}/retire", response_model=StrategyRead)
def retire(strategy_id: str, db: DbSession, user: StaffUser) -> StrategyRead:
    try:
        strategy = strategy_service.retire_strategy(db, strategy_id=strategy_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    db.commit()
    return StrategyRead.model_validate(strategy)


@router.get("/{strategy_id}/latest", response_model=BacktestDetail)
def latest_backtest(strategy_id: str, db: DbSession, user: CurrentUser) -> BacktestDetail:
    backtest = db.scalar(
        select(Backtest)
        .where(Backtest.strategy_id == strategy_id, Backtest.status == BacktestStatus.COMPLETED)
        .order_by(Backtest.created_at.desc())
    )
    if backtest is None:
        raise HTTPException(status_code=404, detail="No completed backtest for this strategy")
    return _detail(db, backtest)
