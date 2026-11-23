"""Chief AI orchestrator.

Runs the specialists, then synthesises. Three rules shape the synthesis:

1. **The orchestrator cannot manufacture agreement.** If the technical read is
   bullish and the news evidence does not support any direction, the run is
   recorded as conflicted or undirected rather than averaged into a tidy number.
2. **Refusal is a valid result.** When no agent produced usable evidence, the run
   returns `refused` with the reason. A trading platform that always finds an
   opinion is lying about its information.
3. **Nothing here can place an order.** The output is a proposal record with a
   stance, a rule-derived confidence and a `tradeable` flag that is `false` until
   Phase 9 wires the independent risk controller. The flag exists so the gap is
   explicit in the data rather than in a comment.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.ai import (
    AgentFinding,
    AgentRun,
    AgentStatus,
    EvidenceKind,
    FindingEvidence,
    RunStatus,
    RunTrigger,
    Stance,
)
from app.models.market import DataQualityEvent, Instrument, MarketCandle, Timeframe
from app.models.research import ReportClaim, ResearchDocument, ResearchReport, SourceRecord
from app.services.ai import agents
from app.services.market import indicators as indicator_service

logger = logging.getLogger(__name__)

# Agents that vote on direction. Quality, news and portfolio are informational:
# they supply context and constraints rather than a stance, and their absence must
# not silently flip a synthesis.
DIRECTIONAL_AGENTS = ("technical",)
INFORMATIONAL_AGENTS = ("news", "regime", "portfolio", "quality")

DISAGREEMENT_THRESHOLD = 0.25


def _quality_context(db: Session, instrument_id: str | None, now: datetime) -> dict:
    if instrument_id is None:
        return {"state": "unknown", "issues": []}
    events = list(
        db.scalars(
            select(DataQualityEvent)
            .where(DataQualityEvent.instrument_id == instrument_id)
            .order_by(DataQualityEvent.created_at.desc())
            .limit(10)
        )
    )
    issues: list[str] = []
    worst = "ok"
    order = {"ok": 0, "info": 1, "warning": 2, "critical": 3}
    for event in events:
        severity = event.severity.value if hasattr(event.severity, "value") else str(event.severity)
        kind = event.event_type.value if hasattr(event.event_type, "value") else str(event.event_type)
        issues.append(f"{severity}: {kind} — {event.message}")
        if order.get(severity, 0) > order.get(worst, 0):
            worst = severity
    return {"state": worst, "issues": issues}


def _market_context(
    db: Session, instrument_id: str | None, timeframe: Timeframe, limit: int = 180
) -> agents.MarketSnapshot | None:
    if instrument_id is None:
        return None
    rows = list(
        db.scalars(
            select(MarketCandle)
            .where(MarketCandle.instrument_id == instrument_id, MarketCandle.timeframe == timeframe)
            .order_by(MarketCandle.open_time.desc())
            .limit(limit)
        )
    )
    if not rows:
        return None
    rows = list(reversed(rows))
    closes = [float(row.close) for row in rows]
    highs = [float(row.high) for row in rows]
    lows = [float(row.low) for row in rows]
    volumes = [float(row.volume or 0) for row in rows]
    return agents.MarketSnapshot(
        symbol=rows[-1].instrument_id,
        closes=closes,
        highs=highs,
        lows=lows,
        volumes=volumes,
        last_close=closes[-1],
        last_bar_at=rows[-1].open_time,
        bar_count=len(rows),
        timeframe=timeframe.value,
    )


def _regime_history(db: Session, instrument_id: str | None, timeframe: Timeframe) -> list[float]:
    """Rolling 20-bar volatility readings, for percentile context."""
    if instrument_id is None:
        return []
    rows = list(
        db.scalars(
            select(MarketCandle)
            .where(MarketCandle.instrument_id == instrument_id, MarketCandle.timeframe == timeframe)
            .order_by(MarketCandle.open_time.asc())
            .limit(400)
        )
    )
    closes = [float(row.close) for row in rows]
    series = indicator_service.realised_volatility(closes, 20)
    return [round(value, 4) for value in series if value is not None][-120:]


def _research_context(db: Session, symbol: str) -> tuple[list[dict], list[dict], float]:
    """Pull verified claims and documents linked to this instrument."""
    reports = list(
        db.scalars(
            select(ResearchReport)
            .where(ResearchReport.instrument_symbol == symbol)
            .order_by(ResearchReport.created_at.desc())
            .limit(3)
        )
    )
    claims: list[dict] = []
    documents: dict[str, dict] = {}
    total = 0
    verified = 0

    for report in reports:
        rows = list(
            db.scalars(
                select(ReportClaim)
                .where(ReportClaim.report_id == report.id)
                .order_by(ReportClaim.ordinal)
            )
        )
        total += report.claims_total
        verified += report.claims_supported
        for claim in rows:
            citation = claim.citations[0] if claim.citations else None
            claims.append(
                {
                    "id": claim.id,
                    "text": claim.text,
                    "verdict": claim.verdict.value
                    if hasattr(claim.verdict, "value")
                    else str(claim.verdict),
                    "url": citation.url if citation else "",
                }
            )
            if citation is None or not citation.document_id:
                continue
            document = db.scalar(
                select(ResearchDocument).where(ResearchDocument.id == citation.document_id)
            )
            if document is None or document.id in documents:
                continue
            source = db.scalar(select(SourceRecord).where(SourceRecord.id == document.source_id))
            documents[document.id] = {
                "id": document.id,
                "title": document.title,
                "url": document.url,
                "published_at": document.published_at,
                "source_name": source.name if source else citation.source_name,
                "source_tier": source.trust_tier.value if source else "",
            }

    coverage = (verified / total) if total else 0.0
    return claims, list(documents.values()), round(coverage, 3)


def _portfolio_context(db: Session, account_id: str | None) -> dict:
    if not account_id:
        return {}
    from app.models.account import TradingAccount
    from app.services import ledger_service

    account = db.get(TradingAccount, account_id)
    if account is None:
        return {}
    balances = ledger_service.ensure_ledger_accounts(db, account)
    cash = float(ledger_service.ledger_cash(db, account))

    # Positions arrive in Phase 8. Until then gross exposure is zero by
    # construction, and the finding says so rather than implying a flat book.
    gross_exposure = 0.0
    return {
        "equity": cash,
        "gross_exposure": gross_exposure,
        "positions": [],
        "ledger_accounts": len(balances),
        "positions_available": False,
    }


def build_context(
    db: Session,
    *,
    symbol: str,
    timeframe: str,
    account_id: str | None,
    now: datetime | None = None,
) -> agents.AgentContext:
    now = now or datetime.now(UTC)
    instrument = db.scalar(select(Instrument).where(Instrument.symbol == symbol))
    timeframe_enum = Timeframe(timeframe) if timeframe in {t.value for t in Timeframe} else Timeframe.D1
    instrument_id = instrument.id if instrument else None

    market = _market_context(db, instrument_id, timeframe_enum)
    indicator_values: dict[str, float | None] = {}
    if market:
        snap = indicator_service.snapshot(closes=market.closes, highs=market.highs, lows=market.lows)
        indicator_values = {
            field: getattr(snap, field)
            for field in (
                "sma_20",
                "sma_50",
                "ema_20",
                "rsi_14",
                "macd",
                "macd_signal",
                "macd_histogram",
                "atr_14",
                "bb_upper",
                "bb_middle",
                "bb_lower",
                "volatility_20",
                "change_pct_1",
                "change_pct_5",
                "change_pct_20",
            )
        }

    claims, documents, coverage = _research_context(db, symbol)
    return agents.AgentContext(
        symbol=symbol,
        timeframe=timeframe_enum.value,
        now=now,
        market=market,
        indicators=indicator_values,
        quality=_quality_context(db, instrument_id, now),
        research_claims=claims,
        research_documents=documents,
        research_coverage=coverage,
        portfolio=_portfolio_context(db, account_id),
        regime_history=_regime_history(db, instrument_id, timeframe_enum),
    )


def _persist_finding(db: Session, run: AgentRun, finding: agents.Finding, ordinal: int) -> AgentFinding:
    row = AgentFinding(
        run_id=run.id,
        agent_code=finding.agent_code,
        ordinal=ordinal,
        headline=finding.headline[:400],
        stance=finding.stance,
        confidence=round(finding.confidence, 4),
        confidence_kind=finding.confidence_kind,
        facts=finding.joined("facts"),
        interpretation=finding.joined("interpretation"),
        uncertainties=finding.joined("uncertainties"),
        limitations=finding.joined("limitations"),
        data_available=finding.data_available,
    )
    db.add(row)
    db.flush()
    for index, item in enumerate(finding.evidence):
        db.add(
            FindingEvidence(
                finding_id=row.id,
                ordinal=index,
                kind=item.kind,
                label=item.label[:200],
                detail=item.detail,
                value=item.value,
                unit=item.unit,
                reference_id=item.reference_id,
                url=item.url,
                observed_at=item.observed_at,
            )
        )
    db.flush()
    return row


def _synthesise(findings: list[agents.Finding]) -> tuple[Stance, float | None, bool, str, bool, str]:
    """Combine findings into a proposal.

    Returns (stance, confidence, conflicted, reason, tradeable, blocked_reason).
    """
    directional = [f for f in findings if f.agent_code in DIRECTIONAL_AGENTS and f.data_available]
    # The quality agent always has something to say about data, so it never counts
    # as substantive evidence. If it did, a run with no prices, no research and no
    # account would report "partial" rather than refusing.
    usable = [f for f in findings if f.data_available and f.agent_code != "quality"]
    evidential = [f for f in usable if any(e.kind != EvidenceKind.ABSENCE for e in f.evidence)]

    if not directional:
        return (
            Stance.UNKNOWN,
            None,
            False,
            "No directional agent produced a usable finding; the run declines to propose a stance.",
            False,
            "no directional evidence",
        )

    technical = directional[0]
    news = next((f for f in findings if f.agent_code == "news"), None)
    regime = next((f for f in findings if f.agent_code == "regime"), None)

    reason_parts: list[str] = [f"Technical: {technical.headline.lower()} ({technical.stance.value})."]
    conflicted = False

    # A directional technical read against a high-volatility regime is a conflict
    # of conditions, not of opinion: the platform says so rather than picking one.
    if regime and regime.data_available and "High-volatility" in regime.headline:
        conflicted = True
        reason_parts.append("Regime: volatility is elevated, which argues against acting on a trend signal alone.")

    if news and news.data_available and news.confidence and technical.confidence:
        # News carries no stance, so it can only ever damp confidence, never set one.
        reason_parts.append(f"News: {news.headline.lower()}.")

    if technical.stance == Stance.NEUTRAL:
        return (
            Stance.NEUTRAL,
            round(technical.confidence * 0.6, 4),
            conflicted,
            " ".join(reason_parts) + " Indicators do not agree enough to propose a direction.",
            False,
            "indicators are mixed",
        )

    confidence = technical.confidence
    if conflicted:
        confidence *= 0.6
    if len(evidential) < 2:
        confidence *= 0.85
        reason_parts.append("Only one agent produced usable evidence.")

    confidence = max(0.0, min(1.0, confidence))
    if confidence < 0.35:
        return (
            Stance.UNKNOWN,
            round(confidence, 4),
            conflicted,
            " ".join(reason_parts) + " Combined confidence is below the proposal floor.",
            False,
            "confidence below floor",
        )

    blocked = (
        "Execution is not enabled: the independent risk controller arrives in Phase 9, "
        "and no order path exists in Phase 5."
    )
    return (technical.stance, round(confidence, 4), conflicted, " ".join(reason_parts), False, blocked)


def run_agents(
    db: Session,
    *,
    symbol: str,
    timeframe: str = "1d",
    account_id: str | None = None,
    requested_by: str | None = None,
    question: str = "",
    trigger: RunTrigger = RunTrigger.MANUAL,
    context: agents.AgentContext | None = None,
) -> AgentRun:
    """Execute one orchestrated pass and persist it."""
    started = datetime.now(UTC)
    run = AgentRun(
        account_id=account_id,
        requested_by=requested_by,
        instrument_symbol=symbol,
        timeframe=timeframe,
        trigger=trigger,
        status=RunStatus.RUNNING,
        question=question,
        started_at=started,
    )
    db.add(run)
    db.flush()

    ctx = context or build_context(
        db, symbol=symbol, timeframe=timeframe, account_id=account_id
    )

    findings: list[agents.Finding] = []
    errors: list[str] = []
    for ordinal, (code, agent) in enumerate(agents.AGENTS.items()):
        try:
            finding = agent(ctx)
        except Exception as exc:  # noqa: BLE001 - one agent failing must not void the run
            logger.exception("agent %s failed for %s", code, symbol)
            errors.append(f"{code}: {exc}")
            finding = agents.Finding(
                agent_code=code,
                headline=f"{code} agent failed",
                data_available=False,
                uncertainties=[f"The {code} agent raised an error and contributed nothing."],
                limitations=[str(exc)[:300]],
                evidence=[
                    agents.Evidence(
                        kind=EvidenceKind.ABSENCE,
                        label="agent error",
                        detail=str(exc)[:500],
                    )
                ],
            )
        findings.append(finding)
        _persist_finding(db, run, finding, ordinal)

    stance, confidence, conflicted, reason, tradeable, blocked = _synthesise(findings)
    run.proposal_stance = stance
    run.proposal_confidence = confidence
    run.proposal_conflict = conflicted
    run.proposal_reason = reason
    run.tradeable = tradeable
    run.blocked_reason = blocked

    usable = [f for f in findings if f.data_available and f.agent_code != "quality"]
    if not usable:
        run.status = RunStatus.REFUSED
    elif errors or len(usable) < len(findings):
        run.status = RunStatus.PARTIAL
    else:
        run.status = RunStatus.COMPLETED

    run.finished_at = datetime.now(UTC)
    run.duration_ms = int((run.finished_at - started).total_seconds() * 1000)
    run.error = "; ".join(errors)[:2000]
    db.flush()
    return run


AGENT_REGISTRY = [
    {
        "code": "technical",
        "name": "Technical Analyst",
        "role": "technical",
        "description": "Trend, momentum and volatility from price structure and indicators.",
        "implemented": True,
        "inputs": "OHLCV candles, SMA/EMA/RSI/MACD/ATR/Bollinger",
        "outputs": "Directional stance from indicator agreement, with evidence per indicator",
    },
    {
        "code": "regime",
        "name": "Market Regime Analyst",
        "role": "regime",
        "description": "Volatility regime and drift relative to the instrument's own history.",
        "implemented": True,
        "inputs": "Realised volatility series over the instrument's history",
        "outputs": "Regime classification (high/normal/low) and drift, with no direction",
    },
    {
        "code": "news",
        "name": "News Analyst",
        "role": "news",
        "description": "Summarises verified research evidence and source quality. Reports no stance.",
        "implemented": True,
        "inputs": "Phase 4 reports, claims, verdicts and source trust tiers",
        "outputs": "Evidence summary with verification coverage and freshness limits",
    },
    {
        "code": "portfolio",
        "name": "Portfolio Analyst",
        "role": "portfolio",
        "description": "Exposure and concentration for the attached paper account.",
        "implemented": True,
        "inputs": "Reconciled ledger balances and open positions",
        "outputs": "Exposure multiple and concentration notes",
    },
    {
        "code": "quality",
        "name": "Data Quality Monitor",
        "role": "risk",
        "description": "Surfaces stale, gapped or outlier market data as a finding in its own right.",
        "implemented": True,
        "inputs": "Phase 3 quality events",
        "outputs": "Quality state and the specific events behind it",
    },
    {
        "code": "fundamental",
        "name": "Fundamental Analyst",
        "role": "fundamental",
        "description": "Filings, earnings and valuation context from SEC and macro data.",
        "implemented": False,
        "inputs": "SEC filings, financial statements",
        "outputs": "Valuation and financial-health context",
    },
    {
        "code": "sentiment",
        "name": "Sentiment Analyst",
        "role": "sentiment",
        "description": "Licensed social and positioning signals with crowding indicators.",
        "implemented": False,
        "inputs": "Licensed sentiment and positioning feeds",
        "outputs": "Sentiment measures, crowding flags",
    },
    {
        "code": "quantitative",
        "name": "Quantitative Analyst",
        "role": "quantitative",
        "description": "Model forecasts with calibration and uncertainty from Phase 6 models.",
        "implemented": False,
        "inputs": "Validated model outputs and feature sets",
        "outputs": "Forecasts, uncertainty, calibration evidence",
    },
]


def seed_agents(db: Session) -> dict[str, int]:
    """Register agent metadata so the UI can show what exists and what does not."""
    from app.models.ai import Agent, AgentRole

    created = 0
    updated = 0
    for spec in AGENT_REGISTRY:
        existing = db.scalar(select(Agent).where(Agent.code == spec["code"]))
        if existing is None:
            db.add(
                Agent(
                    code=spec["code"],
                    name=spec["name"],
                    role=AgentRole(spec["role"]),
                    description=spec["description"],
                    status=AgentStatus.ACTIVE if spec["implemented"] else AgentStatus.SHADOW,
                    implemented=spec["implemented"],
                    deterministic=True,
                    inputs=spec["inputs"],
                    outputs=spec["outputs"],
                )
            )
            created += 1
        else:
            existing.name = spec["name"]
            existing.description = spec["description"]
            existing.implemented = spec["implemented"]
            existing.inputs = spec["inputs"]
            existing.outputs = spec["outputs"]
            existing.status = AgentStatus.ACTIVE if spec["implemented"] else AgentStatus.SHADOW
            updated += 1
    db.flush()
    return {"created": created, "updated": updated, "total": created + updated}
