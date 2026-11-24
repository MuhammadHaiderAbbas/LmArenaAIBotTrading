import { useCallback, useEffect, useState } from "react";
import { Badge, EmptyState, PageHeader, Panel, Stat, useToast } from "../components/ui";
import { api } from "../services/api";
import type {
  ChainVerification,
  Finding,
  HealthReport,
  SealEntry,
  FindingStatus,
} from "../lib/demo/monitoringEngine";

function statusTone(status: FindingStatus): "positive" | "caution" | "negative" {
  if (status === "critical") return "negative";
  if (status === "warning") return "caution";
  return "positive";
}

function shortHash(hash: string | null | undefined): string {
  if (!hash) return "—";
  return `${hash.slice(0, 10)}…${hash.slice(-6)}`;
}

/**
 * Operations — the operational half of the security story.
 *
 * Two things this page deliberately does: it shows a finding's *message* rather than a
 * green tick, and it renders the chain's honest limitation next to the chain's status.
 * A verification screen that says "Verified" and nothing else is decoration.
 */
export function Operations() {
  const toast = useToast();
  const [report, setReport] = useState<HealthReport | null>(null);
  const [chain, setChain] = useState<ChainVerification | null>(null);
  const [entries, setEntries] = useState<SealEntry[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const load = useCallback(async () => {
    try {
      const [health, verification, trail] = await Promise.all([
        api.operationsHealth(),
        api.operationsAuditVerify(),
        api.operationsAudit(15),
      ]);
      setReport(health);
      setChain(verification);
      setEntries(trail.entries);
      setError(null);
    } catch (cause) {
      setError((cause as Error).message);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const seal = async () => {
    setBusy(true);
    try {
      const result = await api.operationsAuditSeal();
      toast({
        tone: "success",
        title: "Audit chain sealed",
        description: `${result.sealed} entr${result.sealed === 1 ? "y" : "ies"} adopted into the chain.`,
      });
      await load();
    } catch (cause) {
      toast({ tone: "error", title: "Could not seal", description: (cause as Error).message });
    } finally {
      setBusy(false);
    }
  };

  if (error) {
    return (
      <div>
        <PageHeader eyebrow="Security" title="Operations" description="Health, monitoring and audit integrity." />
        <EmptyState
          title="Operations data unavailable"
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
        eyebrow="Security"
        title="Operations"
        description="The eight checks the platform runs on itself, and a tamper-evident trail you can verify rather than trust."
        actions={
          <button type="button" className="btn btn-secondary" onClick={() => void seal()} disabled={busy}>
            {busy ? "Sealing…" : "Seal pending entries"}
          </button>
        }
      />

      <div className="mb-4 grid grid-cols-1 gap-3 sm:grid-cols-2 xl:grid-cols-4">
        <Stat
          label="Overall health"
          value={(report?.status ?? "—").toUpperCase()}
          hint={report ? `${report.checksRun} checks run` : "Loading"}
          tone={report?.status === "ok" ? "positive" : report?.status === "critical" ? "negative" : "neutral"}
        />
        <Stat
          label="Findings"
          value={report ? `${report.counts.ok} ok · ${report.counts.warning} warn · ${report.counts.critical} crit` : "—"}
          hint="worst finding sets the overall status"
        />
        <Stat
          label="Audit chain"
          value={chain ? (chain.verified ? "VERIFIED" : "BROKEN") : "—"}
          hint={chain ? `${chain.entries} sealed entries` : "Loading"}
          tone={chain?.verified ? "positive" : "negative"}
        />
        <Stat label="Head digest" value={shortHash(chain?.digest)} hint="publish this elsewhere" />
      </div>

      <div className="grid grid-cols-1 gap-4 xl:grid-cols-2">
        <Panel
          title="Monitoring findings"
          subtitle={`Scope: ${report?.scope === "demo" ? "this browser" : "this account"}`}
          scrollBody
        >
          <table className="w-full text-left text-xs">
            <thead>
              <tr className="text-[10px] uppercase tracking-[0.14em] text-ink-faint">
                <th className="py-2 pr-3 font-medium">Check</th>
                <th className="py-2 pr-3 font-medium">Status</th>
                <th className="py-2 font-medium">What it found</th>
              </tr>
            </thead>
            <tbody>
              {(report?.findings ?? []).map((finding: Finding) => (
                <tr key={finding.check} className="border-t border-line">
                  <td className="py-2.5 pr-3 align-top text-ink-muted">{finding.label}</td>
                  <td className="py-2.5 pr-3 align-top">
                    <Badge tone={statusTone(finding.status)}>{finding.status}</Badge>
                  </td>
                  <td className="py-2.5 align-top text-ink">{finding.message}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </Panel>

        <Panel
          title="Audit trail"
          subtitle="Append-only. Editing, deleting or inserting a row breaks the chain."
          scrollBody
        >
          {entries.length === 0 ? (
            <p className="px-1 py-6 text-center text-xs text-ink-faint">
              Nothing sealed in this session yet.
            </p>
          ) : (
            <table className="w-full text-left text-xs">
              <thead>
                <tr className="text-[10px] uppercase tracking-[0.14em] text-ink-faint">
                  <th className="py-2 pr-3 font-medium">#</th>
                  <th className="py-2 pr-3 font-medium">Action</th>
                  <th className="py-2 pr-3 font-medium">Seal</th>
                  <th className="py-2 font-medium">Recorded</th>
                </tr>
              </thead>
              <tbody>
                {entries.map((entry) => (
                  <tr key={entry.sequence} className="border-t border-line">
                    <td className="py-2.5 pr-3 align-top font-mono text-ink-faint">{entry.sequence}</td>
                    <td className="py-2.5 pr-3 align-top text-ink">{entry.action}</td>
                    <td className="py-2.5 pr-3 align-top font-mono text-[10px] text-ink-muted">
                      {shortHash(entry.entryHash)}
                    </td>
                    <td className="py-2.5 align-top text-ink-muted sm:whitespace-nowrap">
                      {new Date(entry.recordedAt).toLocaleString("en-GB")}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </Panel>
      </div>

      <Panel title="What this page refuses to claim" className="mt-4">
        <ul className="space-y-2.5 text-xs leading-relaxed text-ink-muted">
          <li>
            <span className="text-ink">A hash chain detects edits and deletions; it does not prevent them.</span>{" "}
            Someone with write access to the database can re-seal the whole tail and the chain
            will verify. Publishing the head digest somewhere the database owner does not control
            is what makes that detectable — which is why the digest is shown above.
          </li>
          <li>
            <span className="text-ink">Two of the eight checks are stubs in this demo.</span> Model
            degradation and API-failure monitoring need a server and a model registry. The backend
            runs them against real `DriftEvent` and `SystemEvent` rows; here they say so instead of
            showing a green tick they did not earn.
          </li>
          <li>
            <span className="text-ink">Monitoring is read-only.</span> It reports; it does not act.
            Acting on a breach is the risk engine's job, and that engine is separate by design.
          </li>
          <li>
            <span className="text-ink">The chain proves ordering, not truth.</span> A sealed entry
            records what the application wrote. It cannot tell you the application was right.
          </li>
        </ul>
      </Panel>
    </div>
  );
}
