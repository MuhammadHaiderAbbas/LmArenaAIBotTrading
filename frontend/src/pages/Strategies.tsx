import { useState } from "react";
import { strategies } from "../lib/demoStatic";
import { Badge, EmptyState, Panel, PageHeader, Button } from "../components/ui";
import { titleCase } from "../lib/format";

export function Strategies() {
  const [selectedId, setSelectedId] = useState(strategies[0].id);
  const selected = strategies.find((s) => s.id === selectedId)!;

  return (
    <>
      <PageHeader
        eyebrow="Research to deployment"
        title="Strategies"
        description="A strategy only reaches deployment after backtesting, walk-forward validation and an out-of-sample review. Version history and rollback are mandatory."
        actions={<Badge tone="muted">Backtesting arrives in Phase 7</Badge>}
      />

      <div className="grid gap-4 lg:grid-cols-3">
        <Panel title="Catalogue" subtitle={`${strategies.length} candidate strategies`} bodyClassName="p-0">
          <ul className="divide-y divide-line/70">
            {strategies.map((strategy) => (
              <li key={strategy.id}>
                <button
                  type="button"
                  onClick={() => setSelectedId(strategy.id)}
                  className={
                    selectedId === strategy.id
                      ? "w-full bg-accent/10 px-4 py-3 text-left ring-1 ring-inset ring-accent/25"
                      : "w-full px-4 py-3 text-left hover:bg-surface-hover/50"
                  }
                  aria-pressed={selectedId === strategy.id}
                >
                  <div className="flex items-center justify-between gap-2">
                    <p className="text-xs font-semibold text-ink">{strategy.name}</p>
                    <Badge tone="caution">{titleCase(strategy.status)}</Badge>
                  </div>
                  <p className="mt-0.5 text-[10px] uppercase tracking-wider text-ink-faint">
                    {strategy.family} · Phase {strategy.phase}
                  </p>
                </button>
              </li>
            ))}
          </ul>
        </Panel>

        <Panel className="lg:col-span-2" title={selected.name} subtitle={`${selected.family} · ${selected.instruments.join(", ")}`} actions={<Badge tone="muted">Not validated</Badge>}>
          <div className="space-y-4">
            <div>
              <p className="label mb-1">Description</p>
              <p className="text-xs leading-relaxed text-ink-muted">{selected.description}</p>
            </div>
            <div>
              <p className="label mb-1">Hypothesis under test</p>
              <p className="rounded-md border border-line bg-surface-sunken px-3 py-2 text-xs leading-relaxed text-ink-muted">
                {selected.hypothesis}
              </p>
            </div>

            <div>
              <p className="label mb-2">Evaluation evidence</p>
              <table className="table-base">
                <thead>
                  <tr>
                    <th>Gate</th>
                    <th>Requirement</th>
                    <th className="text-right">Status</th>
                  </tr>
                </thead>
                <tbody>
                  {[
                    ["Backtest", "Realistic fees, spread and slippage", "Not run"],
                    ["Walk-forward", "Rolling out-of-sample windows", "Not run"],
                    ["Regime coverage", "Tested across volatility regimes", "Not run"],
                    ["Calibration", "Forecast confidence validated", "Not run"],
                    ["Paper trading", "Months of live-data simulation", "Not started"],
                  ].map(([gate, requirement, status]) => (
                    <tr key={gate}>
                      <td className="text-xs font-medium">{gate}</td>
                      <td className="text-xs text-ink-muted">{requirement}</td>
                      <td className="text-right">
                        <Badge tone="muted">{status}</Badge>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>

            <div className="flex flex-wrap gap-2">
              <Button disabled variant="secondary">
                Run backtest (Phase 7)
              </Button>
              <Button disabled variant="secondary">
                Compare versions
              </Button>
            </div>
            <p className="text-[11px] leading-relaxed text-ink-faint">
              Deployment requires explicit approval, immutable versioning and a rollback path. No
              model can rewrite production trading rules or promote itself.
            </p>
          </div>
        </Panel>
      </div>

      <div className="mt-4 grid gap-4 lg:grid-cols-2">
        <Panel title="Version history" subtitle="Immutable strategy versions with evaluation records">
          <EmptyState
            title="No versions registered"
            description="Model versioning and rollback live in the Models screen. Strategy versions arrive with the strategy engine in Phase 7."
          />
        </Panel>
        <Panel title="Model family usage" subtitle="Deploy only when out-of-sample value is proven">
          <table className="table-base">
            <thead>
              <tr>
                <th>Family</th>
                <th>Intended use</th>
                <th className="text-right">Deployed</th>
              </tr>
            </thead>
            <tbody>
              {[
                ["XGBoost / LightGBM", "Tabular features, baselines", "No"],
                ["Random Forest", "Robustness checks", "No"],
                ["LSTM", "Sequential research", "No"],
                ["Temporal Transformer", "Long-sequence experiments", "No"],
                ["Isolation Forest", "Anomaly detection", "No"],
                ["Hidden Markov", "Regime estimation", "No"],
                ["Reinforcement learning", "Isolated research only", "No"],
              ].map(([family, use, deployed]) => (
                <tr key={family}>
                  <td className="text-xs font-medium">{family}</td>
                  <td className="text-xs text-ink-muted">{use}</td>
                  <td className="text-right">
                    <Badge tone="muted">{deployed}</Badge>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </Panel>
      </div>
    </>
  );
}
