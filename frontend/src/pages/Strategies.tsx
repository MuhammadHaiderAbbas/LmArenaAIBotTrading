import { useEffect, useMemo, useState } from "react";
import { Badge, Button, Panel, PageHeader, Stat, useToast } from "../components/ui";
import {
  DEFAULT_COSTS,
  STRATEGIES,
  runBacktest,
  type BacktestResult,
  type CostModel,
  type StrategyCode,
} from "../lib/demo/backtestEngine";
import { titleCase } from "../lib/format";

const SYMBOLS = ["GBP/USD", "EUR/USD", "SPY", "BTC/USD"];

function pct(value: number | null | undefined, digits = 2): string {
  if (value === null || value === undefined || Number.isNaN(value)) return "—";
  return `${(value * 100).toFixed(digits)}%`;
}

function signed(value: number | null | undefined, digits = 2): string {
  if (value === null || value === undefined || Number.isNaN(value)) return "—";
  return `${value >= 0 ? "+" : ""}${(value * 100).toFixed(digits)}%`;
}

function money(value: number): string {
  return value.toLocaleString("en-GB", { maximumFractionDigits: 0 });
}

/** Equity curve as an inline SVG — no chart library, and it renders in the preview. */
function EquityCurve({ result }: { result: BacktestResult }) {
  const points = result.equity;
  if (points.length < 2) return null;
  const width = 640;
  const height = 160;
  const values = points.map((point) => point.equity);
  const min = Math.min(...values);
  const max = Math.max(...values);
  const span = max - min || 1;
  const step = width / (points.length - 1);
  const line = points
    .map((point, index) => `${index === 0 ? "M" : "L"}${(index * step).toFixed(1)},${(height - ((point.equity - min) / span) * height).toFixed(1)}`)
    .join(" ");
  const startY = height - ((DEFAULT_COSTS.startingEquity - min) / span) * height;

  return (
    <svg viewBox={`0 0 ${width} ${height}`} className="h-40 w-full" role="img" aria-label="Equity curve">
      <line
        x1={0}
        x2={width}
        y1={startY}
        y2={startY}
        stroke="currentColor"
        strokeWidth={1}
        strokeDasharray="4 4"
        className="text-ink-faint"
      />
      <path d={line} fill="none" stroke="currentColor" strokeWidth={1.8} className="text-accent-soft" />
    </svg>
  );
}

export function Strategies() {
  const [code, setCode] = useState<StrategyCode>("trend_ma_v1");
  const [symbol, setSymbol] = useState(SYMBOLS[0]);
  const [costs, setCosts] = useState<CostModel>(DEFAULT_COSTS);
  const [result, setResult] = useState<BacktestResult | null>(null);
  const [baseline, setBaseline] = useState<BacktestResult | null>(null);
  const [running, setRunning] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const toast = useToast();

  const spec = useMemo(() => STRATEGIES.find((item) => item.code === code)!, [code]);

  const run = () => {
    setRunning(true);
    setError(null);
    // The backend does the same work server-side; in the static demo it runs here.
    try {
      const next = runBacktest(code, symbol, "1d", costs);
      setResult(next);
      setBaseline(runBacktest("buy_and_hold_v1", symbol, "1d", costs));
      toast({
        tone: next.status === "completed" ? "success" : "error",
        title: next.status === "completed" ? "Backtest complete" : "Backtest refused",
        description:
          next.status === "completed"
            ? `${next.trades.length} trades over ${next.barsUsed} bars, costs included.`
            : next.refusalReason,
      });
    } catch (cause) {
      setError((cause as Error).message);
    } finally {
      setRunning(false);
    }
  };

  useEffect(() => {
    run();
    // Intentionally only on mount: the user drives subsequent runs.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const metrics = result?.metrics ?? null;

  return (
    <>
      <PageHeader
        eyebrow="Research to deployment"
        title="Strategies"
        description="A strategy is only useful if it survives realistic costs. Every result here pays spread on both sides, commission and slippage per side, and is measured against buy-and-hold over the identical window."
        actions={<Badge tone="accent">Paper only · nothing trades</Badge>}
      />

      <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
        <Stat label="Trades" value={metrics?.trades ?? "—"} hint="Round trips in the sample" />
        <Stat
          label="Net return"
          value={metrics ? signed(metrics.netReturn) : "—"}
          hint="After every cost"
          tone={(metrics?.netReturn ?? 0) > 0 ? "positive" : "negative"}
        />
        <Stat
          label="Edge vs buy & hold"
          value={metrics ? signed(metrics.edgeOverBaseline) : "—"}
          hint="The only comparison that matters"
          tone={(metrics?.edgeOverBaseline ?? 0) > 0.02 ? "positive" : "negative"}
        />
        <Stat label="Cost drag" value={metrics ? pct(metrics.costDrag) : "—"} hint="Total costs as a share of equity" />
      </div>

      {error && (
        <div className="mt-4 rounded-lg border border-negative/30 bg-negative/10 px-3 py-2.5 text-xs text-negative">
          {error}
        </div>
      )}

      <div className="mt-4 grid gap-4 lg:grid-cols-3">
        <Panel title="Catalogue" subtitle={`${STRATEGIES.length} implemented strategies`} bodyClassName="p-0">
          <ul className="divide-y divide-line/70">
            {STRATEGIES.map((strategy) => (
              <li key={strategy.code}>
                <button
                  type="button"
                  onClick={() => setCode(strategy.code)}
                  aria-pressed={code === strategy.code}
                  className={
                    code === strategy.code
                      ? "w-full bg-accent/10 px-4 py-3 text-left ring-1 ring-inset ring-accent/25"
                      : "w-full px-4 py-3 text-left hover:bg-surface-hover/50"
                  }
                >
                  <div className="flex items-center justify-between gap-2">
                    <p className="text-xs font-semibold text-ink">{strategy.name}</p>
                    <Badge tone="muted">{titleCase(strategy.family)}</Badge>
                  </div>
                  <p className="mt-0.5 text-[10px] uppercase tracking-wider text-ink-faint">
                    {strategy.code}
                  </p>
                </button>
              </li>
            ))}
          </ul>
          <div className="border-t border-line px-4 py-3 text-[11px] leading-relaxed text-ink-faint">
            Only implemented families are listed. Nothing here is enabled for live use, and no
            backtest can enable it.
          </div>
        </Panel>

        <Panel
          className="lg:col-span-2"
          title={spec.name}
          subtitle={`${titleCase(spec.family)} · ${symbol} · 1 day bars`}
          actions={
            <div className="flex items-center gap-2">
              <select
                aria-label="Instrument"
                value={symbol}
                onChange={(event) => setSymbol(event.target.value)}
                className="h-8 rounded-lg border border-line-strong bg-surface-sunken px-2 text-xs text-ink"
              >
                {SYMBOLS.map((value) => (
                  <option key={value} value={value}>
                    {value}
                  </option>
                ))}
              </select>
              <Button onClick={run} disabled={running}>
                {running ? "Running…" : "Run backtest"}
              </Button>
            </div>
          }
        >
          <div className="space-y-4">
            <div>
              <p className="label mb-1">Hypothesis, stated before the result</p>
              <p className="rounded-md border border-line bg-surface-sunken px-3 py-2 text-xs leading-relaxed text-ink-muted">
                {spec.hypothesis}
              </p>
            </div>

            <div>
              <p className="label mb-2">Cost model — stored with every run</p>
              <div className="grid grid-cols-2 gap-2 sm:grid-cols-4">
                {(
                  [
                    ["Spread (bps)", "spreadBps"],
                    ["Commission (bps)", "commissionBps"],
                    ["Slippage (bps)", "slippageBps"],
                    ["Risk per trade", "riskPerTrade"],
                  ] as const
                ).map(([label, key]) => (
                  <label key={key} className="block">
                    <span className="mb-1 block text-[10px] uppercase tracking-wider text-ink-faint">{label}</span>
                    <input
                      type="number"
                      step={key === "riskPerTrade" ? 0.005 : 0.5}
                      min={key === "riskPerTrade" ? 0.001 : 0}
                      value={costs[key]}
                      onChange={(event) => setCosts({ ...costs, [key]: Number(event.target.value) })}
                      className="h-8 w-full rounded-lg border border-line-strong bg-surface-sunken px-2 text-xs text-ink"
                    />
                  </label>
                ))}
              </div>
            </div>

            {result?.status === "refused" && (
              <div className="rounded-lg border border-caution/30 bg-caution/[0.06] px-3 py-2.5 text-2xs leading-relaxed text-caution">
                <strong>Refused.</strong> {result.refusalReason}
              </div>
            )}

            {result?.status === "completed" && metrics && (
              <>
                <div
                  className={
                    result.passesGate
                      ? "rounded-lg border border-positive/25 bg-positive/[0.06] px-3 py-2.5 text-2xs leading-relaxed text-positive"
                      : "rounded-lg border border-negative/25 bg-negative/[0.06] px-3 py-2.5 text-2xs leading-relaxed text-negative"
                  }
                >
                  <strong>{result.passesGate ? "Gate passed." : "Gate not passed."}</strong>{" "}
                  {result.verdict}
                </div>

                <div>
                  <div className="mb-1 flex items-center justify-between">
                    <p className="label">Equity curve</p>
                    <p className="text-[10px] text-ink-faint">
                      dashed line = starting equity · {money(costs.startingEquity)}
                    </p>
                  </div>
                  <div className="rounded-lg border border-line bg-surface-sunken px-2 py-2">
                    <EquityCurve result={result} />
                  </div>
                </div>

                <div className="grid gap-4 sm:grid-cols-2">
                  <div>
                    <p className="label mb-2">Returns</p>
                    <table className="table-base">
                      <tbody>
                        {[
                          ["Gross (costs added back)", signed(metrics.grossReturn)],
                          ["Net after all costs", signed(metrics.netReturn)],
                          ["Buy and hold", signed(metrics.baselineReturn)],
                          ["Edge over buy and hold", signed(metrics.edgeOverBaseline)],
                          ["Total costs paid", money(metrics.totalCosts)],
                        ].map(([label, value]) => (
                          <tr key={label}>
                            <td className="text-xs text-ink-muted">{label}</td>
                            <td className="text-right text-xs font-medium text-ink">{value}</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                  <div>
                    <p className="label mb-2">Risk</p>
                    <table className="table-base">
                      <tbody>
                        {[
                          ["Sharpe", metrics.sharpe === null ? "—" : metrics.sharpe.toFixed(2)],
                          ["Sortino", metrics.sortino === null ? "—" : metrics.sortino.toFixed(2)],
                          ["Max drawdown", pct(metrics.maxDrawdown, 1)],
                          ["Win rate", pct(metrics.winRate, 1)],
                          ["Profit factor", metrics.profitFactor === null ? "—" : metrics.profitFactor.toFixed(2)],
                          ["Exposure", pct(metrics.exposure, 0)],
                        ].map(([label, value]) => (
                          <tr key={label}>
                            <td className="text-xs text-ink-muted">{label}</td>
                            <td className="text-right text-xs font-medium text-ink">{value}</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                </div>

                <div className="text-[11px] leading-relaxed text-ink-faint">
                  Costs are charged on both sides of every trade, and a resting stop is resolved
                  against each bar's low — so a stop that is hit mid-bar counts even when the close
                  recovers. Signals are filled at the next bar's open, never at the close that
                  produced them.
                </div>
              </>
            )}
          </div>
        </Panel>
      </div>

      <div className="mt-4 grid gap-4 lg:grid-cols-2">
        <Panel
          title="Strategy comparison"
          subtitle={`Same instrument, same costs — ${symbol}`}
          bodyClassName="p-0"
        >
          <table className="table-base">
            <thead>
              <tr>
                <th>Strategy</th>
                <th className="text-right">Trades</th>
                <th className="text-right">Net</th>
                <th className="text-right">vs B&amp;H</th>
                <th className="text-right">Gate</th>
              </tr>
            </thead>
            <tbody>
              {STRATEGIES.map((strategy) => {
                const outcome = runBacktest(strategy.code, symbol, "1d", costs);
                return (
                  <tr key={strategy.code}>
                    <td className="text-xs font-medium">{strategy.name}</td>
                    <td className="text-right text-xs text-ink-muted">{outcome.metrics?.trades ?? "—"}</td>
                    <td className="text-right text-xs text-ink">{signed(outcome.metrics?.netReturn)}</td>
                    <td
                      className={
                        (outcome.metrics?.edgeOverBaseline ?? 0) > 0.02
                          ? "text-right text-xs text-positive"
                          : "text-right text-xs text-negative"
                      }
                    >
                      {signed(outcome.metrics?.edgeOverBaseline)}
                    </td>
                    <td className="text-right">
                      <Badge tone={outcome.passesGate ? "positive" : "negative"}>
                        {outcome.passesGate ? "Pass" : "Fail"}
                      </Badge>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </Panel>

        <Panel title="What Phase 7 does not do">
          <ul className="space-y-2.5 text-xs text-ink-muted">
            {[
              ["No orders", "A backtest is a measurement. There is still no order path in this platform."],
              ["No validation without evidence", "Marking a strategy validated needs a passing backtest and staff rights; a run cannot validate itself."],
              ["No parameter search", "Optimising on this data would fit the noise and destroy the out-of-sample claim."],
              ["No live-data paper trading yet", "That is Phase 8, with the order lifecycle and simulated fills."],
            ].map(([title, detail]) => (
              <li key={title} className="rounded-md border border-line bg-surface-sunken px-3 py-2">
                <p className="font-medium text-ink">{title}</p>
                <p className="mt-0.5 text-[11px] leading-relaxed text-ink-faint">{detail}</p>
              </li>
            ))}
          </ul>
          {baseline && baseline.metrics && (
            <p className="mt-3 text-[11px] leading-relaxed text-ink-faint">
              Buy and hold on {symbol} returned {signed(baseline.metrics.netReturn)} over the same
              window. Any active strategy has to beat that, not zero.
            </p>
          )}
        </Panel>
      </div>
    </>
  );
}
