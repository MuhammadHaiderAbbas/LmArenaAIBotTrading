"""Performance analytics endpoints.

Kept as one report call: the metrics, the curve, the benchmark and the trade list are
mutually dependent — a Sharpe ratio shown without the curve it came from invites the
reader to trust a number they cannot check.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query
from sqlalchemy import select

from app.api.deps import CurrentUser, DbSession
from app.models.account import TradingAccount
from app.schemas.analytics import (
    Attribution,
    CostBreakdown,
    CurvePoint,
    MetricRead,
    PerformanceReport,
    TradeRead,
)
from app.services import analytics

router = APIRouter(prefix="/analytics", tags=["analytics"])


def _account(db, user, account_id: str | None) -> TradingAccount:
    if account_id:
        account = db.get(TradingAccount, account_id)
        if account is None or account.user_id != user.id:
            raise HTTPException(status_code=404, detail="Account not found")
        return account
    account = db.scalar(
        select(TradingAccount)
        .where(TradingAccount.user_id == user.id)
        .order_by(TradingAccount.created_at)
    )
    if account is None:
        raise HTTPException(status_code=404, detail="No trading account; create one first")
    return account


@router.get("/performance", response_model=PerformanceReport)
def performance(
    db: DbSession,
    user: CurrentUser,
    account_id: str | None = None,
    trade_limit: int = Query(default=50, ge=1, le=500),
) -> PerformanceReport:
    account = _account(db, user, account_id)
    report = analytics.build_report(db, account)

    benchmark_return = None
    if report.benchmark_curve and report.starting_equity > 0:
        final = report.benchmark_curve[-1]["equity"]
        benchmark_return = (final - report.starting_equity) / report.starting_equity

    return PerformanceReport(
        as_of=report.as_of,
        equity=report.equity,
        cash=report.cash,
        starting_equity=report.starting_equity,
        net_return=report.net_return,
        metrics=[MetricRead(**metric.as_dict()) for metric in report.metrics],
        equity_curve=[CurvePoint(**point) for point in report.equity_curve],
        benchmark_curve=[CurvePoint(**point) for point in report.benchmark_curve],
        benchmark_return=benchmark_return,
        attribution=Attribution(**report.attribution),
        costs=CostBreakdown(**report.cost_breakdown),
        trades=[
            TradeRead(
                symbol=trade.symbol,
                quantity=trade.quantity,
                entry_price=trade.entry_price,
                exit_price=trade.exit_price,
                realised_pnl=trade.realised_pnl,
                commission=trade.commission,
                holding_days=round(trade.holding_days, 4),
                return_pct=round(trade.return_pct, 6),
                opened_at=trade.opened_at,
                closed_at=trade.closed_at,
                strategy_code=trade.strategy_code,
            )
            for trade in report.trades[-trade_limit:]
        ],
        notes=report.notes,
    )


@router.get("/trades", response_model=list[TradeRead])
def trades(
    db: DbSession,
    user: CurrentUser,
    account_id: str | None = None,
    limit: int = Query(default=100, ge=1, le=500),
) -> list[TradeRead]:
    account = _account(db, user, account_id)
    rows = analytics.round_trips(db, account)[-limit:]
    return [
        TradeRead(
            symbol=trade.symbol,
            quantity=trade.quantity,
            entry_price=trade.entry_price,
            exit_price=trade.exit_price,
            realised_pnl=trade.realised_pnl,
            commission=trade.commission,
            holding_days=round(trade.holding_days, 4),
            return_pct=round(trade.return_pct, 6),
            opened_at=trade.opened_at,
            closed_at=trade.closed_at,
            strategy_code=trade.strategy_code,
        )
        for trade in rows
    ]


@router.get("/equity-curve", response_model=list[CurvePoint])
def equity_curve(
    db: DbSession, user: CurrentUser, account_id: str | None = None
) -> list[CurvePoint]:
    account = _account(db, user, account_id)
    return [CurvePoint(**point) for point in analytics.equity_curve(db, account)]
