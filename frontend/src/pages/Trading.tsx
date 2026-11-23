import { useState } from "react";
import { Link } from "react-router-dom";
import { Badge, Button, Dot, Panel, PageHeader, Stat, useToast } from "../components/ui";
import { agents, strategies } from "../lib/demoStatic";
import { IconBot, IconPause, IconPlay, IconStop } from "../lib/icons";

type EngineState = "stopped" | "running" | "paused";

export function Trading() {
  const [state, setState] = useState<EngineState>("stopped");
  const [strategy, setStrategy] = useState(strategies[0].id);
  const toast = useToast();

  const transition = (next: EngineState, message: string) => {
    setState(next);
    toast({
      tone: next === "stopped" ? "error" : "success",
      title: message,
      description: "Phase 1 stub — no order router is connected and no capital is at risk.",
    });
  };

  return (
    <>
      <PageHeader
        eyebrow="Autonomous execution"
        title="AI Trading"
        description="Control the paper-trading engine. The AI proposes; the independent risk controller decides; only the execution service can submit orders."
        actions={
          <Badge tone={state === "running" ? "positive" : state === "paused" ? "caution" : "muted"}>
            <Dot tone={state === "running" ? "positive" : state === "paused" ? "caution" : "muted"} pulse={state === "running"} />
            Engine {state}
          </Badge>
        }
      />

      <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
        <Stat label="Environment" value="Paper" hint="Live trading disabled" tone="accent" />
        <Stat label="Active strategy" value={strategies.find((s) => s.id === strategy)?.name.split(" ")[0] ?? "—"} hint="Selected configuration" />
        <Stat label="Proposals today" value={0} hint="Agent orchestration begins in Phase 5" />
        <Stat label="Open positions" value={0} hint="Execution engine begins in Phase 8" />
      </div>

      <div className="mt-4 grid gap-4 lg:grid-cols-3">
        <Panel className="lg:col-span-2" title="Control panel" subtitle="Strategy selection and execution controls">
          <div className="space-y-4">
            <label className="block">
              <span className="label mb-1.5 block">Strategy</span>
              <select
                value={strategy}
                onChange={(event) => setStrategy(event.target.value)}
                className="h-10 w-full rounded-lg border border-line-strong bg-surface-sunken px-3 text-sm text-ink"
              >
                {strategies.map((item) => (
                  <option key={item.id} value={item.id}>
                    {item.name} · {item.family} · Phase {item.phase}
                  </option>
                ))}
              </select>
            </label>

            <div className="flex flex-wrap gap-2">
              <Button
                disabled={state === "running"}
                onClick={() => transition("running", "Paper engine started")}
              >
                <IconPlay /> Start
              </Button>
              <Button
                variant="secondary"
                disabled={state !== "running"}
                onClick={() => transition("paused", "Paper engine paused")}
              >
                <IconPause /> Pause
              </Button>
              <Button
                variant="danger"
                disabled={state === "stopped"}
                onClick={() => transition("stopped", "Paper engine stopped")}
              >
                <IconStop /> Stop
              </Button>
              <Link to="/risk" className="ml-auto self-center text-xs font-medium text-accent-soft hover:underline">
                Emergency controls live in the Risk Centre →
              </Link>
            </div>

            <div className="rounded-lg border border-caution/25 bg-caution/[0.06] px-3 py-2.5 text-2xs leading-relaxed text-caution">
              <strong>Safety boundary.</strong> Agents never hold broker credentials and cannot
              execute trades. They emit structured proposals; a deterministic trading service
              validates each proposal and an independent risk controller can reject it. No proposal
              can bypass risk limits.
            </div>

            <div>
              <p className="label mb-2">Activity timeline</p>
              <ol className="relative space-y-3 border-l border-line pl-4">
                {[
                  ["Engine initialised", "Paper adapter ready · no broker connection", "now"],
                  ["Risk controller armed", "Nine deterministic limits loaded", "now"],
                  ["Awaiting market data", "Phase 3 connects provider adapters", "pending"],
                  ["Awaiting agent orchestration", "Phase 5 wires specialist agents", "pending"],
                ].map(([title, detail, when]) => (
                  <li key={title} className="relative">
                    <span
                      className={
                        when === "now"
                          ? "absolute -left-[21px] top-1.5 h-1.5 w-1.5 rounded-full bg-positive"
                          : "absolute -left-[21px] top-1.5 h-1.5 w-1.5 rounded-full bg-ink-faint"
                      }
                      aria-hidden
                    />
                    <p className="text-xs font-medium text-ink">{title}</p>
                    <p className="text-[11px] text-ink-muted">{detail}</p>
                  </li>
                ))}
              </ol>
            </div>
          </div>
        </Panel>

        <Panel title="Agent readiness" subtitle="What each specialist needs" bodyClassName="p-0">
          <ul className="divide-y divide-line/70">
            {agents.map((agent) => (
              <li key={agent.id} className="px-4 py-2.5">
                <div className="flex items-center justify-between gap-2">
                  <p className="truncate text-xs font-medium text-ink">{agent.name}</p>
                  <Badge tone={agent.status === "idle" ? "positive" : agent.status === "blocked" ? "muted" : "caution"}>
                    P{agent.phase}
                  </Badge>
                </div>
                <p className="mt-0.5 text-[10px] leading-relaxed text-ink-faint">{agent.description}</p>
              </li>
            ))}
          </ul>
        </Panel>
      </div>

      <div className="mt-4 grid gap-4 lg:grid-cols-2">
        <Panel title="Structured proposal format" subtitle="What an agent will submit from Phase 5">
          <pre className="overflow-x-auto rounded-lg border border-line bg-surface-sunken p-4 font-mono text-[11px] leading-relaxed text-ink-muted">
{`{
  "symbol": "GBPUSD",
  "direction": "BUY",
  "strategy_id": "trend_following_v1",
  "confidence": 0.74,
  "entry_type": "LIMIT",
  "stop_loss": 1.2650,
  "take_profit": 1.2750,
  "invalidation_conditions": [
    "Trend reversal",
    "Volatility outside validated range"
  ],
  "risk_check_required": true
}`}
          </pre>
          <p className="mt-3 text-[11px] leading-relaxed text-ink-faint">
            Illustrative only. Confidence must be calibrated per model and is not treated as a
            probability unless validated as one. Entry, stop and target are always computed from
            current data and strategy rules.
          </p>
        </Panel>

        <Panel title="Why nothing is trading yet">
          <ul className="space-y-2.5 text-xs text-ink-muted">
            {[
              ["No market data", "Phase 3 connects licensed price and volume feeds."],
              ["No agents", "Phase 5 implements evidence-backed specialist findings."],
              ["No validated models", "Phase 6 requires walk-forward, out-of-sample evidence."],
              ["No execution", "Phase 8 adds the order lifecycle and simulated fills."],
              ["No risk service", "Phase 9 makes the veto independent and auditable."],
            ].map(([title, detail]) => (
              <li key={title} className="rounded-md border border-line bg-surface-sunken px-3 py-2">
                <p className="font-medium text-ink">{title}</p>
                <p className="mt-0.5 text-[11px] leading-relaxed text-ink-faint">{detail}</p>
              </li>
            ))}
          </ul>
          <p className="mt-3 flex items-center gap-2 text-[11px] text-ink-faint">
            <IconBot /> A reliable system is built before complex AI is added.
          </p>
        </Panel>
      </div>
    </>
  );
}
