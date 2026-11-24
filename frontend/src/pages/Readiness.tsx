import { useCallback, useEffect, useMemo, useState } from "react";
import { Badge, EmptyState, PageHeader, Panel, Stat } from "../components/ui";
import { api } from "../services/api";
import type {
  BrokerCatalogue,
  ComplianceAssessment,
  ReadinessItem,
  ReadinessStatus,
  ReleaseGate,
  StageReport,
} from "../lib/readinessTypes";

const STATUS_LABEL: Record<ReadinessStatus, string> = {
  done: "Done",
  partial: "Partial",
  blocked: "Blocked",
  not_implemented: "Not built",
};

function statusTone(status: ReadinessStatus): "positive" | "caution" | "negative" | "muted" {
  if (status === "done") return "positive";
  if (status === "partial") return "caution";
  if (status === "blocked") return "negative";
  return "muted";
}

function titleCase(value: string): string {
  return value
    .split("_")
    .map((part) => part.charAt(0).toUpperCase() + part.slice(1))
    .join(" ");
}

/**
 * Readiness — Phase 12's deliverable.
 *
 * This page is an assessment, not a marketing page, and it is designed to be read by
 * someone deciding whether the platform may go further. So:
 *
 * * the headline number is the count of things **not** ready;
 * * every item shows its evidence, which is a fact about the running system;
 * * the gate that passes says, in its own words, what passing it does not mean.
 */
export function Readiness() {
  const [assessment, setAssessment] = useState<ComplianceAssessment | null>(null);
  const [gate, setGate] = useState<ReleaseGate | null>(null);
  const [stages, setStages] = useState<StageReport | null>(null);
  const [brokers, setBrokers] = useState<BrokerCatalogue | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      const [compliance, gateReport, stage, catalogue] = await Promise.all([
        api.readinessCompliance(),
        api.readinessGate(),
        api.readinessStages(),
        api.readinessBrokers(),
      ]);
      setAssessment(compliance);
      setGate(gateReport);
      setStages(stage);
      setBrokers(catalogue);
      setError(null);
    } catch (cause) {
      setError((cause as Error).message);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const grouped = useMemo(() => {
    const buckets = new Map<string, ReadinessItem[]>();
    for (const item of assessment?.items ?? []) {
      const list = buckets.get(item.category) ?? [];
      list.push(item);
      buckets.set(item.category, list);
    }
    return [...buckets.entries()];
  }, [assessment]);

  if (error) {
    return (
      <div>
        <PageHeader eyebrow="Phase 12" title="Readiness" description="Compliance assessment and release gate." />
        <EmptyState
          title="Assessment unavailable"
          description={error}
          action={
            <button type="button" className="btn btn-secondary" onClick={() => void load()}>
              Try again
            </button>
          }
        />
      </div>
    );
  }

  return (
    <div>
      <PageHeader
        eyebrow="Phase 12"
        title="Readiness"
        description="What is built, what is not, and what would have to be true before this platform could handle customer money."
      />

      <div className="mb-4 grid grid-cols-1 gap-3 sm:grid-cols-2 xl:grid-cols-4">
        <Stat
          label="Live ready"
          value={assessment?.live_ready ? "YES" : "NO"}
          hint={assessment ? `${assessment.blocking_for_live.length} blocking items` : "Loading"}
          tone="negative"
        />
        <Stat
          label="Release gate"
          value={(gate?.verdict ?? "—").toUpperCase()}
          hint={gate?.scope ?? ""}
          tone={gate?.verdict === "pass" ? "positive" : "negative"}
        />
        <Stat
          label="Done"
          value={assessment ? `${assessment.counts.done}/${assessment.items.length}` : "—"}
          hint="items with evidence in the running system"
        />
        <Stat
          label="Current stage"
          value={stages ? titleCase(stages.current.stage) : "—"}
          hint={stages ? `next stage reachable: ${stages.current.next_reachable ? "yes" : "no"}` : ""}
        />
      </div>

      <Panel
        title="Blocking for live trading"
        subtitle="Every one of these needs something this codebase cannot supply on its own."
        scrollBody
        className="mb-4"
      >
        <table className="w-full text-left text-xs">
          <thead>
            <tr className="text-[10px] uppercase tracking-[0.14em] text-ink-faint">
              <th className="py-2 pr-3 font-medium">Item</th>
              <th className="py-2 font-medium">Unblocked by</th>
            </tr>
          </thead>
          <tbody>
            {(assessment?.blocking_for_live ?? []).map((entry) => (
              <tr key={entry.key} className="border-t border-line">
                <td className="py-2.5 pr-3 align-top text-ink">{entry.title}</td>
                <td className="py-2.5 align-top text-ink-muted">{entry.unblocked_by ?? "—"}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </Panel>

      <Panel title="Release gate" subtitle={gate?.scope} className="mb-4">
        <ul className="space-y-2">
          {(gate?.checks ?? []).map((check) => (
            <li key={check.check} className="flex flex-wrap items-start gap-2 border-t border-line pt-2 first:border-0 first:pt-0">
              <Badge tone={check.passed ? "positive" : check.blocking === false ? "caution" : "negative"}>
                {check.passed ? "pass" : check.blocking === false ? "informational" : "fail"}
              </Badge>
              <span className="min-w-0 flex-1 text-xs text-ink">
                <span className="font-mono text-[11px] text-ink-muted">{check.check}</span>
                <span className="block text-ink-faint">{check.detail}</span>
              </span>
            </li>
          ))}
        </ul>
        <p className="mt-3 border-t border-line pt-3 text-[11px] leading-relaxed text-ink-faint">
          {gate?.statement}
        </p>
      </Panel>

      <div className="grid grid-cols-1 gap-4 xl:grid-cols-2">
        <Panel title="Broker integration" subtitle="Sandbox only. No live adapter exists." scrollBody>
          <table className="w-full text-left text-xs">
            <thead>
              <tr className="text-[10px] uppercase tracking-[0.14em] text-ink-faint">
                <th className="py-2 pr-3 font-medium">Broker</th>
                <th className="py-2 pr-3 font-medium">Sandbox</th>
                <th className="py-2 font-medium">Live</th>
              </tr>
            </thead>
            <tbody>
              {(brokers?.brokers ?? []).map((broker) => (
                <tr key={broker.key} className="border-t border-line">
                  <td className="py-2.5 pr-3 align-top text-ink">{broker.name}</td>
                  <td className="py-2.5 pr-3 align-top">
                    <Badge tone="positive">{broker.sandbox_supported ? "yes" : "no"}</Badge>
                  </td>
                  <td className="py-2.5 align-top">
                    <Badge tone="negative">{broker.live_supported ? "yes" : "not built"}</Badge>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          <p className="mt-3 border-t border-line pt-3 text-[11px] leading-relaxed text-ink-faint">
            {brokers?.live_trading.reason}
          </p>
        </Panel>

        <Panel title="Stage model" subtitle="From the blueprint's three-stage regulatory map." scrollBody>
          <ul className="space-y-3">
            {(stages?.stages ?? []).map((stage) => (
              <li key={stage.key} className="border-t border-line pt-3 first:border-0 first:pt-0">
                <div className="flex flex-wrap items-center gap-2">
                  <Badge
                    tone={
                      stage.status === "passed"
                        ? "positive"
                        : stage.status === "current"
                          ? "accent"
                          : "muted"
                    }
                  >
                    {stage.status}
                  </Badge>
                  <span className="text-xs font-medium text-ink">{stage.label}</span>
                </div>
                <p className="mt-1 text-[11px] leading-relaxed text-ink-faint">{stage.requirement}</p>
              </li>
            ))}
          </ul>
        </Panel>
      </div>

      <Panel
        title="Assessment"
        subtitle={`Scope: ${assessment?.scope ?? "—"}`}
        scrollBody
        className="mt-4"
      >
        <table className="w-full text-left text-xs">
          <thead>
            <tr className="text-[10px] uppercase tracking-[0.14em] text-ink-faint">
              <th className="py-2 pr-3 font-medium">Item</th>
              <th className="py-2 pr-3 font-medium">Status</th>
              <th className="py-2 font-medium">Evidence</th>
            </tr>
          </thead>
          <tbody>
            {grouped.map(([category, items]) => (
              <>
                <tr key={`${category}-heading`} className="border-t border-line">
                  <td colSpan={3} className="pt-3 pb-1 text-[10px] uppercase tracking-[0.16em] text-ink-faint">
                    {titleCase(category)}
                  </td>
                </tr>
                {items.map((entry) => (
                  <tr key={entry.key} className="border-t border-line align-top">
                    <td className="py-2.5 pr-3 text-ink">
                      {entry.title}
                      {entry.blocks_live && (
                        <span className="ml-2 text-[10px] uppercase tracking-wide text-negative">blocks live</span>
                      )}
                    </td>
                    <td className="py-2.5 pr-3">
                      <Badge tone={statusTone(entry.status)}>{STATUS_LABEL[entry.status]}</Badge>
                    </td>
                    <td className="py-2.5 text-ink-muted">{entry.evidence}</td>
                  </tr>
                ))}
              </>
            ))}
          </tbody>
        </table>
      </Panel>

      <Panel title="What this page refuses to claim" className="mt-4">
        <ul className="space-y-2.5 text-xs leading-relaxed text-ink-muted">
          <li>
            <span className="text-ink">Passing the release gate is not a compliance sign-off.</span>{" "}
            It checks that this paper-trading build has no live path, a verifying audit chain and a
            separate risk controller. It does not assess whether the business may lawfully be
            promoted, and it is not legal advice.
          </li>
          <li>
            <span className="text-ink">"Not built" is not the same as "not needed".</span> KYC,
            client-money safeguarding and GDPR erasure are marked not built because they genuinely
            do not exist. They are only acceptable while the platform holds no customer money.
          </li>
          <li>
            <span className="text-ink">Paper evidence is a sample, not a season.</span> The blueprint
            asks for months of paper trading across varied conditions before any live step. The
            order count on this page is not that, and the platform makes no profitability claim.
          </li>
          <li>
            <span className="text-ink">The demo's assessment is resolved from demo state.</span> It
            is the same shape as the server's, but the server's is the authoritative one.
          </li>
        </ul>
      </Panel>
    </div>
  );
}
