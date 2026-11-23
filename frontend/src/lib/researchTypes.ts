// Research contracts — mirrors backend `app/schemas/research.py`.

export type TrustTier = "primary" | "official_secondary" | "commentary";
export type ClaimVerdict =
  | "supported"
  | "partially_supported"
  | "unsupported"
  | "contradicted"
  | "unverified";
export type StatementKind = "fact" | "interpretation" | "uncertainty" | "contradiction";

export interface ResearchSource {
  id: string;
  code: string;
  name: string;
  publisher: string;
  kind: string;
  trust_tier: TrustTier;
  homepage: string;
  jurisdiction: string;
  licence: string;
  terms_url: string;
  redistribution: string;
  storage_permitted: boolean;
  is_enabled: boolean;
  last_success_at: string | null;
  consecutive_failures: number;
  last_error: string;
  document_count: number;
}

export interface ResearchDocument {
  id: string;
  source_code: string;
  source_name: string;
  source_tier: string;
  title: string;
  url: string;
  summary: string;
  published_at: string | null;
  retrieved_at: string;
  status: string;
  content_stored: boolean;
  word_count: number;
  prompt_injection_flags: number;
  injection_detail: string;
  untrusted: boolean;
  chunk_count: number;
}

export interface SearchHit {
  chunk_id: string;
  document_id: string;
  text: string;
  score: number;
  source_code: string;
  source_name: string;
  source_tier: string;
  title: string;
  url: string;
  published_at: string | null;
  locator: string;
  page: number | null;
  section: string;
  injection_flags: number;
}

export interface Citation {
  id: string;
  source_code: string;
  source_name: string;
  url: string;
  published_at: string | null;
  quote: string;
  locator: string;
  relevance: number;
}

export interface Claim {
  id: string;
  ordinal: number;
  text: string;
  verdict: ClaimVerdict;
  confidence: number;
  evidence_count: number;
  note: string;
  citations: Citation[];
}

export interface ReportStatement {
  kind: StatementKind;
  text: string;
  confidence: number;
}

export interface ResearchReport {
  id: string;
  title: string;
  topic: string;
  instrument_symbol: string | null;
  query: string;
  generated_by: string;
  status: string;
  documents_used: number;
  claims_total: number;
  claims_supported: number;
  claims_unverified: number;
  contradictions: number;
  coverage_score: number;
  body_markdown: string;
  limitations: string;
  created_at: string;
  claims?: Claim[];
  statements?: ReportStatement[];
}

export interface ResearchOverview {
  sources_total: number;
  sources_enabled: number;
  sources_failing: number;
  documents_total: number;
  documents_untrusted: number;
  documents_flagged: number;
  chunks_total: number;
  reports_total: number;
  latest_documents: ResearchDocument[];
  trust_breakdown: Record<string, number>;
  top_entities: Array<{ kind: string; value: string; confidence: number }>;
}

export interface IngestResponse {
  sources: number;
  documents_new: number;
  results: Array<{
    source: string;
    outcome: string;
    seen: number;
    new: number;
    duplicates: number;
    rejected: number;
    error: string;
  }>;
}
