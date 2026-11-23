import { useCallback, useEffect, useMemo, useState } from "react";
import { Area, AreaChart, ResponsiveContainer } from "recharts";
import { api, ApiError } from "../services/api";
import { CandlestickChart } from "../charts/CandlestickChart";
import { sma } from "../lib/indicators";
import type {
  CandleSeries,
  MarketRow,
  ProviderHealth,
  QualitySummary,
  Timeframe,
} from "../lib/marketTypes";
import { formatPercent, relativeTime } from "../lib/format";
import {
  Badge,
  Button,
  Dot,
  EmptyState,
  ErrorNote,
  Input,
  PageHeader,
  Panel,
  Spinner,
} from "../components/ui";
import { IconAlert, IconRefresh, IconSearch } from "../lib/icons";

const CLASSES = ["All", "Crypto", "Forex", "Equity", "ETF"] as const;
const TIMEFRAMES: Timeframe[] = ["1h", "4h", "1d", "1w"];
const ASSET_CLASS_LABELS: Record<string, (typeof CLASSES)[number]> = {
  crypto: "Crypto",
  forex: "Forex",
  equity: "Equity",
  etf: "ETF",
};

function priceDigits(value: number): number {
  if (value >= 1000) return 2;
  if (value >= 10) return 3;
  return 5;
}

function formatPrice(value: number): string {
  return value.toLocaleString("en-GB", {
    minimumFractionDigits: priceDigits(value),
    maximumFractionDigits: priceDigits(value),
  });
}

function sessionTone(row: MarketRow): "positive" | "muted" | "accent" {
  if (row.session.state === "always_on") return "accent";
  return row.session.is_open ? "positive" : "muted";
}

function sessionLabel(row: MarketRow): string {
  if (row.session.state === "always_on") return "24/7";
  return row.session.is_open ? "Open" : "Closed";
}

function qualityBadge(row: MarketRow) {
  switch (row.data_quality) {
    case "critical":
      return <Badge tone="negative">Critical</Badge>;
    case "warning":
      return <Badge tone="caution">Review</Badge>;
    case "no_data":
      return <Badge tone="muted">No data</Badge>;
    default:
      return <Badge tone="positive">Clean</Badge>;
  }
}

export function Markets() {
  const [assetClass, setAssetClass] = useState<(typeof CLASSES)[number]>("All");
  const [query, setQuery] = useState("");
  const [rows, setRows] = useState<MarketRow[] | null>(null);
  const [providers, setProviders] = useState<ProviderHealth[]>([]);
  const [quality, setQuality] = useState<QualitySummary | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [selected, setSelected] = useState<string | null>(null);
  const [timeframe, setTimeframe] = useState<Timeframe>("1d");
  const [series, setSeries] = useState<CandleSeries | null>(null);
  const [seriesLoading, setSeriesLoading] = useState(false);
  const [syncing, setSyncing] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);

  const load = useCallback(async () => {
    setError(null);
    try {
      const [overview, health, summary] = await Promise.all([
        api.marketOverview(),
        api.marketProviders(),
        api.marketQuality(),
      ]);
      setRows(overview);
      setProviders(health);
      setQuality(summary);
      setSelected((current) => current ?? overview[0]?.instrument.symbol ?? null);
    } catch (cause) {
      setError(cause instanceof ApiError ? cause.message : "Unable to load market data.");
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  useEffect(() => {
    if (!selected) return;
    let cancelled = false;
    setSeriesLoading(true);
    api
      .candles(selected, timeframe, 180)
      .then((result) => {
        if (!cancelled) setSeries(result);
      })
      .catch((cause: unknown) => {
        if (!cancelled) {
          setError(cause instanceof ApiError ? cause.message : "Unable to load candles.");
        }
      })
      .finally(() => {
        if (!cancelled) setSeriesLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [selected, timeframe]);

  const filtered = useMemo(() => {
    if (!rows) return [];
    const needle = query.trim().toLowerCase();
    return rows.filter((row) => {
      const matchesClass =
        assetClass === "All" || ASSET_CLASS_LABELS[row.instrument.asset_class] === assetClass;
      const matchesQuery =
        !needle ||
        row.instrument.symbol.toLowerCase().includes(needle) ||
        row.instrument.name.toLowerCase().includes(needle);
      return matchesClass && matchesQuery;
    });
  }, [rows, assetClass, query]);

  const overlays = useMemo(() => {
    if (!series) return [];
    const closes = series.candles.map((candle) => candle.close);
    return [
      { values: sma(closes, 20), colour: "#4C8DFF", label: "SMA 20" },
      { values: sma(closes, 50), colour: "#B084FF", label: "SMA 50", dashed: true },
    ];
  }, [series]);

  async function syncSelected() {
    if (!selected) return;
    setSyncing(true);
    setNotice(null);
    try {
      const result = await api.syncMarkets([selected], timeframe);
      const first = result.results[0];
      setNotice(
        first?.error
          ? `Sync failed: ${first.error}`
          : `Sync complete — ${result.succeeded} of ${result.results.length} instrument(s) refreshed.`,
      );
      await load();
    } catch (cause) {
      setNotice(cause instanceof ApiError ? cause.message : "Sync failed.");
    } finally {
      setSyncing(false);
    }
  }

  const selectedRow = rows?.find((row) => row.instrument.symbol === selected) ?? null;
  const indicators = series?.indicators ?? selectedRow?.indicators ?? null;

  return (
    <>
      <PageHeader
        eyebrow="Market intelligence"
        title="Markets"
        description="Provider-sourced candles with session awareness and data-quality monitoring. Instruments are registered for analysis; none are tradable in this phase."
        actions={
          <div className="flex items-center gap-2">
            <Badge tone="accent">Phase 3 — live data</Badge>
            <Button variant="ghost" onClick={() => void load()} aria-label="Refresh market data">
              <IconRefresh width={14} height={14} /> Refresh
            </Button>
          </div>
        }
      />

      {error && <ErrorNote message={error} onRetry={() => void load()} />}

      {/* Provider health */}
      <div className="mb-4 grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
        {providers.map((provider) => (
          <div key={provider.provider} className="panel px-4 py-3">
            <div className="flex items-center justify-between gap-2">
              <p className="text-xs font-semibold text-ink">{provider.display_name}</p>
              <span className="flex items-center gap-1.5">
                <Dot tone={provider.healthy ? "positive" : "caution"} pulse={provider.healthy} />
                <span className={provider.healthy ? "text-[10px] text-positive" : "text-[10px] text-caution"}>
                  {provider.healthy ? "Healthy" : "Degraded"}
                </span>
              </span>
            </div>
            <p className="mt-1 line-clamp-2 text-[10px] leading-relaxed text-ink-faint">
              {provider.message}
            </p>
            <p className="mt-1.5 text-[10px] text-ink-muted">
              {provider.instruments_covered} instruments · {provider.timeframes.join(" ")}
            </p>
          </div>
        ))}
      </div>

      {rows === null ? (
        <Panel>
          <Spinner label="Loading market data" />
        </Panel>
      ) : (
        <div className="grid gap-4 xl:grid-cols-[minmax(0,1.55fr)_minmax(0,1fr)]">
          {/* Screener */}
          <Panel
            title="Screener"
            subtitle={`${filtered.length} of ${rows.length} instruments`}
            bodyClassName="p-0"
            actions={
              quality ? (
                <Badge tone={quality.total_open > 0 ? "caution" : "positive"}>
                  {quality.total_open} open finding{quality.total_open === 1 ? "" : "s"}
                </Badge>
              ) : null
            }
          >
            <div className="flex flex-wrap items-center gap-2 border-b border-line px-4 py-3">
              <div className="flex flex-wrap gap-1">
                {CLASSES.map((item) => (
                  <button
                    key={item}
                    type="button"
                    onClick={() => setAssetClass(item)}
                    className={
                      assetClass === item
                        ? "rounded-md border border-accent/40 bg-accent/15 px-3 py-1.5 text-xs font-semibold text-accent-soft"
                        : "rounded-md border border-transparent px-3 py-1.5 text-xs font-medium text-ink-muted hover:bg-surface-raised hover:text-ink"
                    }
                    aria-pressed={assetClass === item}
                  >
                    {item}
                  </button>
                ))}
              </div>
              <div className="relative ml-auto w-full sm:w-56">
                <span className="pointer-events-none absolute left-2.5 top-2.5 text-ink-faint">
                  <IconSearch width={16} height={16} />
                </span>
                <Input
                  className="h-9 pl-8"
                  placeholder="Search instruments…"
                  value={query}
                  onChange={(event) => setQuery(event.target.value)}
                  aria-label="Search instruments"
                />
              </div>
            </div>

            {filtered.length === 0 ? (
              <div className="p-5">
                <EmptyState
                  title="No instruments match"
                  description="Adjust the asset-class filter or clear the search."
                />
              </div>
            ) : (
              <div className="max-h-[560px] overflow-auto">
                <table className="table-base">
                  <thead className="sticky top-0 z-10 bg-surface">
                    <tr>
                      <th>Instrument</th>
                      <th>Venue</th>
                      <th className="text-right">Last</th>
                      <th className="text-right">24h</th>
                      <th className="text-right">RSI</th>
                      <th>30d</th>
                      <th>Session</th>
                      <th>Quality</th>
                    </tr>
                  </thead>
                  <tbody>
                    {filtered.map((row) => {
                      const active = row.instrument.symbol === selected;
                      return (
                        <tr
                          key={row.instrument.symbol}
                          onClick={() => setSelected(row.instrument.symbol)}
                          className={active ? "cursor-pointer bg-accent/5" : "cursor-pointer"}
                        >
                          <td>
                            <p className="font-mono text-xs font-semibold text-ink">
                              {row.instrument.symbol}
                            </p>
                            <p className="text-[10px] text-ink-faint">{row.instrument.name}</p>
                          </td>
                          <td className="text-xs text-ink-muted">
                            <p>{row.instrument.venue.code}</p>
                            <p className="text-[10px] text-ink-faint">{row.session.local_time}</p>
                          </td>
                          <td className="cell-num text-right">
                            {row.quote ? formatPrice(row.quote.last) : "—"}
                          </td>
                          <td
                            className={
                              (row.quote?.change_24h_pct ?? 0) >= 0
                                ? "cell-num text-right text-positive"
                                : "cell-num text-right text-negative"
                            }
                          >
                            {row.quote?.change_24h_pct != null
                              ? formatPercent(row.quote.change_24h_pct)
                              : "—"}
                          </td>
                          <td className="cell-num text-right">
                            {row.indicators?.rsi_14 != null ? row.indicators.rsi_14.toFixed(1) : "—"}
                          </td>
                          <td className="w-24">
                            <div className="h-7">
                              {row.sparkline.length > 1 && (
                                <ResponsiveContainer width="100%" height="100%">
                                  <AreaChart
                                    data={row.sparkline.map((value, index) => ({ index, value }))}
                                  >
                                    <Area
                                      type="monotone"
                                      dataKey="value"
                                      stroke={(row.quote?.change_24h_pct ?? 0) >= 0 ? "#25D19A" : "#FF5F7E"}
                                      fill={
                                        (row.quote?.change_24h_pct ?? 0) >= 0
                                          ? "#25D19A22"
                                          : "#FF5F7E22"
                                      }
                                      strokeWidth={1.3}
                                      isAnimationActive={false}
                                    />
                                  </AreaChart>
                                </ResponsiveContainer>
                              )}
                            </div>
                          </td>
                          <td>
                            <Badge tone={sessionTone(row)}>{sessionLabel(row)}</Badge>
                          </td>
                          <td>{qualityBadge(row)}</td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>
            )}
          </Panel>

          {/* Detail */}
          <div className="grid gap-4">
            <Panel
              title={selectedRow ? selectedRow.instrument.symbol : "Select an instrument"}
              subtitle={selectedRow ? selectedRow.instrument.name : undefined}
              actions={
                <div className="flex items-center gap-1">
                  {TIMEFRAMES.map((item) => (
                    <button
                      key={item}
                      type="button"
                      onClick={() => setTimeframe(item)}
                      className={
                        timeframe === item
                          ? "rounded border border-accent/40 bg-accent/15 px-2 py-1 text-[10px] font-semibold uppercase text-accent-soft"
                          : "rounded border border-transparent px-2 py-1 text-[10px] font-medium uppercase text-ink-muted hover:bg-surface-raised hover:text-ink"
                      }
                      aria-pressed={timeframe === item}
                    >
                      {item}
                    </button>
                  ))}
                </div>
              }
              bodyClassName="p-4"
            >
              {seriesLoading && !series ? (
                <Spinner label="Loading candles" />
              ) : series ? (
                <>
                  <div className="mb-3 flex flex-wrap items-end justify-between gap-3">
                    <div>
                      <p className="font-mono text-2xl font-semibold text-white">
                        {formatPrice(series.candles[series.candles.length - 1]?.close ?? 0)}
                      </p>
                      <p
                        className={
                          (series.indicators.change_pct_1 ?? 0) >= 0
                            ? "text-xs text-positive"
                            : "text-xs text-negative"
                        }
                      >
                        {series.indicators.change_pct_1 != null
                          ? formatPercent(series.indicators.change_pct_1)
                          : "—"}{" "}
                        <span className="text-ink-faint">last bar</span>
                      </p>
                    </div>
                    <div className="flex flex-wrap items-center gap-1.5">
                      <Badge tone={series.session.state === "always_on" ? "accent" : series.session.is_open ? "positive" : "muted"}>
                        {series.session.venue} · {series.session.state.replace("_", " ")}
                      </Badge>
                      <Badge
                        tone={!series.session.is_open ? "muted" : series.is_stale ? "caution" : "positive"}
                      >
                        {!series.session.is_open
                          ? "Last print"
                          : series.is_stale
                            ? "Stale"
                            : "Fresh"}
                      </Badge>
                      <Badge tone="muted">{series.candles.length} bars</Badge>
                    </div>
                  </div>

                  <CandlestickChart
                    candles={series.candles}
                    overlays={overlays}
                    symbol={series.instrument.symbol}
                  />

                  <div className="mt-2 flex flex-wrap items-center gap-3 text-[10px] text-ink-faint">
                    <span className="flex items-center gap-1.5">
                      <span className="inline-block h-0.5 w-4 bg-[#4C8DFF]" /> SMA 20
                    </span>
                    <span className="flex items-center gap-1.5">
                      <span className="inline-block h-0.5 w-4 bg-[#B084FF]" /> SMA 50
                    </span>
                    <span className="ml-auto">
                      Source: {series.candles[series.candles.length - 1]?.provider} · updated{" "}
                      {relativeTime(series.candles[series.candles.length - 1]?.close_time ?? "")}
                    </span>
                  </div>
                </>
              ) : (
                <EmptyState title="No series loaded" description="Pick an instrument from the screener." />
              )}
            </Panel>

            <Panel
              title="Indicator readout"
              subtitle={selectedRow ? `${selectedRow.instrument.symbol} · ${timeframe.toUpperCase()}` : undefined}
              actions={
                <Button variant="ghost" onClick={() => void syncSelected()} disabled={syncing || !selected}>
                  <IconRefresh width={14} height={14} />
                  {syncing ? "Syncing…" : "Sync"}
                </Button>
              }
            >
              {notice && (
                <p className="mb-3 rounded-md border border-line bg-surface-sunken px-3 py-2 text-[11px] text-ink-muted">
                  {notice}
                </p>
              )}
              {indicators ? (
                <div className="grid grid-cols-2 gap-x-4 gap-y-3 text-xs sm:grid-cols-3">
                  {[
                    ["RSI (14)", indicators.rsi_14, (value: number) => value.toFixed(2)],
                    ["ATR (14)", indicators.atr_14, formatPrice],
                    ["SMA 20", indicators.sma_20, formatPrice],
                    ["SMA 50", indicators.sma_50, formatPrice],
                    ["EMA 20", indicators.ema_20, formatPrice],
                    ["MACD", indicators.macd, (value: number) => value.toFixed(4)],
                    ["Signal", indicators.macd_signal, (value: number) => value.toFixed(4)],
                    ["Histogram", indicators.macd_histogram, (value: number) => value.toFixed(4)],
                    ["BB upper", indicators.bb_upper, formatPrice],
                    ["BB lower", indicators.bb_lower, formatPrice],
                    ["Volatility (20)", indicators.volatility_20, (value: number) => `${value.toFixed(1)}%`],
                    ["Change 20 bars", indicators.change_pct_20, (value: number) => formatPercent(value)],
                  ].map(([label, value, format]) => (
                    <div key={label as string} className="rounded-md border border-line bg-surface-sunken px-3 py-2">
                      <p className="text-[10px] uppercase tracking-wide text-ink-faint">{label as string}</p>
                      <p className="mt-0.5 font-mono text-sm text-ink">
                        {value == null ? "—" : (format as (input: number) => string)(value as number)}
                      </p>
                    </div>
                  ))}
                </div>
              ) : (
                <Spinner label="Computing indicators" />
              )}

              <div className="mt-4 rounded-md border border-caution/25 bg-caution/5 px-3 py-2.5">
                <p className="flex items-center gap-1.5 text-[11px] font-semibold text-caution">
                  <IconAlert width={13} height={13} /> Warm-up discipline
                </p>
                <p className="mt-1 text-[11px] leading-relaxed text-ink-muted">
                  Indicators report nothing until their window is complete. A 50-period average
                  computed from 20 bars is not a rough estimate — it is a wrong number, and nothing
                  downstream can tell the difference.
                </p>
              </div>
            </Panel>

            {quality && (
              <Panel title="Data quality" subtitle="Open findings across the registry">
                <div className="grid grid-cols-3 gap-2">
                  {(["info", "warning", "critical"] as const).map((severity) => (
                    <div key={severity} className="rounded-md border border-line bg-surface-sunken px-3 py-2">
                      <p className="text-[10px] uppercase tracking-wide text-ink-faint">{severity}</p>
                      <p className="font-mono text-sm text-ink">{quality.by_severity[severity] ?? 0}</p>
                    </div>
                  ))}
                </div>
                {quality.stale_instruments.length > 0 && (
                  <p className="mt-3 text-[11px] leading-relaxed text-ink-muted">
                    Awaiting a fresh print (venue closed):{" "}
                    <span className="font-mono text-ink">{quality.stale_instruments.join(", ")}</span>
                  </p>
                )}
                {quality.stale_instruments.length === 0 && (
                  <p className="mt-3 text-[11px] text-ink-muted">
                    Every instrument is inside its freshness budget.
                  </p>
                )}
              </Panel>
            )}
          </div>
        </div>
      )}
    </>
  );
}
