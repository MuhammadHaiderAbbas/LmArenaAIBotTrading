"""Entity extraction for linking documents to instruments and macro themes.

Conservative by design. A missed entity costs a link; a wrong entity puts a
citation behind the wrong instrument, which is far worse. Every rule here is a
closed vocabulary — currency codes, the registered instrument symbols, known
institutions — plus one careful pattern for monetary-policy metrics, which is the
only place Phase 4 needs numeric extraction.
"""

from __future__ import annotations

import re

from app.models.research import EntityKind

CURRENCY_CODES = {
    "USD",
    "EUR",
    "GBP",
    "JPY",
    "CHF",
    "AUD",
    "CAD",
    "NZD",
    "CNY",
    "SEK",
    "NOK",
}

INSTITUTIONS = {
    "ecb": "European Central Bank",
    "european central bank": "European Central Bank",
    "bank of england": "Bank of England",
    "boe": "Bank of England",
    "mpc": "Bank of England",
    "federal reserve": "Federal Reserve",
    "fed": "Federal Reserve",
    "fomc": "Federal Reserve",
    "sec": "US Securities and Exchange Commission",
    "fca": "Financial Conduct Authority",
    "pra": "Prudential Regulation Authority",
    "opec": "OPEC",
}

# Percentages that are almost always policy-relevant. Anything else numeric is
# left alone rather than guessed at.
METRIC_RE = re.compile(
    r"\b(bank rate|inflation|cpi|gdp|unemployment|interest rate|policy rate|"
    r"consumer prices?|retail sales|payrolls?)\b[^.%]{0,60}?(\d{1,2}(?:\.\d{1,2})?)\s?(%)",
    re.I,
)

TICKER_RE = re.compile(r"\b(?:[A-Z]{2,5}(?:\.[A-Z])?)\b")
PERIOD_RE = re.compile(r"\b(Q[1-4]\s?\d{4}|FY\s?\d{4}|H[12]\s?\d{4}|\d{4})\b")


def extract(text: str, title: str = "") -> list[tuple[EntityKind, str, float]]:
    """Return (kind, value, confidence) triples, de-duplicated."""
    haystack = f"{title}\n{text}"
    lowered = haystack.lower()
    found: dict[tuple[EntityKind, str], float] = {}

    for token in CURRENCY_CODES:
        # Require a currency context so "CAD" the software is not mistaken for
        # the Canadian dollar.
        if re.search(rf"\b{token}\b", haystack) and re.search(
            rf"(\b{token}/|{token}\b[^.]{{0,15}}\b(rate|pair|currency|dollar|sterling|euro|yen))",
            haystack,
            re.I,
        ):
            found[(EntityKind.CURRENCY, token)] = 0.8

    for alias, canonical in INSTITUTIONS.items():
        if re.search(rf"\b{re.escape(alias)}\b", lowered):
            found[(EntityKind.INSTITUTION, canonical)] = 0.9

    for match in METRIC_RE.finditer(haystack):
        value = f"{match.group(1).strip().title()} {match.group(2)}%"
        found[(EntityKind.METRIC, value)] = 0.8

    for match in PERIOD_RE.finditer(title or haystack[:200]):
        found[(EntityKind.PERIOD, match.group(1))] = 0.6

    # Registered symbols, matched exactly and only in upper case.
    for symbol in (
        "BTC/USD",
        "ETH/USD",
        "SOL/USD",
        "XRP/USD",
        "ADA/USD",
        "GBP/USD",
        "EUR/USD",
        "USD/JPY",
        "GBP/EUR",
        "AAPL",
        "MSFT",
        "NVDA",
        "TSLA",
        "SHEL",
        "HSBA",
        "SPY",
        "QQQ",
    ):
        if symbol in haystack:
            found[(EntityKind.INSTRUMENT, symbol)] = 0.95

    return [(kind, value, confidence) for (kind, value), confidence in found.items()]


def instrument_symbols(text: str) -> set[str]:
    return {value for kind, value, _ in extract(text) if kind is EntityKind.INSTRUMENT}
