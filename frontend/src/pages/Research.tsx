import { useRef, useState } from "react";
import { Badge, Button, EmptyState, Panel, PageHeader, useToast } from "../components/ui";
import { agents } from "../lib/demoStatic";
import { IconDoc, IconFlask } from "../lib/icons";

interface UploadedDoc {
  name: string;
  size: number;
  at: string;
}

export function Research() {
  const [docs, setDocs] = useState<UploadedDoc[]>([]);
  const [question, setQuestion] = useState("");
  const inputRef = useRef<HTMLInputElement>(null);
  const toast = useToast();

  function handleFiles(files: FileList | null) {
    if (!files?.length) return;
    const accepted = Array.from(files).map((file) => ({
      name: file.name,
      size: file.size,
      at: new Date().toISOString(),
    }));
    setDocs((current) => [...accepted, ...current]);
    toast({
      tone: "info",
      title: "Files queued locally",
      description: "Document extraction, OCR and vector indexing arrive in Phase 4.",
    });
  }

  return (
    <>
      <PageHeader
        eyebrow="Evidence first"
        title="AI Research Lab"
        description="Ingest reports, extract verified facts and produce source-linked findings. Verified facts are kept separate from model interpretation."
        actions={<Badge tone="muted">Phase 4 activates analysis</Badge>}
      />

      <div className="grid gap-4 lg:grid-cols-3">
        <Panel className="lg:col-span-2" title="Research assistant" subtitle="Ask a question about the market or a document">
          <div className="space-y-3">
            <textarea
              value={question}
              onChange={(event) => setQuestion(event.target.value)}
              rows={3}
              placeholder="e.g. Summarise the risk factors disclosed in the latest annual report and flag anything that contradicts guidance."
              className="w-full resize-y rounded-lg border border-line-strong bg-surface-sunken px-3 py-2.5 text-sm text-ink placeholder:text-ink-faint focus:border-accent"
              aria-label="Research question"
            />
            <div className="flex flex-wrap items-center gap-2">
              <Button
                disabled={!question.trim()}
                onClick={() =>
                  toast({
                    tone: "info",
                    title: "Research pipeline not enabled",
                    description: "Retrieval, citation verification and report generation ship in Phase 4.",
                  })
                }
              >
                <IconFlask /> Run research
              </Button>
              <Button variant="secondary" onClick={() => inputRef.current?.click()}>
                <IconDoc /> Upload report
              </Button>
              <input
                ref={inputRef}
                type="file"
                accept=".pdf,.txt,.md,.csv,.xlsx"
                multiple
                className="sr-only"
                onChange={(event) => handleFiles(event.target.files)}
                aria-label="Upload research documents"
              />
              <span className="text-2xs text-ink-faint">PDF, TXT, MD, CSV, XLSX</span>
            </div>
          </div>

          <div className="mt-4">
            {docs.length === 0 ? (
              <EmptyState
                title="No documents ingested"
                description="Uploads are held in this session only. Phase 4 adds text and table extraction, OCR for scanned filings, entity tagging and vector indexing with page-level citations."
                icon={<IconDoc width={26} height={26} />}
              />
            ) : (
              <table className="table-base">
                <thead>
                  <tr>
                    <th>Document</th>
                    <th className="text-right">Size</th>
                    <th className="text-right">Status</th>
                  </tr>
                </thead>
                <tbody>
                  {docs.map((doc) => (
                    <tr key={`${doc.name}-${doc.at}`}>
                      <td className="text-xs">{doc.name}</td>
                      <td className="cell-num text-right text-ink-muted">{(doc.size / 1024).toFixed(0)} KB</td>
                      <td className="text-right">
                        <Badge tone="caution">Awaiting pipeline</Badge>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </div>
        </Panel>

        <Panel title="Analyst agents" subtitle="Specialists produce structured findings" bodyClassName="p-0">
          <ul className="divide-y divide-line/70">
            {agents.filter((a) => a.id !== "risk" && a.id !== "orchestrator").map((agent) => (
              <li key={agent.id} className="px-4 py-3">
                <div className="flex items-start justify-between gap-2">
                  <p className="text-xs font-medium text-ink">{agent.name}</p>
                  <Badge tone="muted">P{agent.phase}</Badge>
                </div>
                <p className="mt-1 text-[11px] leading-relaxed text-ink-faint">{agent.description}</p>
              </li>
            ))}
          </ul>
        </Panel>
      </div>

      <div className="mt-4 grid gap-4 lg:grid-cols-2">
        <Panel title="Research pipeline" subtitle="Ten steps from source to validated report">
          <ol className="space-y-2 text-xs text-ink-muted">
            {[
              "Ingest source with URL, publisher, publication and retrieval time",
              "Extract text and tables; OCR scanned documents where permitted",
              "Classify the document; identify entities, dates and reporting periods",
              "Chunk content preserving page and section references",
              "Index text and embeddings with source metadata",
              "Run specialist agents over the retrieved evidence",
              "Verify extracted facts against source passages",
              "Separate facts, interpretation, uncertainty and contradictions",
              "Record evidence freshness and source quality",
              "Expose a report to strategies only through a validation workflow",
            ].map((step, index) => (
              <li key={step} className="flex gap-2.5">
                <span className="mt-0.5 flex h-4 w-4 shrink-0 items-center justify-center rounded-full border border-line-strong font-mono text-[9px] text-ink-faint">
                  {index + 1}
                </span>
                <span className="leading-relaxed">{step}</span>
              </li>
            ))}
          </ol>
        </Panel>

        <Panel title="Trust model" subtitle="How the lab avoids confident nonsense">
          <ul className="space-y-2.5 text-xs">
            {[
              ["Every claim is cited", "Findings link to dated source passages with page references."],
              ["Facts vs interpretation", "Model-generated interpretation is labelled, never merged with reported figures."],
              ["Contradictions surfaced", "Conflicting evidence is displayed, not averaged away."],
              ["Untrusted content", "Retrieved documents are data, never executable instructions."],
              ["Licensing respected", "Provider terms, copyright and entitlements are enforced at ingestion."],
              ["Freshness tracked", "Stale evidence is flagged before it can influence a strategy."],
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
