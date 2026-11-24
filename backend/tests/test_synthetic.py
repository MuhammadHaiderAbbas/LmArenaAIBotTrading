"""The offline provider: useful, deterministic, and impossible to mistake for a market.

A fresh install with no network used to report CRITICAL monitoring on day one. The fix is
a generated feed — which is exactly the kind of thing that quietly becomes "our data" if
nobody writes tests around it. So these tests defend the disclosure as hard as the maths.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from app.integrations.providers.synthetic import (
    SYNTHETIC_NOTICE,
    SyntheticProvider,
    _asset_class_from_symbol,
)
from app.models.market import Timeframe


@pytest.fixture
def provider() -> SyntheticProvider:
    return SyntheticProvider()


# ------------------------------------------------------------------ disclosure


def test_the_provider_announces_itself_as_synthetic(provider) -> None:
    assert provider.name == "synthetic"
    assert "Synthetic" in provider.display_name


def test_the_notice_says_it_is_not_market_data() -> None:
    assert "NOT market data" in SYNTHETIC_NOTICE
    assert "Do not use them to evaluate a strategy" in SYNTHETIC_NOTICE


def test_the_provider_is_not_in_the_default_routing_table() -> None:
    """It must be opted into. Silent selection would put generated bars in a real book."""
    from app.services.market.ingestion import OPT_IN_PROVIDERS

    assert "synthetic" not in OPT_IN_PROVIDERS  # nothing has registered it in this suite


# ----------------------------------------------------------------- determinism


def test_the_same_bars_come_back_twice(provider) -> None:
    first = provider.fetch_ohlc("BTC/USD", Timeframe.D1, limit=20)
    second = provider.fetch_ohlc("BTC/USD", Timeframe.D1, limit=20)
    assert [bar.close for bar in first] == [bar.close for bar in second]


def test_two_instruments_trace_different_paths(provider) -> None:
    btc = provider.fetch_ohlc("BTC/USD", Timeframe.D1, limit=30)
    eth = provider.fetch_ohlc("ETH/USD", Timeframe.D1, limit=30)
    assert [b.close for b in btc] != [b.close for b in eth]


def test_bars_are_ordered_and_ohcl_is_coherent(provider) -> None:
    bars = provider.fetch_ohlc("GBP/USD", Timeframe.H1, limit=48)
    assert len(bars) == 48
    for earlier, later in zip(bars, bars[1:], strict=False):
        assert earlier.open_time < later.open_time
    for bar in bars:
        assert bar.low <= bar.open <= bar.high
        assert bar.low <= bar.close <= bar.high
        assert bar.volume > 0


def test_the_most_recent_bar_is_closed_not_forming(provider) -> None:
    """Nothing downstream should receive a bar whose period has not ended."""
    bars = provider.fetch_ohlc("SPY", Timeframe.H1, limit=5)
    assert bars[-1].open_time + timedelta(hours=1) <= datetime.now(UTC)


def test_prices_stay_in_a_plausible_band(provider) -> None:
    """Not "realistic" — just not absurd, so indicators and risk maths behave."""
    for symbol, floor, ceiling in [
        ("BTC/USD", 10_000, 200_000),
        ("GBP/USD", 0.5, 3.0),
        ("AAPL", 20, 900),
    ]:
        for bar in provider.fetch_ohlc(symbol, Timeframe.D1, limit=60):
            assert floor < bar.close < ceiling, f"{symbol} produced {bar.close}"


def test_a_zero_limit_is_rejected(provider) -> None:
    from app.integrations.providers.base import ProviderError

    with pytest.raises(ProviderError):
        provider.fetch_ohlc("SPY", Timeframe.D1, limit=0)


def test_asset_classes_are_inferred_from_the_symbol() -> None:
    assert _asset_class_from_symbol("BTC/USD") == "crypto"
    assert _asset_class_from_symbol("GBP/USD") == "forex"
    assert _asset_class_from_symbol("AAPL") == "equity"


# --------------------------------------------------------------------- quotes


def test_a_quote_has_a_spread_around_the_last_price(provider) -> None:
    quote = provider.fetch_quote("EUR/USD")
    assert quote.bid is not None and quote.ask is not None
    assert quote.bid < quote.last < quote.ask


def test_health_never_raises(provider) -> None:
    healthy, message = provider.health()
    assert healthy is True
    assert message


# ----------------------------------------------------------------- the seed path


def test_seed_can_refuse_synthetic_data() -> None:
    """The default must be off: deploying generated bars silently would be indefensible."""
    import inspect

    from app import seed

    source = inspect.getsource(seed.main)
    assert 'AUREX_SEED_SYNTHETIC_DATA", "0"' in source


def test_the_seed_warns_before_writing_anything() -> None:
    import inspect

    from app import seed

    source = inspect.getsource(seed._seed_synthetic_market_data)
    assert "logger.warning(SYNTHETIC_NOTICE)" in source
    # and restores the real feeds afterwards, so the install is not left crippled
    assert 'mapping.is_enabled = True' in source
