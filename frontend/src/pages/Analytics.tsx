import { Badge, EmptyState, PageHeader, Panel, Stat } from "../components/ui";
import { IconChart } from "../lib/icons";

const metrics = [
  ["Net return after costs", "Return net of fees, spread, slippage and financing", "—"],
  ["Sharpe ratio", "Annualised excess return ÷ volatility", "—"],
  ["Sortino ratio", "Excess return ÷ downside deviation", "—"],
  ["Maximum drawdown", "Worst peak-to-trough decline", "—"],
  ["Win rate", "Profitable trades ÷ total trades", "—"],
  ["Profit factor", "Gross profit ÷ gross loss", "—"],
  ["Turnover", "Traded notional ÷ average equity", "—"],
  ["Exposure", "Average and peak portfolio exposure", "—"],
  ["Model calibration", "Forecast confidence versus realised frequency", "—"],
  ["Drift response", "Detection latency and action taken", "—"],
];

export function Analytics() {
  return (
    <>
      <PageHeader
        eyebrow="Evidence, not appearance"
        title="Analytics"
        description="Risk-adjusted performance, cost attribution and decision-quality metrics. Values stay blank until paper trading produces auditable results."
        actions={<Badge tone="muted">Phase 10 activates reporting</Badge>}
      />

      <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
        <Stat label="Net return" value="—" hint="After all costs" />
        <Stat label="Sharpe" value="—" hint="Target measured, not promised" />
        <Stat label="Max drawdown" value="—" hint="Peak-to-trough" />
        <Stat label="Profit factor" value="—" hint="Gross profit ÷ gross loss" />
      </div>

      <div className="mt-4 grid gap-4 lg:grid-cols-3">
        <Panel className="lg:col-span-2" title="Metric definitions" subtitle="Fixed now so results cannot be redefined after the fact" bodyClassName="p-0">
          <table className="table-base">
            <thead>
              <tr>
                <th>Metric</th>
                <th>Definition</th>
                <th className="text-right">Value</th>
              </tr>
            </thead>
            <tbody>
              {metrics.map(([metric, definition, value]) => (
                <tr key={metric}>
                  <td className="text-xs font-medium">{metric}</td>
                  <td className="text-xs text-ink-muted">{definition}</td>
                  <td className="cell-num text-right text-ink-faint">{value}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </Panel>

        <Panel title="Validation sequence" subtitle="Required before live consideration">
          <ol className="space-y-2 text-xs text-ink-muted">
            {[
              "Historical backtest with realistic costs, no look-ahead bias",
              "Walk-forward and time-separated out-of-sample testing",
              "Coverage across market regimes and instruments",
              "Live-data paper trading with no real-money execution",
              "Shadow comparison against independent benchmarks",
              "Long-duration reliability and incident testing",
              "Separately controlled live deployment only after review",
            ].map((step, index) => (
              <li key={step} className="flex gap-2.5">
                <span className="mt-0.5 flex h-4 w-4 shrink-0 items-center justify-center rounded-full border border-line-strong font-mono text-[9px] text-ink-faint">
                  {index + 1}
                </span>
                <span className="leading-relaxed">{step}</span>
              </li>
            ))}
          </ol>
        </Panel>
      </div>

      <div className="mt-4">
        <Panel title="Equity curve & attribution" subtitle="Populated once paper trading accumulates history">
          <EmptyState
            title="No performance history"
            description="AUREX AI deliberately shows no performance figures before they can be computed from recorded paper trades. Months of varied market conditions are a prudent minimum evidence target — and still not proof of future profitability."
            icon={<IconChart width={26} height={26} />}
          />
        </Panel>
      </div>
    </>
  );
}
