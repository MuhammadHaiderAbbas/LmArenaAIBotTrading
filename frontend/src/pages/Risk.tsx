import { useCallback, useEffect, useState } from "react";
import { Badge, Button, Dot, EmptyState, Panel, PageHeader, Stat, useToast } from "../components/ui";
import { api } from "../services/api";
import type { RiskCheckRecord, RiskOverview, RiskStatus } from "../lib/riskTypes";
import { titleCase } from "../lib/format";

function pct(value: number | null | undefined, digits = 2): string {
  if (value === null || value === undefined) return "—";
  return `${(value * 100).toFixed(digits)}%`;
}

const DECISION_TONE: Record<string, "positive" | "negative" | "accent" | "muted"> = {
  allow: "positive",
  allow_reduced: "accent",
  reject: "negative",
};

const RULE_LABEL: Record<string, string> = {
  per_trade_risk: "Per-trade risk",
  daily_loss: "Daily loss",
  max_drawdown: "Drawdown",
  max_positions: "Position count",
  max_exposure: "Portfolio exposure",
  leverage: "Leverage",
  data_freshness: "Data freshness",
  spread: "Spread",
  concentration: "Concentration",
  correlation: "Correlation",
  kill_switch: "Kill switch",
  order_rate: "Order rate",
};

export function Risk() {
  const [status, setStatus] = useState<RiskStatus | null>(null);
  const [overview, setOverview] = useState<RiskOverview | null>(null);
  const [checks, setChecks] = useState<RiskCheckRecord[]>([]);
  const [busy, setBusy] = useState(false);
  const [reason, setReason] = useState("");
  const [error, setError] = useState<string | null>(null);
  const toast = useToast();

  const refresh = useCallback(async () => {
    try {
      const [nextStatus, nextOverview, nextChecks] = await Promise.all([
        api.riskStatus(),
        api.riskOverview(),
        api.riskChecks(15),
      ]);
      setStatus(nextStatus);
      setOverview(nextOverview);
      setChecks(nextChecks);
      setError(null);
    } catch (cause) {
      setError((cause as Error).message);
    }
  }, []);

  useEffect(() => {
    void refresh();
    const timer = setInterval(() => void refresh(), 15_000);
    return () => clearInterval(timer);
  }, [refresh]);

  const trip = async () => {
    setBusy(true);
    try {
      await api.riskEmergencyStop(reason || "Emergency stop requested from the Risk Centre.");
      toast({
        tone: "error",
        title: "Emergency stop engaged",
        description: "New orders are refused. Open positions were not liquidated.",
      });
      setReason("");
      await refresh();
    } catch (cause) {
      toast({ tone: "error", title: "Stop failed", description: (cause as Error).message });
    } finally {
      setBusy(false);
    }
  };

  const resume = async () => {
    setBusy(true);
    try {
      await api.riskResume("Resumed from the Risk Centre.");
      toast({ tone: "success", title: "Trading resumed", description: "The halt has been cleared and logged." });
      await refresh();
    } catch (cause) {
      toast({ tone: "error", title: "Resume failed", description: (cause as Error).message });
    } finally {
      setBusy(false);
    }
  };

  const policy = status?.policy;
  const halted = status?.halted_new_risk ?? false;

  return (
    <>
      <PageHeader
        eyebrow="Independent risk controller"
        title="Risk Centre"
        description="Deterministic limits evaluated immediately before an order can exist. The controller reads the ledger and the position book, never an agent's own account of itself, and it can refuse or shrink an order. It cannot be talked around."
        actions={
          <Badge tone={halted ? "negative" : "positive"}>
            <Dot tone={halted ? "negative" : "positive"} pulse={halted} />
            {halted ? "New risk halted" : "Trading permitted"}
          </Badge>
        }
      />

      {error && (
        <div className="mt-4 rounded-lg border border-negative/30 bg-negative/10 px-3 py-2.5 text-xs text-negative">
          {error}
        </div>
      )}

      <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
        <Stat label="Equity" value={status ? `${status.equity.toLocaleString("en-GB", { maximumFractionDigits: 2 })} GBP` : "—"} hint="Cash plus marked positions" tone="accent" />
        <Stat
          label="Exposure"
          value={pct(status?.exposure)}
          hint={policy ? `Limit ${pct(policy.max_exposure)}` : "—"}
          tone={(status?.exposure ?? 0) > (policy?.max_exposure ?? 1) * 0.8 ? "negative" : "neutral"}
        />
        <Stat
          label="Drawdown"
          value={pct(status?.drawdown)}
          hint={policy ? `Limit ${pct(policy.max_drawdown)}` : "—"}
          tone={(status?.drawdown ?? 0) > (policy?.max_drawdown ?? 1) * 0.5 ? "negative" : "neutral"}
        />
        <Stat
          label="Rejected"
          value={overview?.rejected_total ?? 0}
          hint={overview ? `${pct(overview.rejection_rate, 0)} of ${overview.checks_total} checks` : "—"}
          tone={(overview?.rejected_total ?? 0) > 0 ? "negative" : "neutral"}
        />
      </div>

      <div className="mt-4 grid gap-4 lg:grid-cols-3">
        <Panel
          className="lg:col-span-2"
          title="Limits in force"
          subtitle={policy ? `Policy v${policy.version} · blueprint paper defaults` : "Loading"}
        >
          <div className="grid gap-2 sm:grid-cols-2">
            {policy &&
              [
                ["Max risk per trade", pct(policy.max_risk_per_trade), "A stop-out costs at most this share of equity"],
                ["Max daily loss", pct(policy.max_daily_loss), "Realised loss halts new risk for the day"],
                ["Max drawdown", pct(policy.max_drawdown), "Distance from peak equity"],
                ["Max positions", String(policy.max_positions), "Distinct open instruments"],
                ["Max exposure", pct(policy.max_exposure), "Invested share of equity"],
                ["Concentration", pct(policy.max_position_concentration), "Single-instrument share of equity"],
                ["Correlation", policy.max_correlation.toFixed(2), "Against the held book"],
                ["Order rate", `${policy.max_orders_per_hour}/h`, "Runaway-loop guard"],
              ].map(([label, value, hint]) => (
                <div key={label} className="rounded-md border border-line bg-surface-sunken px-3 py-2">
                  <div className="flex items-baseline justify-between gap-2">
                    <span className="text-[11px] font-medium text-ink">{label}</span>
                    <span className="font-mono text-xs text-accent-soft">{value}</span>
                  </div>
                  <p className="mt-0.5 text-[10px] leading-relaxed text-ink-faint">{hint}</p>
                </div>
              ))}
          </div>
          <p className="mt-3 text-[10px] leading-relaxed text-ink-faint">
            Leverage is {policy?.leverage_enabled ? "enabled" : "disabled"}. These are illustrative
            paper-mode defaults for simulation — engineering starting points, not investment advice.
          </p>
        </Panel>

        <Panel title="Emergency stop" subtitle="Halts new risk; never liquidates">
          <div className="space-y-3">
            <p className="text-[11px] leading-relaxed text-ink-muted">
              The stop refuses all new orders and cancels pending ones. It deliberately does{" "}
              <strong className="text-ink">not</strong> close open positions: forced liquidation in a
              disrupted market can create the loss it is meant to prevent. Clearing it is an explicit,
              attributed action.
            </p>
            {halted ? (
              <>
                <div className="rounded-lg border border-negative/30 bg-negative/10 px-3 py-2.5 text-[11px] leading-relaxed text-negative">
                  <strong>{titleCase(status?.breaker_state ?? "open")}</strong>
                  {status?.breaker_reason ? ` — ${status.breaker_reason}` : ""}
                  <br />
                  {status?.positions_affected ?? 0} position(s) remain open and are not being liquidated.
                </div>
                <Button onClick={resume} disabled={busy} className="w-full">
                  {busy ? "Resuming…" : "Clear the halt"}
                </Button>
              </>
            ) : (
              <>
                <label className="block">
                  <span className="label mb-1 block">Reason (recorded on the event)</span>
                  <input
                    aria-label="Stop reason"
                    value={reason}
                    onChange={(event) => setReason(event.target.value)}
                    placeholder="Why trading should halt"
                    className="h-9 w-full rounded-lg border border-line-strong bg-surface-sunken px-2.5 text-xs text-ink"
                  />
                </label>
                <Button onClick={trip} disabled={busy} variant="danger" className="w-full">
                  {busy ? "Stopping…" : "Engage emergency stop"}
                </Button>
                <p className="text-[10px] text-ink-faint">
                  Requires a staff role. A user cannot halt the platform, and cannot clear its halt.
                </p>
              </>
            )}
          </div>
        </Panel>
      </div>

      <Panel className="mt-4" title="Recent risk checks" subtitle="Every decision, with what it was measured against" bodyClassName="p-0">
        {checks.length === 0 ? (
          <div className="px-4 py-4">
            <EmptyState
              title="No checks yet"
              description="Place an order on the Execution screen; the controller evaluates it and records the result here."
            />
          </div>
        ) : (
          <table className="table-base">
            <thead>
              <tr>
                <th>Instrument</th>
                <th>Side</th>
                <th className="text-right">Requested</th>
                <th className="text-right">Approved</th>
                <th>Decision</th>
                <th>Reason</th>
              </tr>
            </thead>
            <tbody>
              {checks.map((check) => (
                <tr key={check.id}>
                  <td className="text-xs font-medium">{check.instrument_symbol}</td>
                  <td className="text-xs text-ink-muted">{titleCase(check.side)}</td>
                  <td className="text-right text-xs text-ink-muted">{check.requested_quantity}</td>
                  <td className="text-right text-xs text-ink-muted">
                    {check.approved_quantity}
                    {check.approved_quantity < check.requested_quantity && (
                      <span className="ml-1 text-2xs text-caution">↓</span>
                    )}
                  </td>
                  <td>
                    <Badge tone={DECISION_TONE[check.decision] ?? "muted"}>
                      {titleCase(check.decision)}
                    </Badge>
                  </td>
                  <td className="max-w-md text-[10px] leading-relaxed text-ink-faint">{check.reason}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </Panel>

      <div className="mt-4 grid gap-4 lg:grid-cols-2">
        <Panel title="Rules applied, in order" subtitle="A rejection stops at the first binding constraint">
          <ul className="space-y-1.5">
            {[
              ["kill_switch", "Outranks everything: a tripped breaker refuses the order before any limit is read"],
              ["order_rate", "The runaway-loop guard"],
              ["per_trade_risk", "Measured against the stop, not the notional — can shrink an order instead of refusing it"],
              ["max_positions", "Adds to an existing position stay permitted at the limit"],
              ["max_exposure", "Projected post-trade exposure"],
              ["concentration", "Post-trade single-instrument share of equity"],
              ["correlation", "A second instrument that moves like one already held is one bet"],
            ].map(([rule, detail], index) => (
              <li key={rule} className="flex gap-2.5 border-b border-line pb-1.5 last:border-0">
                <span className="mt-0.5 font-mono text-[10px] text-ink-faint">{index + 1}</span>
                <div>
                  <p className="text-[11px] font-medium text-ink">{RULE_LABEL[rule] ?? rule}</p>
                  <p className="text-[10px] leading-relaxed text-ink-faint">{detail}</p>
                </div>
              </li>
            ))}
          </ul>
        </Panel>

        <Panel title="What this controller is not">
          <ul className="space-y-2.5 text-xs text-ink-muted">
            {[
              ["Not an optimiser", "It never sizes a position up, only down or to zero. It has no view on whether a trade is good."],
              ["Not learning", "No model, no weights, no thresholds tuned on outcomes. The same state always yields the same decision."],
              ["Not a liquidation engine", "Closing exposure is a separate policy decision a human makes with the book in front of them."],
              ["Not the whole story", "Execution validation still runs first: it bounds what the account can pay for, risk bounds what it may risk."],
              ["Not silent", "Every decision stores the checks, the observed values and the account snapshot it was measured against."],
            ].map(([title, detail]) => (
              <li key={title} className="rounded-md border border-line bg-surface-sunken px-3 py-2">
                <p className="font-medium text-ink">{title}</p>
                <p className="mt-0.5 text-[11px] leading-relaxed text-ink-faint">{detail}</p>
              </li>
            ))}
          </ul>
        </Panel>
      </div>
    </>
  );
}
