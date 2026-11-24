import { useCallback, useEffect, useMemo, useState } from "react";
import { Badge, EmptyState, PageHeader, Panel, Stat } from "../components/ui";
import { api } from "../services/api";
import type { AnalyticMetric, CurvePoint, PerformanceReport } from "../lib/analyticsTypes";

function money(value: number | null | undefined): string {
  if (value === null || value === undefined) return "—";
  return `${value < 0 ? "-" : ""}${Math.abs(value).toLocaleString("en-GB", {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  })}`;
}

/** A metric's displayed value — or an honest dash, never a fabricated zero. */
function display(metric: AnalyticMetric | undefined): string {
  if (!metric || metric.value === null) return "—";
  if (metric.unit === "pct") return `${(metric.value * 100).toFixed(2)}%`;
  return metric.value.toFixed(2);
}

function tone(metric: AnalyticMetric | undefined): "positive" | "negative" | "neutral" {
  if (!metric || metric.value === null) return "neutral";
  if (metric.key === "max_drawdown") return metric.value > 0.1 ? "negative" : "neutral";
  return metric.value >= 0 ? "positive" : "negative";
}

/**
 * Equity against benchmark.
 *
 * Drawn as two paths on one scale, with the y-range derived from the data rather than
 * forced to zero: an account moving between 100k and 101k is flat, and a chart that
 * draws that as a mountain is a chart that lies.
 */
function EquityChart({ equity, benchmark }: { equity: CurvePoint[]; benchmark: CurvePoint[] }) {
  const width = 640;
  const height = 200;

  const { paths, min, max } = useMemo(() => {
    const values = [...equity.map((point) => point.equity), ...benchmark.map((point) => point.equity)];
    if (values.length < 2) return { paths: null, min: 0, max: 0 };
    const low = Math.min(...values);
    const high = Math.max(...values);
    const pad = (high - low) * 0.1 || 1;
    const floor = low - pad;
    const ceiling = high + pad;

    const toPath = (points: CurvePoint[]) => {
      if (points.length < 2) return "";
      return points
        .map((point, index) => {
          const x = (index / (points.length - 1)) * width;
          const y = height - ((point.equity - floor) / (ceiling - floor)) * height;
          return `${index === 0 ? "M" : "L"}${x.toFixed(1)},${y.toFixed(1)}`;
        })
        .join(" ");
    };

    return {
      paths: { equity: toPath(equity), benchmark: toPath(benchmark) },
      min: floor,
      max: ceiling,
    };
  }, [equity, benchmark]);

  if (!paths) {
    return (
      <p className="px-1 py-8 text-center text-xs text-ink-faint">
        Not enough history to plot a curve yet.
      </p>
    );
  }

  return (
    <div>
      <svg
        viewBox={`0 0 ${width} ${height}`}
        className="h-44 w-full"
        role="img"
        aria-label="Equity against benchmark"
        preserveAspectRatio="none"
      >
        <path d={paths.benchmark} fill="none" stroke="currentColor" strokeWidth={1.5} strokeDasharray="5 4" className="text-ink-faint" />
        <path d={paths.equity} fill="none" stroke="currentColor" strokeWidth={2} className="text-accent-soft" />
      </svg>
      <div className="mt-1 flex flex-wrap items-center justify-between gap-2 text-[10px] text-ink-faint">
        <span className="flex items-center gap-3">
          <span className="flex items-center gap-1">
            <span className="inline-block h-0.5 w-4 bg-accent-soft" /> Strategy
          </span>
          <span className="flex items-center gap-1">
            <span className="inline-block h-0.5 w-4 border-t border-dashed border-ink-faint" /> Buy and hold
          </span>
        </span>
        <span className="font-mono">
          {money(min)} – {money(max)}
        </span>
      </div>
    </div>
  );
}

export function Analytics() {
  const [report, setReport] = useState<PerformanceReport | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      setReport(await api.analyticsPerformance());
      setError(null);
    } catch (cause) {
      setError((cause as Error).message);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const byKey = useMemo(
    () => Object.fromEntries((report?.metrics ?? []).map((metric) => [metric.key, metric])),
    [report],
  );

  return (
    <>
      <PageHeader
        eyebrow="Evidence, not appearance"
        title="Analytics"
        description="Risk-adjusted performance, cost attribution and decision quality, computed from recorded fills. A metric the sample cannot support is withheld with a reason — never shown as zero, which reads like a result."
        actions={<Badge tone="muted">Simulated results</Badge>}
      />

      {error && (
        <div className="mt-4 rounded-lg border border-negative/30 bg-negative/10 px-3 py-2.5 text-xs text-negative">
          {error}
        </div>
      )}

      <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
        <Stat
          label="Net return"
          value={display(byKey.net_return)}
          hint="After every cost"
          tone={tone(byKey.net_return)}
        />
        <Stat label="Sharpe" value={display(byKey.sharpe)} hint={byKey.sharpe?.note || "Annualised"} tone="neutral" />
        <Stat label="Max drawdown" value={display(byKey.max_drawdown)} hint="Peak to trough" tone={tone(byKey.max_drawdown)} />
        <Stat label="Profit factor" value={display(byKey.profit_factor)} hint="Gross profit ÷ gross loss" tone="neutral" />
      </div>

      <div className="mt-4 grid gap-4 lg:grid-cols-3">
        <Panel
          className="lg:col-span-2"
          title="Equity against buy-and-hold"
          subtitle={
            report
              ? `Benchmark ${report.benchmark_return === null ? "—" : `${(report.benchmark_return * 100).toFixed(2)}%`} over the same window`
              : "Loading"
          }
        >
          {report ? (
            <EquityChart equity={report.equity_curve} benchmark={report.benchmark_curve} />
          ) : (
            <p className="py-8 text-center text-xs text-ink-faint">Loading…</p>
          )}
        </Panel>

        <Panel title="Costs" subtitle="Paid, not estimated">
          {report ? (
            <dl className="space-y-2">
              {[
                ["Commission", report.costs.commission],
                ["Slippage", report.costs.slippage],
                ["Total", report.costs.total],
              ].map(([label, value]) => (
                <div key={String(label)} className="flex items-baseline justify-between border-b border-line pb-1.5 last:border-0">
                  <dt className={label === "Total" ? "text-xs font-medium text-ink" : "text-xs text-ink-muted"}>{label}</dt>
                  <dd className={`font-mono text-xs ${label === "Total" ? "font-semibold text-ink" : "text-ink-muted"}`}>
                    {money(value as number)}
                  </dd>
                </div>
              ))}
              <div className="flex items-baseline justify-between pt-1 text-[10px] text-ink-faint">
                <dt>
                  {report.costs.orders} orders · {report.costs.fills} fills
                </dt>
                <dd>{report.costs.rejected_orders} rejected</dd>
              </div>
            </dl>
          ) : (
            <p className="text-xs text-ink-faint">Loading…</p>
          )}
        </Panel>
      </div>

      <div className="mt-4 grid gap-4 lg:grid-cols-2">
        <Panel title="Every metric, with its definition" subtitle="Fixed now so results cannot be redefined later" scrollBody>
          <table className="table-base">
            <thead>
              <tr>
                <th>Metric</th>
                <th className="text-right">Value</th>
                <th>Definition</th>
              </tr>
            </thead>
            <tbody>
              {(report?.metrics ?? []).map((metric) => (
                <tr key={metric.key}>
                  <td className="text-xs font-medium">{metric.label}</td>
                  <td className="text-right font-mono text-xs">
                    {metric.value === null ? (
                      <span className="text-ink-faint" title={metric.note || "Not enough data"}>
                        —
                      </span>
                    ) : (
                      <span className={tone(metric) === "negative" ? "text-negative" : "text-ink"}>{display(metric)}</span>
                    )}
                  </td>
                  <td className="text-[10px] leading-relaxed text-ink-faint">
                    {metric.definition}
                    {metric.note && <span className="ml-1 text-caution">({metric.note})</span>}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </Panel>

        <Panel title="Why a number is missing" subtitle="The report's own caveats">
          <ul className="space-y-2">
            {(report?.notes ?? []).length === 0 ? (
              <li className="text-xs text-ink-faint">No caveats — every metric in this report is supported by the sample.</li>
            ) : (
              (report?.notes ?? []).map((note) => (
                <li key={note} className="rounded-md border border-line bg-surface-sunken px-3 py-2 text-[11px] leading-relaxed text-ink-muted">
                  {note}
                </li>
              ))
            )}
          </ul>
          <p className="mt-3 text-[10px] leading-relaxed text-ink-faint">
            Historical and simulated results do not guarantee live outcomes. Nothing on this page is a
            forecast, and the API sets <code>forward_looking: false</code> on every report.
          </p>
        </Panel>
      </div>

      <div className="mt-4 grid gap-4 lg:grid-cols-2">
        <Panel title="Attribution" subtitle="Where the money came from" >
          {report && Object.keys(report.attribution.by_symbol).length > 0 ? (
            <div className="space-y-3">
              {[
                ["By instrument", report.attribution.by_symbol],
                ["By origin", report.attribution.by_strategy],
              ].map(([heading, values]) => (
                <div key={String(heading)}>
                  <p className="label mb-1.5">{heading as string}</p>
                  <ul className="space-y-1">
                    {Object.entries(values as Record<string, number>).map(([key, value]) => (
                      <li key={key} className="flex items-baseline justify-between text-xs">
                        <span className="text-ink-muted">{key}</span>
                        <span className={`font-mono ${value >= 0 ? "text-positive" : "text-negative"}`}>
                          {value >= 0 ? "+" : ""}
                          {money(value)}
                        </span>
                      </li>
                    ))}
                  </ul>
                </div>
              ))}
            </div>
          ) : (
            <EmptyState title="Nothing to attribute yet" description="Closed round trips are attributed by instrument and by the origin that opened them." />
          )}
        </Panel>

        <Panel title="Closed trades" subtitle="Net of commission, FIFO matched" scrollBody>
          {(report?.trades ?? []).length === 0 ? (
            <div className="p-1">
              <EmptyState
                title="No closed trades"
                description="A position is realised when it is sold. Until then it contributes to equity but not to trade statistics."
              />
            </div>
          ) : (
            <table className="table-base">
              <thead>
                <tr>
                  <th>Instrument</th>
                  <th className="text-right">Qty</th>
                  <th className="text-right">Entry</th>
                  <th className="text-right">Exit</th>
                  <th className="text-right">P&amp;L</th>
                  <th className="text-right">Return</th>
                  <th>Origin</th>
                </tr>
              </thead>
              <tbody>
                {(report?.trades ?? []).map((trade, index) => (
                  <tr key={`${trade.symbol}-${trade.closed_at}-${index}`}>
                    <td className="text-xs font-medium">{trade.symbol}</td>
                    <td className="text-right text-xs text-ink-muted">{trade.quantity}</td>
                    <td className="text-right text-xs text-ink-muted">{trade.entry_price.toFixed(4)}</td>
                    <td className="text-right text-xs text-ink-muted">{trade.exit_price.toFixed(4)}</td>
                    <td className={`text-right text-xs ${trade.realised_pnl >= 0 ? "text-positive" : "text-negative"}`}>
                      {money(trade.realised_pnl)}
                    </td>
                    <td className="text-right text-xs text-ink-muted">{(trade.return_pct * 100).toFixed(2)}%</td>
                    <td className="text-[10px] text-ink-faint">{trade.strategy_code ?? "manual"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </Panel>
      </div>

      <Panel className="mt-4" title="What this page refuses to do">
        <ul className="grid gap-2.5 sm:grid-cols-2">
          {[
            ["No zero for missing data", "A Sharpe ratio over three observations is not 0.00 — it is undefined, and the cell is blank with the reason attached."],
            ["No 'infinite' profit factor", "An unbeaten strategy has no meaningful profit factor; the number is withheld rather than flattered."],
            ["No index benchmark", "Buy-and-hold of the instruments actually traded is the only comparison that answers 'was the selection worth it?'."],
            ["No forecast", "Every figure is retrospective. The API sets forward_looking: false and the page says so."],
            ["No gross/net mixing", "Headline return is net of costs and the trade list is net too, so a winner cannot be counted twice."],
            ["No smoothing", "Equity is marked daily from fills; a day the book was held at a loss shows as that loss."],
          ].map(([title, detail]) => (
            <li key={title} className="rounded-md border border-line bg-surface-sunken px-3 py-2">
              <p className="text-[11px] font-medium text-ink">{title}</p>
              <p className="mt-0.5 text-[10px] leading-relaxed text-ink-faint">{detail}</p>
            </li>
          ))}
        </ul>
      </Panel>
    </>
  );
}

