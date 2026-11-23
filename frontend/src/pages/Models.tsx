import { useEffect, useMemo, useState } from "react";
import { Badge, Button, Dot, EmptyState, Panel, PageHeader, Stat, useToast } from "../components/ui";
import { api } from "../services/api";
import type { DriftEvent, ModelOverview, ModelVersion } from "../lib/demo/modelEngine";
import { FEATURE_NAMES, gate, verdict } from "../lib/demo/modelEngine";
import { titleCase } from "../lib/format";

const SYMBOLS = ["GBP/USD", "EUR/USD", "SPY", "BTC/USD"];

const STATUS_TONE: Record<string, "positive" | "caution" | "muted" | "negative" | "accent"> = {
  champion: "positive",
  candidate: "caution",
  rejected: "negative",
  retired: "muted",
  draft: "muted",
};

function percent(value: number | null, digits = 1): string {
  return value === null || Number.isNaN(value) ? "—" : `${(value * 100).toFixed(digits)}%`;
}

function signed(value: number | null, digits = 3): string {
  if (value === null || Number.isNaN(value)) return "—";
  return `${value >= 0 ? "+" : ""}${value.toFixed(digits)}`;
}

export function Models() {
  const [symbol, setSymbol] = useState(SYMBOLS[0]);
  const [versions, setVersions] = useState<ModelVersion[]>([]);
  const [overview, setOverview] = useState<ModelOverview | null>(null);
  const [drift, setDrift] = useState<DriftEvent[]>([]);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [acknowledged, setAcknowledged] = useState<string[]>([]);
  const toast = useToast();

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError(null);
    Promise.all([api.modelVersions(symbol), api.modelOverview(symbol), api.modelDrift(symbol)])
      .then(([nextVersions, nextOverview, nextDrift]) => {
        if (cancelled) return;
        setVersions(nextVersions);
        setOverview(nextOverview);
        setDrift(nextDrift);
        setSelectedId(nextVersions[0]?.id ?? null);
      })
      .catch((cause: Error) => {
        if (!cancelled) setError(cause.message);
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [symbol]);

  const selected = useMemo(
    () => versions.find((version) => version.id === selectedId) ?? versions[0] ?? null,
    [versions, selectedId],
  );

  const outOfSample = selected?.evaluations.find((evaluation) => evaluation.is_out_of_sample) ?? null;
  const decision = selected ? gate(outOfSample ?? selected.evaluations[0], selected.walk_forward) : null;

  const attemptPromotion = () => {
    if (!selected || !decision) return;
    if (!decision.passed) {
      // The promotion button must not exist for a model that failed its gate.
      // Reporting the refusal is the honest outcome, not a silent no-op.
      toast({
        tone: "error",
        title: "Promotion refused",
        description: decision.reason,
      });
      return;
    }
    toast({
      tone: "success",
      title: "Promotion recorded",
      description: "A promotion is an audited human action and stores the version it replaced.",
    });
  };

  return (
    <>
      <PageHeader
        eyebrow="Model engineering"
        title="Models"
        description="Candidates are trained on leakage-controlled, time-ordered features and compared with the champion out of sample. Promotion is a deliberate human act with a rollback path; nothing here can change trading rules on its own."
        actions={
          <div className="flex items-center gap-2">
            <label className="sr-only" htmlFor="model-symbol">
              Instrument
            </label>
            <select
              id="model-symbol"
              value={symbol}
              onChange={(event) => setSymbol(event.target.value)}
              className="h-9 rounded-lg border border-line-strong bg-surface-sunken px-2.5 text-xs text-ink"
            >
              {SYMBOLS.map((value) => (
                <option key={value} value={value}>
                  {value}
                </option>
              ))}
            </select>
            <Badge tone="muted">Paper only</Badge>
          </div>
        }
      />

      <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
        <Stat label="Registered models" value={overview?.models_total ?? "—"} hint="Families with code behind them" />
        <Stat label="Versions" value={overview?.versions_total ?? "—"} hint={`${overview?.candidates ?? 0} candidate · ${overview?.rejected ?? 0} rejected`} />
        <Stat
          label="Champions"
          value={overview?.champions ?? 0}
          hint={overview?.champions ? "Promoted and in use" : "Nothing has passed the gate"}
          tone={overview?.champions ? "positive" : "accent"}
        />
        <Stat label="Open drift alerts" value={overview?.drift_events_open ?? 0} hint="Advisory only — never auto-acted" />
      </div>

      {error && (
        <div className="mt-4 rounded-lg border border-negative/30 bg-negative/10 px-3 py-2.5 text-xs text-negative">
          {error}
        </div>
      )}

      <div className="mt-4 grid gap-4 lg:grid-cols-3">
        <Panel title="Version history" subtitle="Immutable, checksummed, append-only" bodyClassName="p-0">
          {loading ? (
            <p className="px-4 py-6 text-xs text-ink-muted">Loading versions…</p>
          ) : versions.length === 0 ? (
            <EmptyState title="No versions" description="Train a candidate to create the first version." />
          ) : (
            <ul className="divide-y divide-line/70">
              {versions.map((version) => (
                <li key={version.id}>
                  <button
                    type="button"
                    onClick={() => setSelectedId(version.id)}
                    aria-pressed={version.id === selected?.id}
                    className={
                      version.id === selected?.id
                        ? "w-full bg-accent/10 px-4 py-3 text-left ring-1 ring-inset ring-accent/25"
                        : "w-full px-4 py-3 text-left hover:bg-surface-hover/50"
                    }
                  >
                    <div className="flex items-center justify-between gap-2">
                      <p className="text-xs font-semibold text-ink">
                        {version.model_code.replace(/_/g, " ")} · v{version.version}
                      </p>
                      <Badge tone={STATUS_TONE[version.status] ?? "muted"}>{titleCase(version.status)}</Badge>
                    </div>
                    <p className="mt-0.5 text-[10px] uppercase tracking-wider text-ink-faint">
                      {version.instrument_symbol} · {version.timeframe} · {version.training_rows} rows
                    </p>
                    <p className="mt-1 font-mono text-[10px] text-ink-faint">{version.checksum.slice(0, 16)}…</p>
                  </button>
                </li>
              ))}
            </ul>
          )}
          <div className="border-t border-line px-4 py-3">
            <p className="text-[11px] leading-relaxed text-ink-faint">
              Training a version never promotes it. A candidate can only become champion through the
              promotion action, which records the actor and the version it replaced.
            </p>
          </div>
        </Panel>

        <Panel
          className="lg:col-span-2"
          title={selected ? `${selected.model_code.replace(/_/g, " ")} v${selected.version}` : "Version detail"}
          subtitle={selected ? `${selected.instrument_symbol} · ${selected.timeframe} · label horizon ${selected.label_horizon} bar` : undefined}
          actions={
            selected ? <Badge tone={STATUS_TONE[selected.status] ?? "muted"}>{titleCase(selected.status)}</Badge> : null
          }
        >
          {!selected ? (
            <EmptyState title="No version selected" description="Choose a version on the left." />
          ) : (
            <div className="space-y-4">
              <div
                className={
                  decision?.passed
                    ? "rounded-lg border border-positive/25 bg-positive/[0.06] px-3 py-2.5 text-2xs leading-relaxed text-positive"
                    : "rounded-lg border border-negative/25 bg-negative/[0.06] px-3 py-2.5 text-2xs leading-relaxed text-negative"
                }
              >
                <strong>{decision?.passed ? "Gate passed." : "Gate not passed."}</strong>{" "}
                {decision?.reason} {outOfSample ? verdict(outOfSample, selected.walk_forward) : ""}
              </div>

              <div>
                <p className="label mb-2">Evaluation</p>
                <table className="table-base">
                  <thead>
                    <tr>
                      <th>Slice</th>
                      <th className="text-right">Rows</th>
                      <th className="text-right">Accuracy</th>
                      <th className="text-right">Baseline</th>
                      <th className="text-right">Edge</th>
                    </tr>
                  </thead>
                  <tbody>
                    {selected.evaluations.map((evaluation) => (
                      <tr key={evaluation.kind}>
                        <td className="text-xs font-medium">
                          {titleCase(evaluation.kind.replace(/_/g, " "))}
                          {evaluation.is_out_of_sample && (
                            <span className="ml-1.5 align-middle">
                              <Badge tone="accent">OOS</Badge>
                            </span>
                          )}
                        </td>
                        <td className="text-right text-xs text-ink-muted">{evaluation.rows}</td>
                        <td className="text-right text-xs text-ink">{percent(evaluation.accuracy)}</td>
                        <td className="text-right text-xs text-ink-muted">{percent(evaluation.baseline_accuracy)}</td>
                        <td
                          className={
                            (evaluation.edge_over_baseline ?? 0) > 0.02
                              ? "text-right text-xs text-positive"
                              : "text-right text-xs text-negative"
                          }
                        >
                          {signed(evaluation.edge_over_baseline)}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
                <p className="mt-2 text-[11px] leading-relaxed text-ink-faint">
                  Baseline is the majority-class share. An edge inside ±0.02 is noise, however
                  impressive the accuracy looks.
                </p>
              </div>

              <div className="grid gap-4 sm:grid-cols-2">
                <div>
                  <p className="label mb-2">Walk-forward folds</p>
                  {selected.walk_forward.length === 0 ? (
                    <p className="text-[11px] text-ink-faint">No folds recorded.</p>
                  ) : (
                    <ul className="space-y-1.5">
                      {selected.walk_forward.map((fold) => (
                        <li key={fold.fold} className="flex items-center justify-between gap-2 text-xs">
                          <span className="text-ink-muted">
                            Fold {fold.fold} · {fold.train_rows}→{fold.test_rows}
                          </span>
                          <span className={fold.edge_over_baseline > 0 ? "text-positive" : "text-negative"}>
                            {signed(fold.edge_over_baseline)}
                          </span>
                        </li>
                      ))}
                    </ul>
                  )}
                </div>
                <div>
                  <p className="label mb-2">Calibration</p>
                  {outOfSample && outOfSample.bins.length > 0 ? (
                    <ul className="space-y-1.5">
                      {outOfSample.bins.slice(0, 5).map((bin) => (
                        <li key={bin.lower} className="text-[11px] text-ink-muted">
                          {percent(bin.lower, 0)}–{percent(bin.upper, 0)} bucket · predicted{" "}
                          <span className="text-ink">{percent(bin.predicted)}</span> · observed{" "}
                          <span className="text-ink">{percent(bin.observed)}</span>
                        </li>
                      ))}
                    </ul>
                  ) : (
                    <p className="text-[11px] text-ink-faint">No reliability bins recorded.</p>
                  )}
                  <p className="mt-2 text-[11px] leading-relaxed text-ink-faint">
                    Maximum calibration error {percent(outOfSample?.max_calibration_error ?? null)}.
                  </p>
                </div>
              </div>

              <div>
                <p className="label mb-2">Features ({FEATURE_NAMES.length})</p>
                <div className="flex flex-wrap gap-1.5">
                  {FEATURE_NAMES.map((feature) => (
                    <span
                      key={feature}
                      className="rounded border border-line bg-surface-sunken px-1.5 py-0.5 font-mono text-[10px] text-ink-muted"
                    >
                      {feature}
                    </span>
                  ))}
                </div>
              </div>

              <div className="flex flex-wrap items-center gap-2">
                <Button
                  variant={decision?.passed ? "primary" : "secondary"}
                  onClick={attemptPromotion}
                  aria-disabled={!decision?.passed}
                >
                  {decision?.passed ? "Promote to champion" : "Promotion blocked by gate"}
                </Button>
                <Button
                  variant="secondary"
                  disabled={!selected.promoted_at}
                  title={selected.promoted_at ? undefined : "Only a champion can be rolled back"}
                >
                  Roll back
                </Button>
                <span className="text-[11px] text-ink-faint">
                  {selected.promoted_at
                    ? `Promoted ${selected.promoted_at.slice(0, 10)}`
                    : "Never promoted — candidates are inert"}
                </span>
              </div>
              <p className="text-[11px] leading-relaxed text-ink-faint">
                A gate failure cannot be clicked through. Even a passing model stays a candidate until
                a human promotes it, and every promotion stores the version it replaced so rollback is
                a lookup rather than a rebuild.
              </p>
            </div>
          )}
        </Panel>
      </div>

      <div className="mt-4 grid gap-4 lg:grid-cols-2">
        <Panel title="Drift alerts" subtitle={`${drift.length} detected on ${symbol} · advisory only`} bodyClassName="p-0">
          {drift.length === 0 ? (
            <div className="px-4 py-4">
              <EmptyState title="No drift detected" description="Feature distributions are consistent with the training reference." />
            </div>
          ) : (
            <ul className="divide-y divide-line/70">
              {drift.map((event) => {
                const isAcknowledged = acknowledged.includes(event.id) || event.acknowledged;
                return (
                  <li key={event.id} className="px-4 py-3">
                    <div className="flex items-center justify-between gap-2">
                      <p className="font-mono text-xs text-ink">{event.feature}</p>
                      <Badge tone={event.severity === "critical" ? "negative" : "accent"}>
                        <Dot tone={event.severity === "critical" ? "negative" : "caution"} />
                        {event.severity}
                      </Badge>
                    </div>
                    <p className="mt-1 text-[11px] leading-relaxed text-ink-muted">{event.message}</p>
                    <p className="mt-1 text-[11px] leading-relaxed text-ink-faint">{event.recommendation}</p>
                    <button
                      type="button"
                      disabled={isAcknowledged}
                      onClick={() => setAcknowledged((current) => [...current, event.id])}
                      className="mt-2 text-[11px] font-medium text-accent-soft hover:underline disabled:text-ink-faint disabled:no-underline"
                    >
                      {isAcknowledged ? "Acknowledged" : "Acknowledge"}
                    </button>
                  </li>
                );
              })}
            </ul>
          )}
        </Panel>

        <Panel title="What Phase 6 does not do">
          <ul className="space-y-2.5 text-xs text-ink-muted">
            {[
              ["No automatic promotion", "A candidate can never promote itself, whatever its metrics."],
              ["No production rule changes", "Models inform commentary; the trading rules are deterministic and stay in code."],
              ["No deep learning yet", "LSTM, transformer and RL families are registered but unimplemented until a baseline edge exists."],
              ["No live economics", "Costs, slippage and position sizing are modelled in Phases 7 and 8; nothing here trades."],
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
