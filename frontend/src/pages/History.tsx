import { Badge, EmptyState, PageHeader, Panel, Stat } from "../components/ui";
import { IconHistory } from "../lib/icons";

export function History() {
  return (
    <>
      <PageHeader
        eyebrow="Decision provenance"
        title="Trade History"
        description="Orders, fills, fees, slippage and the AI research that motivated each decision. Every trade must be reproducible from recorded data, model versions and decisions."
        actions={<Badge tone="muted">Phase 8 activates execution</Badge>}
      />

      <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
        <Stat label="Orders" value={0} hint="No order router connected" />
        <Stat label="Fills" value={0} hint="Simulated fills from Phase 8" />
        <Stat label="Fees paid" value="—" hint="Costs are modelled explicitly" />
        <Stat label="Slippage" value="—" hint="Measured against arrival price" />
      </div>

      <div className="mt-4 grid gap-4 lg:grid-cols-3">
        <Panel className="lg:col-span-2" title="Orders" subtitle="Full lifecycle from proposal to terminal state" bodyClassName="p-0">
          <div className="p-5">
            <EmptyState
              title="No orders yet"
              description="The order lifecycle — proposed, risk-checked, submitted, partially filled, filled, cancelled, rejected — is implemented in Phase 8 against the paper broker adapter."
              icon={<IconHistory width={26} height={26} />}
            />
          </div>
        </Panel>

        <Panel title="What each record will contain">
          <ul className="space-y-2 text-xs text-ink-muted">
            {[
              "Idempotency key preventing duplicate submission",
              "Proposal id and the research findings behind it",
              "Risk check result with every input used",
              "Arrival, decision and fill timestamps in UTC",
              "Fees, spread and slippage versus benchmark",
              "Model and strategy version pinned to the decision",
              "Reconciliation status against the ledger",
            ].map((item) => (
              <li key={item} className="flex items-start gap-2">
                <span className="mt-1.5 h-1 w-1 shrink-0 rounded-full bg-accent" aria-hidden />
                <span className="leading-relaxed">{item}</span>
              </li>
            ))}
          </ul>
        </Panel>
      </div>

      <div className="mt-4">
        <Panel title="Execution realism tests" subtitle="Acceptance criteria before paper trading is trusted">
          <div className="grid gap-2 text-xs sm:grid-cols-2 lg:grid-cols-3">
            {[
              "Partial fills honoured",
              "Latency modelled between decision and fill",
              "Market closures respected per venue",
              "Spread and liquidity constraints enforced",
              "Rejected orders recorded with reasons",
              "Duplicate messages are idempotent",
              "Provider outage handling",
              "Restart recovery and reconciliation",
              "Fees applied to every fill",
            ].map((item) => (
              <div key={item} className="rounded-md border border-line bg-surface-sunken px-3 py-2 text-ink-muted">
                {item}
              </div>
            ))}
          </div>
        </Panel>
      </div>
    </>
  );
}
