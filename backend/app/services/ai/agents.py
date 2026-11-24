"""Specialist agents.

Every agent is a pure function of evidence already in the database. None of them
calls a language model, and none of them can place an order:

```
agent(context) -> Finding
```

The contract each agent keeps:

1. **Say what the data was.** If candles are missing, the finding is
   `data_available=False` with an explicit absence record. It does not invent a
   neutral stance to look productive; "I have no data" and "I see nothing" are
   different statements and the platform must not confuse them.
2. **Separate the observation from the reading.** `facts` hold measured values;
   `interpretation` holds the judgement drawn from them. The UI never merges them.
3. **Attach evidence to every assertion.** A stance with no evidence row is
   rejected by the orchestrator.
4. **Be honest about confidence.** `confidence_kind` is `rule_score` throughout
   Phase 5. Nothing here is a calibrated probability.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime

from app.models.ai import ConfidenceKind, EvidenceKind, Stance


@dataclass
class Evidence:
    kind: EvidenceKind
    label: str
    detail: str = ""
    value: float | None = None
    unit: str = ""
    reference_id: str | None = None
    url: str = ""
    observed_at: datetime | None = None


@dataclass
class Finding:
    agent_code: str
    headline: str
    stance: Stance = Stance.UNKNOWN
    confidence: float = 0.0
    confidence_kind: ConfidenceKind = ConfidenceKind.RULE_SCORE
    facts: list[str] = field(default_factory=list)
    interpretation: list[str] = field(default_factory=list)
    uncertainties: list[str] = field(default_factory=list)
    limitations: list[str] = field(default_factory=list)
    evidence: list[Evidence] = field(default_factory=list)
    data_available: bool = True

    def joined(self, attribute: str) -> str:
        return "\n".join(getattr(self, attribute))


def clamp(value: float, low: float = 0.0, high: float = 1.0) -> float:
    return max(low, min(high, value))


def aware(value: datetime | None) -> datetime | None:
    """Normalise a stored datetime to UTC-aware.

    SQLite returns naive datetimes even for `DateTime(timezone=True)` columns, so
    subtracting one from `datetime.now(UTC)` raises. Every age calculation goes
    through here instead of assuming the driver behaved.
    """
    if value is None:
        return None
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


def age_hours(value: datetime | None, now: datetime) -> float | None:
    normalised = aware(value)
    if normalised is None:
        return None
    return (now - normalised).total_seconds() / 3600


@dataclass
class MarketSnapshot:
    symbol: str
    closes: list[float]
    highs: list[float]
    lows: list[float]
    volumes: list[float]
    last_close: float
    last_bar_at: datetime | None
    bar_count: int
    timeframe: str


@dataclass
class AgentContext:
    """Everything an agent is allowed to look at.

    Passing a context object rather than a live session keeps agents from
    reaching into tables they were not given, and makes them trivially testable.
    """

    symbol: str
    timeframe: str
    now: datetime
    market: MarketSnapshot | None = None
    indicators: dict[str, float | None] = field(default_factory=dict)
    quality: dict[str, object] = field(default_factory=dict)
    research_claims: list[dict] = field(default_factory=list)
    research_documents: list[dict] = field(default_factory=list)
    research_coverage: float = 0.0
    portfolio: dict[str, object] = field(default_factory=dict)
    regime_history: list[float] = field(default_factory=list)
    news_summary: dict[str, object] = field(default_factory=dict)
    # A champion model's probability for the next bar, and the metadata needed to
    # read it honestly. None means no champion exists for this instrument.
    model_forecast: dict[str, object] | None = None

    @property
    def has_market(self) -> bool:
        return bool(self.market and self.market.bar_count)


# ---------------------------------------------------------------- technical


def technical_analyst(context: AgentContext) -> Finding:
    """Trend, momentum and volatility from price structure alone."""
    if not context.has_market:
        return Finding(
            agent_code="technical",
            headline="No price history available for this instrument",
            data_available=False,
            evidence=[
                Evidence(
                    kind=EvidenceKind.ABSENCE,
                    label="market candles",
                    detail=(
                        "No candles are stored for this instrument and timeframe, so no "
                        "technical read is possible. This is a data gap, not a neutral signal."
                    ),
                )
            ],
            limitations=["Technical analysis requires price history; none was present."],
        )

    market = context.market
    assert market is not None
    ind = context.indicators
    price = market.last_close
    sma20 = ind.get("sma_20")
    sma50 = ind.get("sma_50")
    rsi = ind.get("rsi_14")
    macd = ind.get("macd")
    macd_signal = ind.get("macd_signal")
    atr = ind.get("atr_14")
    vol = ind.get("volatility_20")

    facts: list[str] = [
        f"Last close {price:.4f} on {market.last_bar_at:%d %b %Y}"
        if market.last_bar_at
        else f"Last close {price:.4f}",
        f"{market.bar_count} bars analysed at {context.timeframe}",
    ]
    evidence: list[Evidence] = [
        Evidence(
            kind=EvidenceKind.MARKET_CANDLE,
            label="last close",
            detail=f"{market.symbol} {context.timeframe} close",
            value=price,
            unit="price",
            observed_at=market.last_bar_at,
        )
    ]

    score = 0.0
    weight = 0.0

    if sma20 is not None:
        facts.append(f"SMA(20) {sma20:.4f}")
        distance = (price - sma20) / sma20 if sma20 else 0.0
        direction = 1.0 if distance > 0 else -1.0
        score += direction * clamp(abs(distance) * 25, 0.0, 1.0)
        weight += 1.0
        evidence.append(
            Evidence(
                kind=EvidenceKind.INDICATOR,
                label="SMA(20)",
                detail=f"close is {distance * 100:+.2f}% versus the 20-period mean",
                value=sma20,
                unit="price",
            )
        )

    if sma20 is not None and sma50 is not None:
        facts.append(f"SMA(50) {sma50:.4f}")
        cross = (sma20 - sma50) / sma50 if sma50 else 0.0
        score += (1.0 if cross > 0 else -1.0) * clamp(abs(cross) * 40, 0.0, 1.0)
        weight += 1.0
        evidence.append(
            Evidence(
                kind=EvidenceKind.INDICATOR,
                label="SMA(20) vs SMA(50)",
                detail="20-period mean above 50-period mean"
                if cross > 0
                else "20-period mean below 50-period mean",
                value=cross * 100,
                unit="%",
            )
        )

    if macd is not None and macd_signal is not None:
        facts.append(f"MACD {macd:.4f} vs signal {macd_signal:.4f}")
        hist = macd - macd_signal
        scale = (atr or (price * 0.01)) or 1.0
        score += (1.0 if hist > 0 else -1.0) * clamp(abs(hist) / (scale * 0.5), 0.0, 1.0)
        weight += 1.0
        evidence.append(
            Evidence(
                kind=EvidenceKind.INDICATOR,
                label="MACD histogram",
                detail="MACD above its signal line" if hist > 0 else "MACD below its signal line",
                value=hist,
                unit="price",
            )
        )

    interpretation: list[str] = []
    uncertainties: list[str] = []
    confidence_reducers: list[str] = []

    if rsi is not None:
        facts.append(f"RSI(14) {rsi:.1f}")
        evidence.append(
            Evidence(
                kind=EvidenceKind.INDICATOR,
                label="RSI(14)",
                detail="momentum oscillator on 14 periods",
                value=rsi,
                unit="index",
            )
        )
        if rsi >= 70:
            interpretation.append(
                f"RSI {rsi:.1f} is in overbought territory; trend may be stretched."
            )
            confidence_reducers.append("momentum reading is extended")
        elif rsi <= 30:
            interpretation.append(
                f"RSI {rsi:.1f} is in oversold territory; a bounce is possible but unconfirmed."
            )
            confidence_reducers.append("momentum reading is extended")

    if atr is not None and price:
        atr_pct = atr / price * 100
        facts.append(f"ATR(14) {atr:.4f} ({atr_pct:.2f}% of price)")
        if atr_pct > 4:
            uncertainties.append(
                f"Daily range is {atr_pct:.1f}% of price; stops implied by this volatility are wide."
            )

    if vol is not None:
        facts.append(f"Realised volatility {vol:.2f}%")
        if vol > 40:
            confidence_reducers.append(f"realised volatility {vol:.1f}% is high")

    if weight == 0:
        return Finding(
            agent_code="technical",
            headline="Price history is too short for trend indicators",
            data_available=False,
            facts=facts,
            evidence=evidence,
            limitations=["Fewer bars than the shortest indicator period (20)."],
        )

    normalised = score / weight
    if normalised > 0.15:
        stance = Stance.BULLISH
        headline = "Trend indicators lean constructive"
    elif normalised < -0.15:
        stance = Stance.BEARISH
        headline = "Trend indicators lean defensive"
    else:
        stance = Stance.NEUTRAL
        headline = "Trend indicators are mixed"

    # Confidence reflects signal agreement and data quality, not predictive skill.
    agreement = abs(normalised)
    quality = str(context.quality.get("state", "ok"))
    confidence = clamp(0.35 + agreement * 0.45)
    if quality != "ok":
        confidence -= 0.15
        uncertainties.append(
            f"Data quality for this instrument is '{quality}', lowering confidence."
        )

    if market.bar_count < 60:
        # Capping, not subtracting: a short history removes corroborating checks,
        # so it must not be possible to end up more confident than with full data.
        confidence = min(confidence, 0.45)
        uncertainties.append(
            f"Only {market.bar_count} bars are available; the 50-period average is "
            "unavailable or fragile, so the trend read is capped at low confidence."
        )

    interpretation.append(
        "Indicator agreement is measured, not predicted: this is a description of the recent "
        "price structure and carries no claim about future returns."
    )
    if confidence_reducers:
        uncertainties.append("Confidence reduced because " + "; ".join(confidence_reducers) + ".")

    return Finding(
        agent_code="technical",
        headline=headline,
        stance=stance,
        confidence=clamp(confidence),
        facts=facts,
        interpretation=interpretation,
        uncertainties=uncertainties,
        limitations=[
            "Rule-based scoring over indicators; not validated against forward returns.",
            "Indicators are lagging by construction and say nothing about news or positioning.",
        ],
        evidence=evidence,
    )


# ------------------------------------------------------------------- regime


def regime_analyst(context: AgentContext) -> Finding:
    """Volatility regime and drift, from price behaviour only."""
    if not context.has_market:
        return Finding(
            agent_code="regime",
            headline="No price history available to classify a regime",
            data_available=False,
            evidence=[
                Evidence(
                    kind=EvidenceKind.ABSENCE,
                    label="market candles",
                    detail="Regime classification needs a price series; none is stored.",
                )
            ],
        )

    market = context.market
    assert market is not None
    vol = context.indicators.get("volatility_20")
    closes = market.closes

    facts = [f"{len(closes)} closes analysed"]
    evidence: list[Evidence] = []

    if vol is None:
        return Finding(
            agent_code="regime",
            headline="Not enough history to classify volatility",
            data_available=False,
            facts=facts,
            limitations=["Realised volatility needs at least 21 closes."],
        )

    facts.append(f"Realised 20-bar volatility {vol:.2f}%")
    evidence.append(
        Evidence(
            kind=EvidenceKind.INDICATOR,
            label="realised volatility",
            detail="standard deviation of 20 log returns, annualised",
            value=vol,
            unit="%",
        )
    )

    # Compare the current window with the instrument's own recent history rather
    # than an arbitrary absolute threshold: "high for this instrument" is the
    # question a reader actually has.
    reference = context.regime_history
    state = "normal"
    if len(reference) >= 10:
        ordered = sorted(reference)
        p20 = ordered[int(len(ordered) * 0.2)]
        p80 = ordered[int(len(ordered) * 0.8)]
        if vol >= p80:
            state = "high"
        elif vol <= p20:
            state = "low"
        facts.append(f"20-bar volatility distribution median {ordered[len(ordered) // 2]:.2f}%")
    else:
        state = "high" if vol > 35 else ("low" if vol < 12 else "normal")

    # Drift: signed net move over the last 20 closes, scaled by volatility.
    window = closes[-20:]
    drift = (window[-1] - window[0]) / window[0] * 100 if window[0] else 0.0
    facts.append(f"Net move over last 20 bars {drift:+.2f}%")
    evidence.append(
        Evidence(
            kind=EvidenceKind.COMPUTATION,
            label="20-bar drift",
            detail="percentage change from the 20th-most-recent close to the last close",
            value=drift,
            unit="%",
        )
    )

    if state == "high":
        headline = "High-volatility regime"
        stance = Stance.UNKNOWN
        uncertainties = [
            "High volatility widens the range of plausible outcomes; position sizing, not direction, is the relevant response."
        ]
    elif state == "low":
        headline = "Low-volatility regime"
        stance = Stance.UNKNOWN
        uncertainties = [
            "Compressed volatility can persist or resolve abruptly; regime absence is not a directional signal."
        ]
    else:
        headline = "Volatility is within its normal range for this instrument"
        stance = Stance.UNKNOWN
        uncertainties = []

    confidence = clamp(0.4 + min(len(reference), 60) / 60 * 0.35)
    if len(reference) < 10:
        confidence -= 0.15
        uncertainties.append(
            "Volatility percentile uses a short reference window; the classification is provisional."
        )

    return Finding(
        agent_code="regime",
        headline=headline,
        stance=stance,
        confidence=clamp(confidence),
        facts=facts,
        interpretation=[
            f"The current window is classified '{state}' against this instrument's own recent volatility.",
            "A regime is not a direction. This agent reports conditions, not trades.",
        ],
        uncertainties=uncertainties,
        limitations=[
            "Single-instrument volatility only; cross-asset correlation regimes are not modelled.",
            "Regime stability is not validated over multiple cycles.",
        ],
        evidence=evidence,
    )


# --------------------------------------------------------------------- news


def news_analyst(context: AgentContext) -> Finding:
    """What the research corpus says, weighted by verification status."""
    claims = context.research_claims
    documents = context.research_documents

    if not claims and not documents:
        return Finding(
            agent_code="news",
            headline="No relevant research evidence was retrieved",
            data_available=False,
            evidence=[
                Evidence(
                    kind=EvidenceKind.ABSENCE,
                    label="research corpus",
                    detail=(
                        "No ingested document matched this instrument or topic. Absence of "
                        "evidence is recorded as absence, not as a neutral read."
                    ),
                )
            ],
            limitations=[
                "The research corpus is empty for this query; no conclusion can be drawn."
            ],
        )

    supported = [c for c in claims if c.get("verdict") == "supported"]
    unverified = [
        c for c in claims if c.get("verdict") in {"unverified", "unsupported", "contradicted"}
    ]
    trusted = [d for d in documents if d.get("source_tier") == "primary"]

    facts = [
        f"{len(documents)} document(s) retrieved",
        f"{len(claims)} claim(s) extracted, {len(supported)} verified verbatim against their source",
        f"{len(trusted)} document(s) from a primary source (central bank, regulator)",
    ]
    evidence: list[Evidence] = []

    for doc in documents[:4]:
        evidence.append(
            Evidence(
                kind=EvidenceKind.RESEARCH_CLAIM,
                label=doc.get("title", "")[:180],
                detail=f"{doc.get('source_name', '')} · {doc.get('source_tier', '')}",
                url=doc.get("url", ""),
                reference_id=doc.get("id"),
                observed_at=doc.get("published_at"),
            )
        )
    for claim in supported[:4]:
        evidence.append(
            Evidence(
                kind=EvidenceKind.RESEARCH_CLAIM,
                label=claim.get("text", "")[:180],
                detail=f"verdict: {claim.get('verdict')}",
                url=claim.get("url", ""),
                reference_id=claim.get("id"),
            )
        )

    if unverified:
        evidence.append(
            Evidence(
                kind=EvidenceKind.RESEARCH_CLAIM,
                label=f"{len(unverified)} claim(s) could not be verified",
                detail="These are reported in the research report as unverified and are not used to support a stance.",
            )
        )

    # The news agent deliberately reports no stance. Mapping text to direction is
    # exactly the step that needs a validated model, and none exists yet.
    confidence = clamp(context.research_coverage * 0.5 + (0.2 if trusted else 0.0) + 0.1)

    uncertainties: list[str] = []
    if not supported:
        uncertainties.append(
            "No claim survived verification, so the corpus supports no directional reading."
        )
    if context.research_coverage < 0.5:
        uncertainties.append(
            f"Only {context.research_coverage * 100:.0f}% of extracted claims were verified; coverage is thin."
        )
    if not trusted:
        uncertainties.append("All retrieved documents are commentary rather than primary sources.")

    return Finding(
        agent_code="news",
        headline=(
            f"{len(supported)} verified claim(s) across {len(documents)} document(s)"
            if supported
            else f"{len(documents)} document(s) retrieved but nothing verified"
        ),
        stance=Stance.UNKNOWN,
        confidence=confidence,
        facts=facts,
        interpretation=[
            "This agent summarises what sources say and how well it is verified. It does not "
            "translate text into a bullish or bearish view: that requires a model validated "
            "against outcomes, which Phase 5 does not have."
        ],
        uncertainties=uncertainties,
        limitations=[
            "Retrieval is lexical with declared synonyms; genuine paraphrase may be missed.",
            "Freshness is bounded by the ingestion cadence, not real time.",
        ],
        evidence=evidence,
        data_available=True,
    )


# ----------------------------------------------------------------- portfolio


def portfolio_analyst(context: AgentContext) -> Finding:
    """Exposure and concentration for the account, if an account was supplied."""
    portfolio = context.portfolio
    if not portfolio:
        return Finding(
            agent_code="portfolio",
            headline="No account context supplied",
            data_available=False,
            evidence=[
                Evidence(
                    kind=EvidenceKind.ABSENCE,
                    label="account state",
                    detail="No account was attached to this run, so no exposure analysis is possible.",
                )
            ],
        )

    positions = portfolio.get("positions", [])
    equity = float(portfolio.get("equity", 0.0) or 0.0)
    gross = float(portfolio.get("gross_exposure", 0.0) or 0.0)
    facts = [
        f"Account equity {equity:,.2f}",
        f"{len(positions)} open position(s)",
        f"Gross exposure {gross:,.2f}",
    ]
    evidence: list[Evidence] = [
        Evidence(
            kind=EvidenceKind.COMPUTATION,
            label="account equity",
            detail="derived from reconciled ledger balances",
            value=equity,
            unit="cash",
        )
    ]

    concentration = 0.0
    if equity > 0 and gross > 0:
        concentration = gross / equity
        facts.append(f"Gross exposure is {concentration:.2f}x equity")

    uncertainties: list[str] = []
    if concentration > 1.0:
        uncertainties.append(
            f"Gross exposure is {concentration:.2f}x equity; a held instrument is already a large share of the book."
        )
    if not positions:
        uncertainties.append(
            "The account holds no positions; any proposal would be an opening trade."
        )

    return Finding(
        agent_code="portfolio",
        headline=(
            f"{len(positions)} position(s), gross exposure {concentration:.2f}x equity"
            if equity
            else "Account has no equity to size against"
        ),
        stance=Stance.UNKNOWN,
        confidence=0.6 if equity else 0.2,
        facts=facts,
        interpretation=[
            "Exposure is reported, not judged. Sizing decisions belong to the risk controller."
        ],
        uncertainties=uncertainties,
        limitations=["Correlation between holdings is not modelled in Phase 5."],
        evidence=evidence,
    )


# ------------------------------------------------------------------- quality


def data_quality_finding(context: AgentContext) -> Finding:
    """Surfaces data problems as a finding in their own right.

    Data quality is not a side note attached to another agent's output: if the
    candles are stale, every other conclusion inherits that. Giving it a row of
    its own means it cannot be missed in the UI.
    """
    quality = context.quality or {}
    state = str(quality.get("state", "unknown"))
    issues = list(quality.get("issues", []) or [])

    facts = [f"Quality state: {state}"]
    bar_age = age_hours(context.market.last_bar_at, context.now) if context.market else None
    if bar_age is not None:
        facts.append(f"Newest bar is {bar_age:.1f} hours old")
        if bar_age > 72:
            issues.append(f"stale: newest bar is {bar_age / 24:.1f} days old")
    facts.extend(issues[:6])

    evidence = [
        Evidence(
            kind=EvidenceKind.QUALITY_EVENT,
            label=issue,
            detail="recorded during market-data ingestion",
        )
        for issue in issues[:6]
    ]
    if not issues:
        evidence.append(
            Evidence(
                kind=EvidenceKind.COMPUTATION,
                label="no quality events",
                detail="Ingestion recorded no gaps, duplicates, outliers or stale windows for this instrument.",
            )
        )

    return Finding(
        agent_code="quality",
        headline=(
            "Market data is clean" if state == "ok" else f"Market data problems recorded: {state}"
        ),
        stance=Stance.UNKNOWN,
        confidence=0.9 if state == "ok" else 0.5,
        facts=facts,
        interpretation=["Quality is reported before any other agent's evidence is trusted."],
        uncertainties=["Degraded data lowers the confidence of every finding that depends on it."]
        if state != "ok"
        else [],
        limitations=[
            "Quality checks are structural (gaps, staleness, outliers), not correctness checks."
        ],
        evidence=evidence,
    )


# ---------------------------------------------------------------- quantitative


def quantitative_analyst(context: AgentContext) -> Finding:
    """Reports a champion model's forecast, or says there is no champion.

    This agent exists so Phase 6 is visible in the product rather than sitting in
    a registry. It is deliberately conservative about what a probability means:

    - it reports the number, the baseline it must beat, and the calibration slope;
    - it refuses to convert a probability into a stance unless out-of-sample
      calibration has been validated, which is a Phase 7 concern;
    - when no champion exists it records absence, which is the normal state.
    """
    forecast = context.model_forecast
    if not forecast:
        return Finding(
            agent_code="quantitative",
            headline="No champion model is registered for this instrument",
            data_available=False,
            evidence=[
                Evidence(
                    kind=EvidenceKind.ABSENCE,
                    label="champion model",
                    detail=(
                        "No promoted model version covers this instrument. A candidate that has "
                        "not passed the out-of-sample gate is not used for any commentary."
                    ),
                )
            ],
            limitations=["Quantitative forecasts require a promoted model version."],
        )

    probability = float(forecast.get("probability") or 0.0)
    edge = forecast.get("edge_over_baseline")
    accuracy = forecast.get("out_of_sample_accuracy")
    baseline = forecast.get("baseline_accuracy")
    slope = forecast.get("calibration_slope")
    rows = forecast.get("out_of_sample_rows", 0)
    oos_auc = forecast.get("roc_auc")

    facts = [
        f"Up-probability for the next {forecast.get('horizon', 1)} bar: {probability:.3f}",
        f"Champion version v{forecast.get('version')} ({forecast.get('model_code')})",
        f"Out-of-sample rows {rows}",
    ]
    if accuracy is not None and baseline is not None:
        facts.append(
            f"Out-of-sample accuracy {float(accuracy):.3f} vs baseline {float(baseline):.3f}"
        )
    if edge is not None:
        facts.append(f"Edge over majority-class baseline {float(edge):+.3f}")
    if oos_auc is not None:
        facts.append(f"Out-of-sample ROC AUC {float(oos_auc):.3f}")
    if slope is not None:
        facts.append(f"Calibration slope {float(slope):.2f}")

    evidence = [
        Evidence(
            kind=EvidenceKind.COMPUTATION,
            label="up-probability",
            detail=f"logistic model {forecast.get('model_code')} v{forecast.get('version')}",
            value=probability,
            unit="probability",
            reference_id=str(forecast.get("version_id") or ""),
            observed_at=context.now,
        )
    ]
    if accuracy is not None:
        evidence.append(
            Evidence(
                kind=EvidenceKind.COMPUTATION,
                label="out-of-sample accuracy",
                detail="measured on a strictly forward time split, never a random one",
                value=float(accuracy),
                unit="ratio",
            )
        )

    uncertainties: list[str] = []
    # A negative edge is worse than no edge, so it must be called out as loudly as
    # a zero one. An earlier version tested |edge| < 0.02, which stayed silent on
    # a model that was actively worse than the baseline.
    if edge is None or float(edge) < 0.02:
        if edge is not None and float(edge) < 0:
            caveat = (
                f"The model is {abs(float(edge)):.3f} *worse* than the majority-class "
                "baseline out of sample; the probability carries no directional information."
            )
            label = "edge below baseline"
        else:
            caveat = (
                "The measured edge over the majority-class baseline is within noise; the "
                "probability is not reliable as a directional signal."
            )
            label = "edge within noise"
        uncertainties.append(caveat)
        evidence.append(
            Evidence(
                kind=EvidenceKind.COMPUTATION,
                label=label,
                detail="out-of-sample accuracy is not meaningfully above the baseline",
            )
        )
    if slope is not None and float(slope) < 0.5:
        uncertainties.append(
            f"Calibration slope {float(slope):.2f} indicates over-confidence: a 0.70 "
            "prediction does not mean a 70% chance."
        )
    if rows and int(rows) < 100:
        uncertainties.append(f"Only {rows} out-of-sample rows; the estimate is imprecise.")

    # A bare probability is misleading on its own. The caveat is decided by the
    # evaluation, not by whether the uncertainty list happens to be empty: a model
    # can clear the 0.02 edge floor and still be below the 0.52 AUC bar, and it
    # must still read as caveated.
    well_supported = bool(
        uncertainties == []
        and edge is not None
        and float(edge) >= 0.02
        and (oos_auc is None or float(oos_auc) >= 0.55)
    )

    return Finding(
        agent_code="quantitative",
        headline=(
            f"Champion model gives {probability:.0%} probability of an up move"
            if well_supported
            else f"Champion model gives {probability:.0%} up-probability, with caveats"
        ),
        stance=Stance.UNKNOWN,
        confidence=clamp(0.25 + max(0.0, float(edge or 0.0)) * 4),
        confidence_kind=ConfidenceKind.UNCALIBRATED,
        facts=facts,
        interpretation=[
            "This is a model output, not a recommendation. No stance is taken from it: "
            "converting a probability into a directional proposal needs a strategy layer "
            "with validated economics, which arrives in Phase 7."
        ],
        uncertainties=uncertainties,
        limitations=[
            "Trained on a single instrument's price history; it knows nothing about news, positioning or macro.",
            "A probability above 0.5 is not a signal unless the edge survives costs and slippage.",
        ],
        evidence=evidence,
        data_available=True,
    )


AGENTS = {
    "technical": technical_analyst,
    "regime": regime_analyst,
    "news": news_analyst,
    "portfolio": portfolio_analyst,
    "quality": data_quality_finding,
    "quantitative": quantitative_analyst,
}
