"""Specialist agents and orchestration.

These tests pin the properties that make a multi-agent trading assistant safe to
put in front of a user. The agents must be able to say "I have no data", they must
never produce a tradeable proposal in Phase 5, and the orchestrator must not
manufacture agreement that the evidence does not contain.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from app.models.account import Environment, TradingAccount
from app.models.ai import (
    ConfidenceKind,
    EvidenceKind,
    RunStatus,
    Stance,
)
from app.models.market import (
    Instrument,
    MarketCandle,
    QualityEventType,
    QualitySeverity,
    Timeframe,
)
from app.services.ai import agents, orchestrator

NOW = datetime(2026, 11, 23, 12, 0, tzinfo=UTC)


def make_snapshot(
    *, closes: list[float] | None = None, bars: int = 120, last: datetime | None = None
) -> agents.MarketSnapshot:
    if closes is None:
        # A steady uptrend with small noise: SMA20 > SMA50, MACD positive.
        closes = [100 + index * 0.5 + (0.4 if index % 2 else -0.4) for index in range(bars)]
    return agents.MarketSnapshot(
        symbol="TEST",
        closes=closes,
        highs=[value * 1.004 for value in closes],
        lows=[value * 0.996 for value in closes],
        volumes=[1000.0] * len(closes),
        last_close=closes[-1],
        last_bar_at=last or (NOW - timedelta(hours=6)),
        bar_count=len(closes),
        timeframe="1d",
    )


def make_context(**overrides) -> agents.AgentContext:
    base = {"symbol": "TEST", "timeframe": "1d", "now": NOW, "market": make_snapshot()}
    base.update(overrides)
    context = agents.AgentContext(**base)
    # Indicators are derived the same way the orchestrator derives them.
    if context.market:
        from app.services.market import indicators

        snap = indicators.snapshot(
            closes=context.market.closes, highs=context.market.highs, lows=context.market.lows
        )
        context.indicators = {
            "sma_20": snap.sma_20,
            "sma_50": snap.sma_50,
            "rsi_14": snap.rsi_14,
            "macd": snap.macd,
            "macd_signal": snap.macd_signal,
            "atr_14": snap.atr_14,
            "volatility_20": snap.volatility_20,
        }
    return context


# ------------------------------------------------------------------ technical


def test_uptrend_is_read_as_bullish() -> None:
    finding = agents.technical_analyst(make_context())
    assert finding.stance == Stance.BULLISH
    assert finding.confidence > 0.3
    assert finding.data_available is True


def test_downtrend_is_read_as_bearish() -> None:
    closes = [200 - index * 0.5 + (0.4 if index % 2 else -0.4) for index in range(120)]
    finding = agents.technical_analyst(make_context(market=make_snapshot(closes=closes)))
    assert finding.stance == Stance.BEARISH


def test_missing_data_yields_an_explicit_absence_not_a_neutral_stance() -> None:
    """'I have no data' and 'I see nothing' are different statements."""
    finding = agents.technical_analyst(agents.AgentContext(symbol="X", timeframe="1d", now=NOW))
    assert finding.data_available is False
    assert finding.stance == Stance.UNKNOWN
    assert len(finding.evidence) == 1
    assert finding.evidence[0].kind == EvidenceKind.ABSENCE
    assert "not a neutral signal" in finding.evidence[0].detail


def test_short_history_is_reported_rather_than_faked() -> None:
    finding = agents.technical_analyst(make_context(market=make_snapshot(bars=8)))
    assert finding.data_available is False
    assert "too short" in finding.headline.lower()


def test_every_technical_assertion_carries_evidence() -> None:
    finding = agents.technical_analyst(make_context())
    kinds = {item.kind for item in finding.evidence}
    assert EvidenceKind.MARKET_CANDLE in kinds
    assert EvidenceKind.INDICATOR in kinds
    # Each fact about an indicator is paired with an evidence row.
    indicator_labels = " ".join(item.label for item in finding.evidence)
    assert "SMA" in indicator_labels


def test_quality_state_lowers_confidence() -> None:
    clean = agents.technical_analyst(make_context(quality={"state": "ok", "issues": []}))
    degraded = agents.technical_analyst(
        make_context(quality={"state": "critical", "issues": ["stale data"]})
    )
    assert degraded.confidence < clean.confidence
    assert any("critical" in note for note in degraded.uncertainties)


def test_technical_confidence_is_labelled_a_rule_score() -> None:
    finding = agents.technical_analyst(make_context())
    assert finding.confidence_kind == ConfidenceKind.RULE_SCORE
    assert 0.0 <= finding.confidence <= 1.0


def test_short_history_reduces_confidence() -> None:
    full = agents.technical_analyst(make_context())
    short = agents.technical_analyst(make_context(market=make_snapshot(bars=30)))
    assert short.confidence < full.confidence


# --------------------------------------------------------------------- regime


def test_regime_reports_no_direction() -> None:
    finding = agents.regime_analyst(make_context())
    assert finding.stance == Stance.UNKNOWN
    assert "not a direction" in " ".join(finding.interpretation).lower()


def test_high_volatility_is_classified_against_the_instrument_own_history() -> None:
    calm = [100 + (0.1 if i % 2 else -0.1) for i in range(60)]
    wild = [100 * (1.06 if i % 2 else 0.94) for i in range(60)]
    context = make_context(market=make_snapshot(closes=wild))
    context.regime_history = _vol_series(calm)
    finding = agents.regime_analyst(context)
    assert "High-volatility" in finding.headline


def _vol_series(closes: list[float]) -> list[float]:
    from app.services.market import indicators

    series = indicators.realised_volatility(closes, 20)
    return [value for value in series if value is not None]


def test_regime_without_history_still_reports_and_flags_provisional() -> None:
    finding = agents.regime_analyst(make_context(regime_history=[]))
    assert finding.data_available is True
    assert any("provisional" in note for note in finding.uncertainties)


def test_regime_without_prices_returns_absence() -> None:
    finding = agents.regime_analyst(agents.AgentContext(symbol="X", timeframe="1d", now=NOW))
    assert finding.data_available is False
    assert finding.evidence[0].kind == EvidenceKind.ABSENCE


# ----------------------------------------------------------------------- news


def test_news_agent_refuses_to_convert_text_into_a_direction() -> None:
    """Mapping prose to bullish/bearish needs a validated model; Phase 5 has none."""
    context = make_context(
        research_claims=[
            {"id": "c1", "text": "Inflation 2.6 percent", "verdict": "supported", "url": "https://e.com/1"}
        ],
        research_documents=[
            {
                "id": "d1",
                "title": "Monetary Policy Summary",
                "url": "https://e.com/1",
                "source_name": "Bank of England",
                "source_tier": "primary",
                "published_at": NOW,
            }
        ],
        research_coverage=1.0,
    )
    finding = agents.news_analyst(context)
    assert finding.stance == Stance.UNKNOWN
    assert "does not translate text into a bullish or bearish view" in " ".join(
        finding.interpretation
    )


def test_news_agent_reports_empty_corpus_as_absence() -> None:
    finding = agents.news_analyst(make_context())
    assert finding.data_available is False
    assert finding.evidence[0].kind == EvidenceKind.ABSENCE


def test_unverified_claims_are_counted_separately() -> None:
    context = make_context(
        research_claims=[
            {"id": "c1", "text": "A", "verdict": "supported", "url": ""},
            {"id": "c2", "text": "B", "verdict": "unverified", "url": ""},
            {"id": "c3", "text": "C", "verdict": "contradicted", "url": ""},
        ],
        research_documents=[{"id": "d1", "title": "T", "source_tier": "primary"}],
        research_coverage=0.33,
    )
    finding = agents.news_analyst(context)
    facts = " ".join(finding.facts)
    assert "1 verified" in facts
    assert any(item.label == "2 claim(s) could not be verified" for item in finding.evidence)
    assert any("coverage is thin" in note for note in finding.uncertainties)


def test_news_confidence_rises_with_verification_coverage() -> None:
    docs = [{"id": "d1", "title": "T", "source_tier": "primary"}]
    weak = agents.news_analyst(
        make_context(research_claims=[], research_documents=docs, research_coverage=0.1)
    )
    strong = agents.news_analyst(
        make_context(research_claims=[], research_documents=docs, research_coverage=0.9)
    )
    assert strong.confidence > weak.confidence


# ------------------------------------------------------------------ portfolio


def test_portfolio_agent_without_account_reports_absence() -> None:
    finding = agents.portfolio_analyst(make_context())
    assert finding.data_available is False
    assert finding.evidence[0].kind == EvidenceKind.ABSENCE


def test_portfolio_agent_flags_concentration() -> None:
    finding = agents.portfolio_analyst(
        make_context(
            portfolio={
                "equity": 1000.0,
                "gross_exposure": 2500.0,
                "positions": [{"symbol": "X"}],
            }
        )
    )
    assert finding.data_available is True
    assert any("2.50x equity" in note for note in finding.uncertainties)


# -------------------------------------------------------------------- quality


def test_quality_finding_flags_stale_data() -> None:
    """A stale bar must be visible even when nothing else is wrong."""
    stale = make_snapshot(last=NOW - timedelta(days=5))
    finding = agents.data_quality_finding(
        make_context(market=stale, quality={"state": "ok", "issues": []})
    )
    assert "5.0 days old" in " ".join(finding.facts)


def test_quality_finding_records_clean_data_explicitly() -> None:
    finding = agents.data_quality_finding(make_context(quality={"state": "ok", "issues": []}))
    assert finding.headline == "Market data is clean"
    assert any("no quality events" in item.label for item in finding.evidence)


def test_quality_finding_lists_the_events() -> None:
    finding = agents.data_quality_finding(
        make_context(
            quality={"state": "critical", "issues": ["critical: gap_detected — 40 bars missing"]}
        )
    )
    assert "critical" in finding.headline.lower()
    assert any("gap_detected" in item.label for item in finding.evidence)


# --------------------------------------------------------------- orchestration


@pytest.fixture
def db_session(client):
    from app.database.session import SessionLocal

    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def test_run_refuses_when_no_agent_has_evidence(db_session) -> None:
    """A platform that always finds an opinion is lying about its information."""
    db = db_session
    orchestrator.seed_agents(db)
    db.commit()

    run = orchestrator.run_agents(db, symbol="NOPE/USD", timeframe="1d")
    db.commit()

    assert run.status == RunStatus.REFUSED
    assert run.proposal_stance == Stance.UNKNOWN
    assert run.proposal_confidence is None
    assert run.tradeable is False
    assert "declines" in run.proposal_reason


def test_run_persists_every_finding_with_evidence(db_session) -> None:
    db = db_session
    orchestrator.seed_agents(db)
    db.commit()
    run = orchestrator.run_agents(db, symbol="NOPE/USD", timeframe="1d")
    db.commit()

    # All five implemented agents contribute, even when they have nothing.
    codes = {finding.agent_code for finding in run.findings}
    assert codes == {"technical", "regime", "news", "portfolio", "quality"}
    for finding in run.findings:
        assert finding.evidence, f"{finding.agent_code} produced no evidence row"
        assert finding.confidence_kind == ConfidenceKind.RULE_SCORE


def test_nothing_is_tradeable_in_phase_five(db_session) -> None:
    """The hard invariant for this phase."""
    db = db_session
    orchestrator.seed_agents(db)
    db.commit()
    run = orchestrator.run_agents(db, symbol="NOPE/USD", timeframe="1d")
    db.commit()
    assert run.tradeable is False
    assert run.blocked_reason


def _seed_candles(db, symbol: str, count: int = 160, trend: float = 0.4) -> Instrument:
    from app.models.market import Venue

    venue = db.query(Venue).first()
    if venue is None:
        venue = Venue(code="TEST", name="Test venue", asset_class="forex")
        db.add(venue)
        db.flush()

    instrument = db.query(Instrument).filter(Instrument.symbol == symbol).first()
    if instrument is None:
        instrument = Instrument(
            symbol=symbol,
            name=symbol,
            asset_class="forex",
            venue_id=venue.id,
            base_currency=symbol.split("/")[0],
            quote_currency=symbol.split("/")[-1],
            is_tradable=False,
            status="active",
        )
        db.add(instrument)
        db.flush()
    base = datetime.now(UTC) - timedelta(days=count)
    price = 1.25
    for index in range(count):
        price = max(0.5, price + trend * 0.001 + (0.0006 if index % 2 else -0.0006))
        db.add(
            MarketCandle(
                instrument_id=instrument.id,
                timeframe=Timeframe.D1,
                open_time=base + timedelta(days=index),
                close_time=base + timedelta(days=index, hours=23),
                open=price,
                high=price * 1.003,
                low=price * 0.997,
                close=price,
                volume=1000.0,
                provider="test",
                ingested_at=datetime.now(UTC),
            )
        )
    db.flush()
    return instrument


def test_directional_run_from_real_candles(db_session) -> None:
    db = db_session
    orchestrator.seed_agents(db)
    _seed_candles(db, "TST/UP", trend=0.6)
    db.commit()

    run = orchestrator.run_agents(db, symbol="TST/UP", timeframe="1d")
    db.commit()

    assert run.status in {RunStatus.COMPLETED, RunStatus.PARTIAL}
    assert run.proposal_stance in {Stance.BULLISH, Stance.BEARISH, Stance.NEUTRAL}
    technical = next(f for f in run.findings if f.agent_code == "technical")
    assert technical.data_available is True
    # Still not tradeable, whatever the stance.
    assert run.tradeable is False


def test_high_volatility_against_a_trend_is_recorded_as_a_conflict(db_session) -> None:
    """Conflicting conditions are surfaced, not silently averaged."""
    db = db_session
    orchestrator.seed_agents(db)
    instrument = _seed_candles(db, "TST/VOL", count=160, trend=0.0)
    # Replace with a violently oscillating series.
    db.query(MarketCandle).filter(MarketCandle.instrument_id == instrument.id).delete()
    base = datetime.now(UTC) - timedelta(days=160)
    price = 1.25
    for index in range(160):
        price = price * (1.09 if index % 2 else 0.92)
        price = min(price, 8.0)
        db.add(
            MarketCandle(
                instrument_id=instrument.id,
                timeframe=Timeframe.D1,
                open_time=base + timedelta(days=index),
                close_time=base + timedelta(days=index, hours=23),
                open=price,
                high=price * 1.02,
                low=price * 0.98,
                close=price,
                volume=1000.0,
                provider="test",
                ingested_at=datetime.now(UTC),
            )
        )
    db.commit()

    run = orchestrator.run_agents(db, symbol="TST/VOL", timeframe="1d")
    db.commit()
    regime = next(f for f in run.findings if f.agent_code == "regime")
    assert "High-volatility" in regime.headline
    if run.proposal_stance in {Stance.BULLISH, Stance.BEARISH}:
        assert run.proposal_conflict is True
        assert "argues against acting" in run.proposal_reason


def test_agent_failure_does_not_void_the_run(db_session, monkeypatch) -> None:
    """One broken specialist must not take down the whole result."""
    db = db_session
    orchestrator.seed_agents(db)
    db.commit()

    def explode(context):  # noqa: ANN001, ARG001
        raise RuntimeError("indicator blew up")

    monkeypatch.setitem(orchestrator.agents.AGENTS, "regime", explode)
    run = orchestrator.run_agents(db, symbol="NOPE/USD", timeframe="1d")
    db.commit()

    failed = next(f for f in run.findings if f.agent_code == "regime")
    assert failed.data_available is False
    assert "indicator blew up" in failed.limitations
    assert "indicator blew up" in run.error
    assert run.tradeable is False


def test_account_context_is_used_when_supplied(db_session) -> None:
    db = db_session
    orchestrator.seed_agents(db)
    from app.services import auth_service, ledger_service

    # Created on this same session: a second connection would deadlock on SQLite.
    user = auth_service.register(
        db, email="aic@aurex.ai", full_name="AI Context", password="Str0ngPass!"
    )
    db.flush()
    account = TradingAccount(
        user_id=user.id, name=f"AI context {user.id[:6]}", environment=Environment.PAPER
    )
    db.add(account)
    db.flush()
    ledger_service.open_account_with_funding(db, account=account, amount=5000)
    db.flush()

    run = orchestrator.run_agents(
        db, symbol="NOPE/USD", timeframe="1d", account_id=account.id, requested_by=user.id
    )
    db.commit()

    portfolio = next(f for f in run.findings if f.agent_code == "portfolio")
    assert portfolio.data_available is True
    assert "account equity" in portfolio.facts.lower()


def test_agent_registry_marks_unbuilt_agents_as_unimplemented(db_session) -> None:
    db = db_session
    result = orchestrator.seed_agents(db)
    db.commit()
    assert result["created"] == 8

    from app.models.ai import Agent

    implemented = {agent.code for agent in db.query(Agent) if agent.implemented}
    not_implemented = {agent.code for agent in db.query(Agent) if not agent.implemented}
    assert implemented == {"technical", "regime", "news", "portfolio", "quality"}
    assert not_implemented == {"fundamental", "sentiment", "quantitative"}


def test_seed_agents_is_idempotent(db_session) -> None:
    db = db_session
    orchestrator.seed_agents(db)
    db.commit()
    second = orchestrator.seed_agents(db)
    db.commit()
    assert second["created"] == 0
    assert second["updated"] == 8


def test_run_records_quality_events_as_evidence(db_session) -> None:
    db = db_session
    orchestrator.seed_agents(db)
    instrument = _seed_candles(db, "TST/DQ")
    from app.models.market import DataQualityEvent

    db.add(
        DataQualityEvent(
            instrument_id=instrument.id,
            provider="test",
            event_type=QualityEventType.GAP,
            severity=QualitySeverity.WARNING,
            message="40 bars missing between sessions",
        )
    )
    db.commit()

    run = orchestrator.run_agents(db, symbol="TST/DQ", timeframe="1d")
    db.commit()
    quality = next(f for f in run.findings if f.agent_code == "quality")
    assert "gap" in (quality.facts + quality.headline).lower()
