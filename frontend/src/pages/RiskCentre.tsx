import { useState } from "react";
import { Badge, Button, Dot, Panel, PageHeader, Stat, useToast } from "../components/ui";
import { riskLimits } from "../lib/demoStatic";
import { IconAlert, IconShield, IconStop } from "../lib/icons";

export function RiskCentre() {
  const [armedStop, setArmedStop] = useState(false);
  const [shutdown, setShutdown] = useState(false);
  const toast = useToast();

  return (
    <>
      <PageHeader
        eyebrow="Independent control"
        title="Risk Centre"
        description="Deterministic limits enforced immediately before order submission, outside the AI's control. The AI can propose; only this layer can approve."
        actions={
          <Badge tone={shutdown ? "negative" : "positive"}>
            <Dot tone={shutdown ? "negative" : "positive"} pulse />
            {shutdown ? "Trading halted" : "Trading permitted"}
          </Badge>
        }
      />

      <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
        <Stat label="Daily loss used" value="0.00%" hint="Limit 2.00%" tone="positive" />
        <Stat label="Drawdown" value="0.00%" hint="Limit 10.00%" tone="positive" />
        <Stat label="Portfolio exposure" value="0.00%" hint="Limit 50.00%" />
        <Stat label="Rejected proposals" value={0} hint="No proposals yet (Phase 5)" />
      </div>

      <div className="mt-4 grid gap-4 lg:grid-cols-3">
        <Panel className="lg:col-span-2" title="Enforced limits" subtitle="Engineering defaults for paper simulation — not investment advice" bodyClassName="p-0">
          <table className="table-base">
            <thead>
              <tr>
                <th>Control</th>
                <th>Paper setting</th>
                <th className="text-right">Enforcement</th>
              </tr>
            </thead>
            <tbody>
              {riskLimits.map((limit) => (
                <tr key={limit.label}>
                  <td className="text-xs font-medium">{limit.label}</td>
                  <td className="text-xs text-ink-muted">{limit.value}</td>
                  <td className="text-right">
                    <Badge tone="positive">Deterministic</Badge>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </Panel>

        <div className="space-y-4">
          <Panel title="Emergency shutdown" subtitle="Protected against accidental activation" actions={<IconShield className="text-negative" />}>
            <ol className="space-y-1.5 text-[11px] leading-relaxed text-ink-muted">
              <li>1. Reject all new orders and strategy submissions.</li>
              <li>2. Cancel eligible pending orders where safe.</li>
              <li>3. Freeze autonomous execution; agent processes cannot restart it.</li>
              <li>4. Reconcile open positions and outstanding orders.</li>
              <li>5. Notify the administrator and create an audit record.</li>
              <li>6. Require explicit authorised action to resume.</li>
            </ol>
            <p className="mt-3 rounded-md border border-line bg-surface-sunken px-3 py-2 text-[11px] leading-relaxed text-ink-faint">
              Stopping new risk is separate from closing existing exposure. Blind liquidation can
              itself create losses in illiquid or disrupted markets.
            </p>

            {!shutdown ? (
              <div className="mt-4 space-y-2">
                {!armedStop ? (
                  <Button variant="danger" className="w-full" onClick={() => setArmedStop(true)}>
                    <IconAlert /> Prepare emergency stop
                  </Button>
                ) : (
                  <>
                    <p className="text-xs text-negative">
                      This halts all autonomous execution in this environment. Confirm to proceed.
                    </p>
                    <div className="flex gap-2">
                      <Button
                        variant="danger"
                        className="flex-1"
                        onClick={() => {
                          setShutdown(true);
                          setArmedStop(false);
                          toast({
                            tone: "success",
                            title: "Emergency stop armed",
                            description: "No execution engine is active in Phase 1 — the control is wired for Phase 8.",
                          });
                        }}
                      >
                        <IconStop /> Confirm stop
                      </Button>
                      <Button variant="ghost" className="flex-1" onClick={() => setArmedStop(false)}>
                        Cancel
                      </Button>
                    </div>
                  </>
                )}
              </div>
            ) : (
              <div className="mt-4 space-y-2">
                <p className="text-xs text-negative">
                  Execution halted. Authorised resumption required.
                </p>
                <Button
                  variant="secondary"
                  className="w-full"
                  onClick={() => {
                    setShutdown(false);
                    toast({ tone: "info", title: "Execution resumed", description: "Logged to the audit trail." });
                  }}
                >
                  Resume execution
                </Button>
              </div>
            )}
          </Panel>

          <Panel title="Circuit breakers" subtitle="Automatic actions on threshold breach" bodyClassName="p-4">
            <ul className="space-y-2 text-xs">
              {[
                ["Stale market data", "Reject orders"],
                ["Excess spread / slippage", "Reject order"],
                ["Model drift beyond threshold", "Disable strategy"],
                ["Repeated rejections", "Pause strategy"],
              ].map(([trigger, action]) => (
                <li key={trigger} className="flex items-center justify-between gap-3">
                  <span className="text-ink-muted">{trigger}</span>
                  <span className="font-medium text-ink">{action}</span>
                </li>
              ))}
            </ul>
          </Panel>
        </div>
      </div>

      <div className="mt-4">
        <Panel title="Recent risk events" subtitle="Empty until orders flow through the risk controller">
          <p className="text-xs text-ink-muted">
            Every proposal, approval and rejection will be written to an auditable risk event with the
            exact inputs used, so decisions can be replayed.
          </p>
        </Panel>
      </div>
    </>
  );
}
