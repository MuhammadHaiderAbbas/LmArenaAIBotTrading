import { describe, expect, it } from "vitest";
import {
  DEMO_DATASET_NOTE,
  DEMO_DOCUMENTS,
  DEMO_SOURCES,
  buildDemoReport,
  demoResearchApi,
  searchCorpus,
} from "./researchEngine";

describe("demo research corpus", () => {
  it("labels itself as synthetic", () => {
    expect(DEMO_DATASET_NOTE.toLowerCase()).toContain("synthetic");
    expect(DEMO_DATASET_NOTE.toLowerCase()).toContain("fabricated");
  });

  it("every document maps to a declared source", () => {
    const codes = new Set(DEMO_SOURCES.map((source) => source.code));
    for (const doc of DEMO_DOCUMENTS) {
      expect(codes.has(doc.sourceCode)).toBe(true);
    }
  });

  it("declares licence terms for every source", () => {
    for (const source of DEMO_SOURCES) {
      expect(source.licence.length).toBeGreaterThan(5);
      expect(source.terms_url).toMatch(/^https:\/\//);
      expect(["link_only", "excerpt", "full"]).toContain(source.redistribution);
    }
  });

  it("keeps link-only bodies out of storage", () => {
    for (const doc of DEMO_DOCUMENTS) {
      const source = DEMO_SOURCES.find((item) => item.code === doc.sourceCode)!;
      if (!source.storage_permitted) expect(doc.contentStored).toBe(false);
    }
  });

  it("includes a prompt-injection document so the guard is visible", () => {
    const flagged = DEMO_DOCUMENTS.filter((doc) => doc.injection);
    expect(flagged.length).toBeGreaterThan(0);
  });
});

describe("demo retrieval", () => {
  it("returns nothing for an empty query", () => {
    expect(searchCorpus("   ")).toEqual([]);
  });

  it("ranks on-topic passages and keeps off-topic ones out", () => {
    const hits = searchCorpus("Bank Rate inflation");
    expect(hits.length).toBeGreaterThan(0);
    expect(hits.some((hit) => /Bank Rate/i.test(hit.text))).toBe(true);
    expect(hits.some((hit) => /gold/i.test(hit.text))).toBe(false);
  });

  it("is deterministic", () => {
    expect(searchCorpus("Bank Rate inflation")).toEqual(searchCorpus("Bank Rate inflation"));
  });

  it("caps passages per document", () => {
    const hits = searchCorpus("Bank Rate inflation rate committee", { limit: 30 });
    const counts = new Map<string, number>();
    for (const hit of hits) counts.set(hit.document_id, (counts.get(hit.document_id) ?? 0) + 1);
    for (const count of counts.values()) expect(count).toBeLessThanOrEqual(3);
  });

  it("filters by source", () => {
    const hits = searchCorpus("rate inflation", { source: "ecb_press", limit: 20 });
    expect(hits.every((hit) => hit.source_code === "ecb_press")).toBe(true);
  });
});

describe("demo reports", () => {
  it("produces claims with citable passages", () => {
    const report = buildDemoReport({ query: "Bank Rate inflation", instrument_symbol: "GBP/USD" });
    expect(report.claims_total).toBeGreaterThan(0);
    for (const claim of report.claims) {
      expect(claim.citations.length).toBeGreaterThan(0);
      for (const citation of claim.citations) {
        expect(citation.quote.length).toBeGreaterThan(20);
        expect(citation.url).toMatch(/^https:\/\//);
      }
    }
  });

  it("only marks a claim supported when the passage contains it", () => {
    const report = buildDemoReport({ query: "Bank Rate inflation" });
    for (const claim of report.claims) {
      const quote = claim.citations[0].quote.toLowerCase().replace(/percentage points|per cent/g, "%");
      const value = claim.text.split(" ").pop()!.toLowerCase();
      if (claim.verdict === "supported") expect(quote).toContain(value);
    }
  });

  it("never claims support it does not have", () => {
    const report = buildDemoReport({ query: "Bank Rate inflation" });
    expect(report.claims_supported).toBeLessThanOrEqual(report.claims_total);
    expect(report.coverage_score).toBeLessThanOrEqual(1);
    expect(report.coverage_score).toBeGreaterThanOrEqual(0);
  });

  it("surfaces the flagged document as a contradiction-style statement", () => {
    const report = buildDemoReport({ query: "operational resilience third-party providers" });
    expect(report.statements?.some((statement) => statement.kind === "contradiction")).toBe(true);
  });

  it("returns an honestly empty report for an unanswerable query", () => {
    const report = buildDemoReport({ query: "cocoa futures warehouse receipts" });
    expect(report.claims_total).toBe(0);
    expect(report.limitations.toLowerCase()).toContain("no evidence");
    expect(report.body_markdown).toBe("");
  });

  it("states its limitations every time", () => {
    const report = buildDemoReport({ query: "Bank Rate" });
    expect(report.limitations.toLowerCase()).toContain("synthetic");
    expect(report.generated_by).toContain("extractive");
  });
});

describe("demo research api", () => {
  it("reports ingestion as unavailable instead of pretending", async () => {
    const result = await demoResearchApi.ingest();
    expect(result.documents_new).toBe(0);
    expect(result.results.every((row) => row.outcome === "unavailable")).toBe(true);
    expect(result.results[0].error.toLowerCase()).toContain("demo mode");
  });

  it("filters documents by flagged status", async () => {
    const flagged = await demoResearchApi.documents({ flaggedOnly: true });
    expect(flagged.length).toBeGreaterThan(0);
    expect(flagged.every((doc) => doc.prompt_injection_flags > 0)).toBe(true);
  });

  it("counts every document as untrusted in the overview", async () => {
    const overview = await demoResearchApi.overview();
    expect(overview.documents_untrusted).toBe(overview.documents_total);
    expect(overview.sources_failing).toBe(1);
  });
});
