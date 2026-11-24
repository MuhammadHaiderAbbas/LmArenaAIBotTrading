import { useEffect, useMemo, useState } from "react";
import {
  Badge,
  Button,
  EmptyState,
  Panel,
  PageHeader,
  Spinner,
  Stat,
  useToast,
} from "../components/ui";
import { api, ApiError, IS_DEMO } from "../services/api";
import { DEMO_AI_NOTE } from "../lib/demo/aiEngine";
import { IconFlask, IconShield } from "../lib/icons";
import type { Agent, AgentRun, AIOverview, Finding, Stance } from "../lib/aiTypes";

const STANCE_TONE: Record<Stance, "positive" | "negative" | "muted" | "neutral"> = {
  bullish: "positive",
  bearish: "negative",
  neutral: "muted",
  unknown: "neutral",
};

const STATUS_TONE: Record<string, "positive" | "negative" | "caution" | "muted"> = {
  completed: "positive",
  partial: "caution",
  refused: "muted",
  failed: "negative",
  running: "caution",
  pending: "muted",
};

const STATUS_LABEL: Record<string, string> = {
  completed: "Completed",
  partial: "Partial evidence",
  refused: "Refused — no evidence",
  failed: "Failed",
  running: "Running",
  pending: "Pending",
};

// Only instruments the demo engine can actually price. Offering a symbol that
// resolves to "no price history" would produce a refusal that looks like a data
// problem rather than a missing instrument.
const SYMBOLS = ["GBP/USD", "EUR/USD", "USD/JPY", "SPY", "AAPL", "NVDA", "BTC/USD", "ETH/USD"];

function percent(value: number | null): string {
  if (value === null) return "—";
  return `${(value * 100).toFixed(0)}%`;
}

function lines(text: string): string[] {
  return text
    .split("\n")
    .map((line) => line.trim())
    .filter(Boolean);
}

function FindingCard({ finding, agents }: { finding: Finding; agents: Agent[] }) {
  const [open, setOpen] = useState(false);
  const agent = agents.find((item) => item.code === finding.agent_code);
  const facts = lines(finding.facts);
  const uncertainties = lines(finding.uncertainties);

  return (
    <li className="rounded-lg border border-line bg-surface-sunken">
      <button
        type="button"
        onClick={() => setOpen((current) => !current)}
        aria-expanded={open}
        className="flex w-full items-start justify-between gap-3 px-3 py-2.5 text-left"
      >
        <div className="min-w-0">
          <div className="flex flex-wrap items-center gap-1.5">
            <span className="text-xs font-medium text-ink">{agent?.name ?? finding.agent_code}</span>
            {!finding.data_available && <Badge tone="muted">no data</Badge>}
            {finding.data_available && finding.stance !== "unknown" && (
              <Badge tone={STANCE_TONE[finding.stance]}>{finding.stance}</Badge>
            )}
          </div>
          <p className="mt-0.5 text-[11px] leading-snug text-ink-muted">{finding.headline}</p>
        </div>
        <div className="shrink-0 text-right">
          <p className="font-mono text-xs text-ink">{percent(finding.confidence)}</p>
          <p className="text-[9px] uppercase tracking-wide text-ink-faint">{finding.confidence_kind.replace("_", " ")}</p>
        </div>
      </button>

      {open && (
        <div className="space-y-2.5 border-t border-line/70 px-3 py-2.5">
          {facts.length > 0 && (
            <div>
              <p className="text-[9px] uppercase tracking-wide text-ink-faint">Measured</p>
              <ul className="mt-0.5 space-y-0.5">
                {facts.map((fact) => (
                  <li key={fact} className="font-mono text-[11px] leading-relaxed text-ink-muted">
                    {fact}
                  </li>
                ))}
              </ul>
            </div>
          )}

          {finding.interpretation && (
            <div>
              <p className="text-[9px] uppercase tracking-wide text-ink-faint">Interpretation</p>
              <p className="mt-0.5 text-[11px] leading-relaxed text-ink-muted">{finding.interpretation}</p>
            </div>
          )}

          {uncertainties.length > 0 && (
            <div>
              <p className="text-[9px] uppercase tracking-wide text-ink-faint">Uncertainty</p>
              <ul className="mt-0.5 list-disc space-y-0.5 pl-4">
                {uncertainties.map((note) => (
                  <li key={note} className="text-[11px] leading-relaxed text-caution">
                    {note}
                  </li>
                ))}
              </ul>
            </div>
          )}

          <div>
            <p className="text-[9px] uppercase tracking-wide text-ink-faint">
              Evidence ({finding.evidence.length})
            </p>
            <ul className="mt-1 space-y-1">
              {finding.evidence.map((item, index) => (
                <li
                  key={`${item.label}-${index}`}
                  className="flex items-start gap-2 rounded border border-line/60 bg-surface px-2 py-1.5"
                >
                  <Badge tone={item.kind === "absence" ? "caution" : "muted"}>{item.kind.replace("_", " ")}</Badge>
                  <div className="min-w-0">
                    <p className="truncate text-[11px] text-ink">{item.label}</p>
                    <p className="text-[10px] leading-snug text-ink-faint">{item.detail}</p>
                  </div>
                  {item.url && (
                    <a
                      href={item.url}
                      target="_blank"
                      rel="noreferrer noopener"
                      className="ml-auto shrink-0 text-[10px] text-accent-soft underline decoration-dotted"
                    >
                      source
                    </a>
                  )}
                </li>
              ))}
            </ul>
          </div>

          {finding.limitations && (
            <p className="rounded border border-line/60 bg-surface px-2 py-1.5 text-[10px] leading-relaxed text-ink-faint">
              <span className="font-medium text-ink-muted">Limitations. </span>
              {finding.limitations}
            </p>
          )}
        </div>
      )}
    </li>
  );
}

export function AIDesk() {
  const [agents, setAgents] = useState<Agent[]>([]);
  const [overview, setOverview] = useState<AIOverview | null>(null);
  const [run, setRun] = useState<AgentRun | null>(null);
  const [symbol, setSymbol] = useState("GBP/USD");
  const [question, setQuestion] = useState("");
  const [busy, setBusy] = useState(false);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const toast = useToast();

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const [agentList, summary] = await Promise.all([api.aiAgents(), api.aiOverview()]);
        if (cancelled) return;
        setAgents(agentList);
        setOverview(summary);
      } catch (cause) {
        if (!cancelled) setError(cause instanceof ApiError ? cause.message : "AI data unavailable.");
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  const implemented = useMemo(() => agents.filter((agent) => agent.implemented), [agents]);
  const planned = useMemo(() => agents.filter((agent) => !agent.implemented), [agents]);

  async function execute() {
    setBusy(true);
    setError("");
    try {
      const result = await api.aiRun({ symbol, question });
      setRun(result);
      const summary = await api.aiOverview();
      setOverview(summary);
      if (result.status === "refused") {
        toast({
          tone: "info",
          title: "Run declined to propose a stance",
          description: result.proposal_reason,
        });
      }
    } catch (cause) {
      setError(cause instanceof ApiError ? cause.message : "The run failed.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <>
      <PageHeader
        eyebrow="Structured, not chatty"
        title="AI Desk"
        description="Specialist agents read the evidence and produce structured findings. The orchestrator synthesises them into a proposal record — and says so when the evidence does not support one."
        actions={
          <div className="flex items-center gap-2">
            <Badge tone="positive">Phase 5 live</Badge>
            <Badge tone="muted">no execution path</Badge>
          </div>
        }
      />

      <div className="mb-4 rounded-lg border border-caution/40 bg-caution/10 px-4 py-3 text-xs leading-relaxed text-ink">
        <span className="font-medium">Proposals are not orders.</span> Nothing on this screen can place a
        trade. The independent risk controller and the order path arrive in Phase 9; every run records
        <code className="mx-1 rounded bg-surface-raised px-1 py-0.5 text-[11px]">tradeable: false</code>
        with the reason.
        {IS_DEMO && <span className="mt-1 block text-ink-muted">{DEMO_AI_NOTE}</span>}
      </div>

      {error && (
        <div className="mb-4 rounded-lg border border-negative/40 bg-negative/10 px-4 py-3 text-xs text-ink">
          {error}
        </div>
      )}

      <div className="grid gap-4 lg:grid-cols-3">
        <Panel
          className="lg:col-span-2"
          title="Run the desk"
          subtitle="One instrument, all specialists, one stored record"
        >
          <div className="space-y-3">
            <div className="flex flex-wrap items-end gap-2">
              <label className="flex flex-col gap-1 text-2xs text-ink-faint">
                Instrument
                <select
                  value={symbol}
                  onChange={(event) => setSymbol(event.target.value)}
                  className="rounded-md border border-line-strong bg-surface-sunken px-2 py-1.5 text-xs text-ink"
                >
                  {SYMBOLS.map((option) => (
                    <option key={option} value={option}>
                      {option}
                    </option>
                  ))}
                </select>
              </label>
              <label className="flex min-w-0 flex-1 basis-full flex-col gap-1 text-2xs text-ink-faint sm:basis-56">
                Question (recorded with the run, never obeyed)
                <input
                  value={question}
                  onChange={(event) => setQuestion(event.target.value)}
                  placeholder="e.g. Is the trend intact enough to add exposure?"
                  className="rounded-md border border-line-strong bg-surface-sunken px-2 py-1.5 text-xs text-ink placeholder:text-ink-faint"
                />
              </label>
              <Button onClick={execute} loading={busy} disabled={busy}>
                <IconFlask /> Run specialists
              </Button>
            </div>

            {run ? (
              <div className="space-y-3">
                <div className="rounded-lg border border-line bg-surface-sunken p-3">
                  <div className="flex flex-wrap items-center justify-between gap-2">
                    <div>
                      <p className="text-sm font-semibold text-ink">
                        {run.instrument_symbol} · {run.timeframe}
                      </p>
                      <p className="text-[11px] text-ink-faint">
                        {run.findings?.length ?? 0} findings · {run.duration_ms} ms
                      </p>
                    </div>
                    <div className="flex items-center gap-2">
                      <Badge tone={STATUS_TONE[run.status] ?? "muted"}>
                        {STATUS_LABEL[run.status] ?? run.status}
                      </Badge>
                      <Badge tone={STANCE_TONE[run.proposal_stance]}>
                        {run.proposal_stance === "unknown" ? "no stance" : run.proposal_stance}
                      </Badge>
                      {/* Confidence is shown with its kind, never alone. */}
                      <span className="font-mono text-xs text-ink">
                        {percent(run.proposal_confidence)}
                        <span className="ml-1 text-[9px] uppercase tracking-wide text-ink-faint">
                          rule score
                        </span>
                      </span>
                    </div>
                  </div>

                  <p className="mt-2 text-[11px] leading-relaxed text-ink-muted">{run.proposal_reason}</p>

                  {run.proposal_conflict && (
                    <p className="mt-2 rounded border border-caution/40 bg-caution/10 px-2 py-1.5 text-[11px] text-caution">
                      Agents disagree about conditions, not just direction. The conflict is recorded rather than
                      averaged away.
                    </p>
                  )}

                  <p className="mt-2 flex items-start gap-1.5 text-[10px] leading-relaxed text-ink-faint">
                    <IconShield width={12} height={12} className="mt-0.5 shrink-0" />
                    <span>
                      <span className="font-medium text-ink-muted">Not tradeable. </span>
                      {run.blocked_reason}
                    </span>
                  </p>
                </div>

                <ol className="space-y-2">
                  {(run.findings ?? []).map((finding) => (
                    <FindingCard key={finding.agent_code} finding={finding} agents={agents} />
                  ))}
                </ol>
              </div>
            ) : loading ? (
              <div className="py-10">
                <Spinner label="Loading agents" />
              </div>
            ) : (
              <EmptyState
                title="No run yet"
                description="Run the specialists to see their findings. Each finding separates measured facts from interpretation, lists its uncertainties, and attaches evidence — or records that there was none."
                icon={<IconFlask width={26} height={26} />}
              />
            )}
          </div>
        </Panel>

        <div className="space-y-4">
          <Panel title="Desk activity" subtitle="Last 30 days">
            {overview ? (
              <div className="grid grid-cols-2 gap-2">
                <Stat label="Runs" value={String(overview.runs_total)} />
                <Stat label="Refused" value={String(overview.runs_refused)} />
                <Stat label="Partial" value={String(overview.runs_partial)} />
                <Stat label="Tradeable" value={String(overview.tradeable_proposals)} />
              </div>
            ) : (
              <div className="py-4">
                <Spinner label="Loading activity" />
              </div>
            )}
            <p className="mt-2.5 text-[10px] leading-relaxed text-ink-faint">
              &ldquo;Refused&rdquo; counts runs where no specialist had usable evidence. A desk that always finds
              an opinion is not reading the data.
            </p>
          </Panel>

          <Panel title="Specialists" subtitle={`${implemented.length} of ${agents.length} built`}>
            <ul className="space-y-1.5">
              {implemented.map((agent) => (
                <li key={agent.code} className="flex items-start justify-between gap-2">
                  <div className="min-w-0">
                    <p className="truncate text-[11px] font-medium text-ink">{agent.name}</p>
                    <p className="text-[10px] leading-snug text-ink-faint">{agent.outputs}</p>
                  </div>
                  <Badge tone="positive">live</Badge>
                </li>
              ))}
              {planned.map((agent) => (
                <li key={agent.code} className="flex items-start justify-between gap-2 opacity-60">
                  <div className="min-w-0">
                    <p className="truncate text-[11px] font-medium text-ink">{agent.name}</p>
                    <p className="text-[10px] leading-snug text-ink-faint">{agent.inputs}</p>
                  </div>
                  <Badge tone="muted">planned</Badge>
                </li>
              ))}
            </ul>
          </Panel>

          <Panel title="How this avoids confident nonsense" subtitle="Rules the desk is built on">
            <ul className="space-y-2 text-[11px] leading-relaxed text-ink-muted">
              {[
                ["Absence is recorded", "A specialist with no data says so in its own evidence row. It never reports a neutral stance to look productive."],
                ["Confidence is labelled", "Scores are rule-derived, not calibrated probabilities. The label travels with the number."],
                ["Facts stay separate", "Measured values, interpretation, uncertainty and limitations are distinct fields."],
                ["Conflicts surface", "Disagreement between agents is displayed, not averaged into a tidy figure."],
                ["Nothing executes", "A proposal is a record. Execution needs Phase 9's independent risk controller."],
              ].map(([title, detail]) => (
                <li key={title}>
                  <span className="font-medium text-ink">{title}. </span>
                  {detail}
                </li>
              ))}
            </ul>
          </Panel>
        </div>
      </div>
    </>
  );
}
