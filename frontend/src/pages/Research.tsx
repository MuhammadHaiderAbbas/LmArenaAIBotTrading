import { useEffect, useMemo, useState } from "react";
import {
  Badge,
  Button,
  EmptyState,
  Panel,
  PageHeader,
  Spinner,
  useToast,
} from "../components/ui";
import { api, ApiError, IS_DEMO } from "../services/api";
import { DEMO_DATASET_NOTE } from "../lib/demo/researchEngine";
import { IconDoc, IconFlask, IconShield } from "../lib/icons";
import type {
  Claim,
  ClaimVerdict,
  ResearchDocument,
  ResearchOverview,
  ResearchReport,
  ResearchSource,
  SearchHit,
} from "../lib/researchTypes";

const VERDICT_TONE: Record<ClaimVerdict, "positive" | "caution" | "negative" | "muted"> = {
  supported: "positive",
  partially_supported: "caution",
  unsupported: "negative",
  contradicted: "negative",
  unverified: "muted",
};

const VERDICT_LABEL: Record<ClaimVerdict, string> = {
  supported: "Supported",
  partially_supported: "Partial",
  unsupported: "Unsupported",
  contradicted: "Contradicted",
  unverified: "Unverified",
};

const TIER_TONE: Record<string, "accent" | "muted" | "caution"> = {
  primary: "accent",
  official_secondary: "muted",
  commentary: "caution",
};

function formatDate(value: string | null): string {
  if (!value) return "undated";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "undated";
  return date.toLocaleDateString("en-GB", { day: "2-digit", month: "short", year: "numeric" });
}

const SUGGESTED = [
  "Bank Rate inflation decision",
  "Federal Reserve target range",
  "ECB deposit facility rate",
  "sterling dollar exchange rate",
];

export function Research() {
  const [sources, setSources] = useState<ResearchSource[]>([]);
  const [overview, setOverview] = useState<ResearchOverview | null>(null);
  const [documents, setDocuments] = useState<ResearchDocument[]>([]);
  const [report, setReport] = useState<(ResearchReport & { claims: Claim[] }) | null>(null);
  const [hits, setHits] = useState<SearchHit[]>([]);
  const [query, setQuery] = useState("Bank Rate inflation decision");
  const [instrument, setInstrument] = useState("GBP/USD");
  const [busy, setBusy] = useState(false);
  const [searching, setSearching] = useState(false);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);
  const [flaggedOnly, setFlaggedOnly] = useState(false);
  const toast = useToast();

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const [sourceList, summary, docs] = await Promise.all([
          api.researchSources(),
          api.researchOverview(),
          api.researchDocuments({ limit: 20 }),
        ]);
        if (cancelled) return;
        setSources(sourceList);
        setOverview(summary);
        setDocuments(docs);
      } catch (cause) {
        if (!cancelled) setError(cause instanceof ApiError ? cause.message : "Research data unavailable.");
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  const visibleDocs = useMemo(
    () => (flaggedOnly ? documents.filter((doc) => doc.prompt_injection_flags > 0) : documents),
    [documents, flaggedOnly],
  );

  async function runResearch() {
    if (!query.trim()) return;
    setBusy(true);
    setError("");
    try {
      const built = await api.createResearchReport({
        query,
        topic: "Monetary policy",
        instrument_symbol: instrument || null,
      });
      setReport(built);
      const evidence = await api.researchSearch(query, { instrument: instrument || undefined });
      setHits(evidence);
    } catch (cause) {
      setError(cause instanceof ApiError ? cause.message : "Report generation failed.");
    } finally {
      setBusy(false);
    }
  }

  async function runSearch() {
    if (!query.trim()) return;
    setSearching(true);
    try {
      setHits(await api.researchSearch(query, { instrument: instrument || undefined }));
    } catch (cause) {
      setError(cause instanceof ApiError ? cause.message : "Search failed.");
    } finally {
      setSearching(false);
    }
  }

  async function runIngest() {
    setBusy(true);
    try {
      const result = await api.ingestResearch();
      const unavailable = result.results.filter((row) => row.outcome !== "ok").length;
      toast({
        tone: unavailable ? "info" : "success",
        title: unavailable ? "Some sources could not be reached" : "Ingestion complete",
        description: unavailable
          ? `${unavailable} of ${result.results.length} sources reported a problem. Details are listed per source below.`
          : `${result.documents_new} new document(s) across ${result.sources} sources.`,
      });
      const [summary, docs] = await Promise.all([
        api.researchOverview(),
        api.researchDocuments({ limit: 20 }),
      ]);
      setOverview(summary);
      setDocuments(docs);
    } catch (cause) {
      setError(cause instanceof ApiError ? cause.message : "Ingestion failed.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <>
      <PageHeader
        eyebrow="Evidence first"
        title="AI Research Lab"
        description="Central-bank and regulatory sources are ingested, chunked and indexed. Ask a question and the lab returns claims, each pinned to the passage that supports it — and says so when it cannot."
        actions={
          <div className="flex items-center gap-2">
            <Badge tone="positive">Phase 4 live</Badge>
            <Button variant="secondary" onClick={runIngest} disabled={busy} loading={busy}>
              <IconDoc /> Sync sources
            </Button>
          </div>
        }
      />

      {IS_DEMO && (
        <div className="mb-4 rounded-lg border border-caution/40 bg-caution/10 px-4 py-3 text-xs leading-relaxed text-ink">
          <span className="font-medium">Synthetic demo corpus.</span> {DEMO_DATASET_NOTE} The
          live build ingests the Bank of England, ECB, Federal Reserve and SEC feeds directly.
        </div>
      )}

      {error && (
        <div className="mb-4 rounded-lg border border-negative/40 bg-negative/10 px-4 py-3 text-xs text-ink">
          {error}
        </div>
      )}

      <div className="grid gap-4 lg:grid-cols-3">
        <Panel
          className="lg:col-span-2"
          title="Ask the evidence base"
          subtitle="Extractive and deterministic — no language model writes the findings"
        >
          <div className="space-y-3">
            <textarea
              value={query}
              onChange={(event) => setQuery(event.target.value)}
              rows={2}
              placeholder="e.g. What did the committee say about Bank Rate and inflation?"
              className="w-full resize-y rounded-lg border border-line-strong bg-surface-sunken px-3 py-2.5 text-sm text-ink placeholder:text-ink-faint focus:border-accent"
              aria-label="Research question"
            />
            <div className="flex flex-wrap items-center gap-2">
              <Button onClick={runResearch} disabled={!query.trim() || busy} loading={busy}>
                <IconFlask /> Run research
              </Button>
              <Button variant="secondary" onClick={runSearch} loading={searching} disabled={!query.trim()}>
                Show evidence only
              </Button>
              <label className="ml-auto flex items-center gap-2 text-2xs text-ink-faint">
                Instrument
                <select
                  value={instrument}
                  onChange={(event) => setInstrument(event.target.value)}
                  className="rounded-md border border-line-strong bg-surface-sunken px-2 py-1 text-xs text-ink"
                  aria-label="Instrument filter"
                >
                  <option value="">Any</option>
                  <option value="GBP/USD">GBP/USD</option>
                  <option value="EUR/USD">EUR/USD</option>
                  <option value="SPX500">SPX500</option>
                  <option value="AAPL">AAPL</option>
                  <option value="WTI">WTI</option>
                  <option value="XAUUSD">XAUUSD</option>
                </select>
              </label>
            </div>
            <div className="flex flex-wrap gap-1.5">
              {SUGGESTED.map((suggestion) => (
                <button
                  key={suggestion}
                  type="button"
                  onClick={() => setQuery(suggestion)}
                  className="rounded-full border border-line-strong px-2.5 py-1 text-[11px] text-ink-muted transition hover:border-accent hover:text-ink"
                >
                  {suggestion}
                </button>
              ))}
            </div>
          </div>

          <div className="mt-4">
            {report ? (
              <div className="space-y-3">
                <div className="flex flex-wrap items-center gap-2 text-2xs text-ink-muted">
                  <Badge tone={report.claims_supported > 0 ? "positive" : "muted"}>
                    {report.claims_supported}/{report.claims_total} supported
                  </Badge>
                  <span>{report.documents_used} document(s) used</span>
                  <span aria-hidden>·</span>
                  <span>generator: {report.generated_by}</span>
                  <span aria-hidden>·</span>
                  <span>coverage {(report.coverage_score * 100).toFixed(0)}%</span>
                </div>

                {report.claims.length === 0 ? (
                  <EmptyState
                    title="No checkable claims"
                    description="Nothing in the retrieved evidence contained a quantity with a unit. An empty report is the honest output — it is not padded with commentary."
                    icon={<IconFlask width={26} height={26} />}
                  />
                ) : (
                  <ol className="space-y-2">
                    {report.claims.map((claim) => (
                      <li key={claim.id} className="rounded-lg border border-line bg-surface-sunken p-3">
                        <div className="flex items-start justify-between gap-3">
                          <p className="text-sm font-medium text-ink">{claim.text}</p>
                          <Badge tone={VERDICT_TONE[claim.verdict]}>{VERDICT_LABEL[claim.verdict]}</Badge>
                        </div>
                        <p className="mt-1 text-[11px] text-ink-faint">{claim.note}</p>
                        {claim.citations.map((citation) => (
                          <blockquote
                            key={citation.id}
                            className="mt-2 border-l-2 border-accent/50 pl-3 text-[11px] leading-relaxed text-ink-muted"
                          >
                            <span className="italic">“{citation.quote}”</span>
                            <div className="mt-1 flex flex-wrap items-center gap-1.5 text-[10px] text-ink-faint">
                              <a
                                href={citation.url}
                                target="_blank"
                                rel="noreferrer noopener"
                                className="underline decoration-dotted hover:text-ink"
                              >
                                {citation.source_name}
                              </a>
                              <span aria-hidden>·</span>
                              <span>{formatDate(citation.published_at)}</span>
                              <span aria-hidden>·</span>
                              <span>{citation.locator}</span>
                            </div>
                          </blockquote>
                        ))}
                      </li>
                    ))}
                  </ol>
                )}

                {report.statements && report.statements.length > 0 && (
                  <div className="rounded-lg border border-line bg-surface px-3 py-2.5">
                    <p className="text-2xs uppercase tracking-wide text-ink-faint">Facts, interpretation, uncertainty</p>
                    <ul className="mt-1.5 space-y-1.5">
                      {report.statements.map((statement) => (
                        <li key={statement.text} className="flex gap-2 text-[11px] leading-relaxed text-ink-muted">
                          <Badge tone={statement.kind === "contradiction" ? "negative" : statement.kind === "fact" ? "accent" : "muted"}>
                            {statement.kind}
                          </Badge>
                          <span>{statement.text}</span>
                        </li>
                      ))}
                    </ul>
                  </div>
                )}

                <p className="rounded-md border border-line/70 bg-surface-sunken px-3 py-2 text-[11px] leading-relaxed text-ink-faint">
                  <span className="font-medium text-ink-muted">Limitations. </span>
                  {report.limitations}
                </p>
              </div>
            ) : loading ? (
              <div className="py-10"><Spinner label="Loading research data" /></div>
            ) : (
              <EmptyState
                title="No report yet"
                description="Run a question to build a report. Each claim is extracted from a retrieved passage and verified against it — claims that fail verification are shown as unsupported, not hidden."
                icon={<IconFlask width={26} height={26} />}
              />
            )}
          </div>
        </Panel>

        <div className="space-y-4">
          <Panel title="Source register" subtitle="Trust tier and licence are properties of the source">
            {loading ? (
              <div className="py-8"><Spinner label="Loading sources" /></div>
            ) : (
              <ul className="divide-y divide-line/70">
                {sources.map((source) => (
                  <li key={source.id} className="py-2.5 first:pt-0 last:pb-0">
                    <div className="flex items-start justify-between gap-2">
                      <div className="min-w-0">
                        <p className="truncate text-xs font-medium text-ink">{source.name}</p>
                        <p className="text-[10px] text-ink-faint">
                          {source.jurisdiction} · {source.document_count} docs
                        </p>
                      </div>
                      <Badge tone={TIER_TONE[source.trust_tier] ?? "muted"}>{source.trust_tier}</Badge>
                    </div>
                    <p className="mt-1 text-[10px] leading-relaxed text-ink-faint">
                      {source.storage_permitted ? "Body stored and indexed" : "Link and excerpt only — body fetched on demand, never persisted"}
                    </p>
                    {source.consecutive_failures > 0 && (
                      <p className="mt-1 text-[10px] text-caution">{source.last_error}</p>
                    )}
                  </li>
                ))}
              </ul>
            )}
          </Panel>

          <Panel title="Corpus health" subtitle="What the index actually contains">
            {overview ? (
              <dl className="grid grid-cols-2 gap-2 text-xs">
                {[
                  ["Sources", `${overview.sources_enabled}/${overview.sources_total}`],
                  ["Documents", String(overview.documents_total)],
                  ["Chunks", String(overview.chunks_total)],
                  ["Flagged", String(overview.documents_flagged)],
                ].map(([label, value]) => (
                  <div key={label} className="rounded-md border border-line bg-surface-sunken px-2.5 py-2">
                    <dt className="text-[10px] uppercase tracking-wide text-ink-faint">{label}</dt>
                    <dd className="font-mono text-sm text-ink">{value}</dd>
                  </div>
                ))}
              </dl>
            ) : (
              <div className="py-4"><Spinner label="Loading corpus" /></div>
            )}
            <p className="mt-2.5 flex items-start gap-1.5 text-[10px] leading-relaxed text-ink-faint">
              <IconShield width={12} height={12} className="mt-0.5 shrink-0" />
              Every retrieved document is untrusted data. Instruction-like text is flagged and can never cause an action.
            </p>
          </Panel>
        </div>
      </div>

      <div className="mt-4 grid gap-4 lg:grid-cols-2">
        <Panel
          title="Evidence"
          subtitle={hits.length ? `${hits.length} passage(s) ranked for this query` : "Ranked passages behind the report"}
          bodyClassName="p-0"
        >
          {hits.length === 0 ? (
            <div className="p-4">
              <EmptyState
                title="No passages retrieved"
                description="Run a question first, or try a different phrasing. Retrieval is lexical, so exact policy terms work better than paraphrases."
                icon={<IconDoc width={24} height={24} />}
              />
            </div>
          ) : (
            <ul className="divide-y divide-line/70">
              {hits.map((hit) => (
                <li key={hit.chunk_id} className="px-4 py-3">
                  <div className="flex items-start justify-between gap-2">
                    <p className="text-[11px] font-medium text-ink">{hit.title}</p>
                    <Badge tone={TIER_TONE[hit.source_tier] ?? "muted"}>{hit.source_tier}</Badge>
                  </div>
                  <p className="mt-1 line-clamp-3 text-[11px] leading-relaxed text-ink-muted">{hit.text}</p>
                  <div className="mt-1 flex flex-wrap items-center gap-1.5 text-[10px] text-ink-faint">
                    <span className="font-mono">score {hit.score.toFixed(3)}</span>
                    <span aria-hidden>·</span>
                    <span>{formatDate(hit.published_at)}</span>
                    <span aria-hidden>·</span>
                    <span>{hit.locator}</span>
                    {hit.injection_flags > 0 && (
                      <Badge tone="negative">{hit.injection_flags} injection flag(s)</Badge>
                    )}
                  </div>
                </li>
              ))}
            </ul>
          )}
        </Panel>

        <Panel
          title="Ingested documents"
          subtitle="Newest first, with storage status per licence"
          bodyClassName="p-0"
          actions={
            <label className="flex items-center gap-1.5 text-[10px] text-ink-faint">
              <input
                type="checkbox"
                checked={flaggedOnly}
                onChange={(event) => setFlaggedOnly(event.target.checked)}
                className="accent-accent"
              />
              Flagged only
            </label>
          }
        >
          {loading ? (
            <div className="p-4">
              <Spinner label="Loading documents" />
            </div>
          ) : visibleDocs.length === 0 ? (
            <div className="p-4">
              <EmptyState
                title={flaggedOnly ? "No flagged documents" : "No documents ingested"}
                description={
                  flaggedOnly
                    ? "Nothing in the corpus currently contains instruction-like text."
                    : "Sync the sources to populate the corpus."
                }
                icon={<IconDoc width={24} height={24} />}
              />
            </div>
          ) : (
            <ul className="divide-y divide-line/70">
              {visibleDocs.map((doc) => (
                <li key={doc.id} className="px-4 py-3">
                  <div className="flex items-start justify-between gap-2">
                    <a
                      href={doc.url}
                      target="_blank"
                      rel="noreferrer noopener"
                      className="text-[11px] font-medium text-ink underline decoration-dotted hover:text-accent"
                    >
                      {doc.title}
                    </a>
                    <Badge tone={doc.content_stored ? "positive" : "muted"}>
                      {doc.content_stored ? "stored" : "link only"}
                    </Badge>
                  </div>
                  <div className="mt-1 flex flex-wrap items-center gap-1.5 text-[10px] text-ink-faint">
                    <span>{doc.source_name}</span>
                    <span aria-hidden>·</span>
                    <span>{formatDate(doc.published_at)}</span>
                    <span aria-hidden>·</span>
                    <span className="font-mono">{doc.word_count} words</span>
                    {doc.prompt_injection_flags > 0 && (
                      <Badge tone="negative">{doc.injection_detail}</Badge>
                    )}
                  </div>
                </li>
              ))}
            </ul>
          )}
        </Panel>
      </div>
    </>
  );
}
