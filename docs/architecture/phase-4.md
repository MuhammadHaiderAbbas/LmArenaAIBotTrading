# Phase 4 — Research AI: ingestion, retrieval and verified reports

Phase 4 turns AUREX AI from a system that knows prices into one that can read
what institutions actually said, and show its working. The design goal was not
"summarise the news". It was:

> Every sentence in a research output must be traceable to a passage a human can
> open and read, and the system must say when it cannot do that.

That constraint drives every decision below.

---

## 1. Sources

Six keyless sources, all recorded with licence and terms in `research_sources`:

| Code | Publisher | Tier | Storage |
| --- | --- | --- | --- |
| `ecb_press` | European Central Bank | primary | link only |
| `boe_news` | Bank of England | primary | link only |
| `fed_press` | Federal Reserve Board | primary | stored and indexed |
| `sec_edgar` | US SEC (via `data.sec.gov`) | primary | stored and indexed |
| `dj_markets` | Dow Jones | commentary | link only |
| `bbc_business` | BBC | commentary | link only |

Trust tiers are ordered `primary > official_secondary > commentary`, and the tier
is carried onto every retrieved chunk and every citation so a reader can see
where a claim came from without opening the report metadata.

SEC is read through `data.sec.gov/submissions/CIK##########.json`. The legacy
`browse-edgar?...&output=atom` endpoint returns HTTP 403 to any user agent, so
it is not used.

### Licence handling is a data property, not a promise

`storage_permitted` decides whether a body may be persisted:

- **Permitted** (US federal material, public domain): body extracted, chunked,
  embedded, stored in `research_chunks`.
- **Not permitted**: only title, link, summary and timestamps are stored. When
  the analysis needs the body, `services/research/bodies.py` fetches it **into
  memory only**. The chunk is given a transient id
  (`transient-<document>-<n>`) that is never written as a foreign key, citations
  carry `chunk_id = NULL`, and the locator is suffixed
  `"(article body, not stored)"`.

The consequence is visible in the UI: the document list shows `stored` or
`link only` per row, and a citation from a link-only source says so.

---

## 2. Ingestion

`services/research/ingestion.py`, one run per source, recorded in
`research_ingest_runs` with counts for seen / new / duplicate / rejected and the
error string if the feed failed. A failing source increments
`consecutive_failures` and surfaces its last error in the UI — no silent gaps.

Order of operations:

1. **Fetch** the feed (RSS 2.0 or Atom). DOCTYPE and ENTITY declarations are
   stripped before parsing: they are the entity-expansion vector, and the strip
   has to consume the bracketed internal subset or the document will not parse.
2. **Reject non-headlines.** Some sites emit navigation labels as feed items.
   "Close dialog" is not a document. `_plausible_title` rejects known chrome
   strings and any title without two real words.
3. **Sanitise** (`extraction.sanitise`): strip script/style/tags, remove
   zero-width and control characters, NFKC normalise. Prompt-injection patterns
   are detected and counted, never executed:
   `instruction_override`, `role_hijack`, `system_prompt_probe`,
   `tool_invocation`, `exfiltration`, `credential_request`.
4. **Deduplicate**: SHA-256 exact hash plus a 32-bit simhash, Hamming distance
   ≤ 6.
5. **Chunk** with section headings and character offsets preserved, so every
   chunk knows where it came from.
6. **Extract entities**: institutions, currencies, instruments, metrics with
   their values.
7. **Embed** and index.

Every document is `untrusted = True`. Nothing in a document can cause an action.

---

## 3. Retrieval

`vector.py` provides a **deterministic 256-dimension hashed lexical embedding**
(blake2b → dimension + sign, sub-linear term frequency × IDF, unit-normalised).
It is not a neural embedding, and the model name is stored on every chunk so the
limitation is auditable rather than implied. Paraphrase recall is weak; exact
policy vocabulary works well. Replacing `embed()` with a real model is a single
function.

`retrieval.search`:

1. Filter (source, instrument, date window, document flags).
2. **Rank first**, then hydrate: fetch the bodies of the top
   `hydrate_limit × 2` documents only when hydration is requested.
   Hydrating in database order fetched arbitrary articles while the relevant
   ones stayed as one-line feed stubs — this was the single largest quality bug
   in the phase.
3. Re-rank the combined text.
4. Apply a relevance floor (chunk or title must share a query token) to keep
   unrelated gold and oil stories out of a rates report.
5. Apply `MAX_CHUNKS_PER_DOCUMENT = 3` with a diversity pass. Without it, one
   4,500-word MPC minute occupied all fourteen slots, which looks like
   corroboration and is not.
6. If the instrument filter yields fewer than three hits, retry without it and
   prepend an explicit limitation line instead of returning nothing.

Sentence splitting masks decimal points and tolerates a missing space after a
full stop — central-bank pages routinely emit `…(Box C).Consistent with…`.

---

## 4. Reports

`reports.py` builds an **extractive, deterministic** report. No language model
writes prose in this phase. `generated_by` says `extractive-v1`, and the UI shows
it.

For each candidate passage:

1. Extract a sentence, then a single checkable assertion from it: a subject, a
   value and **a unit**. A bare number is not evidence — thousands separators
   produced "Gold settled 1", regex fragments produced "March 2023 m". Both are
   rejected, because a fragment that looks like a finding is worse than nothing.
2. Verify the assertion against the cited passage with a lexical entailment
   test. Unit spellings are treated as equivalent (`percentage points` vs
   `percent`) since the quantity has already been matched.
3. Assign a verdict: `supported`, `partially_supported`, `unsupported`,
   `contradicted`, `unverified`.

Failed claims are **shown, not dropped**. A report with one unsupported claim
tells a reader more than a report with no claims.

Reports keep `ReportClaim` (each with citation spans) separate from
`ReportStatement` (`fact`, `interpretation`, `uncertainty`, `contradiction`).
The `limitations` text is mandatory and is rendered in the UI.

An empty result is a valid output: *"No checkable claims were found in the
retrieved evidence. An empty report is the honest output here."*

---

## 5. API

Twelve paths under `/api/v1/research`:

| Method | Path | Notes |
| --- | --- | --- |
| GET | `/sources` | licence, terms, tier, failure state |
| POST | `/sources/seed` | staff |
| GET | `/overview` | corpus counts, flag counts, top entities |
| GET | `/documents` | filters: source, instrument, flagged_only |
| GET | `/documents/{id}` | includes chunks and sanitisation detail |
| GET | `/documents/{id}/chunks` | offsets, section, embedding model |
| GET | `/search` | ranked hits with locator and tier |
| GET | `/reports` | list |
| POST | `/reports` | builds a report from a query |
| GET | `/reports/{id}` | claims, citations, statements |
| GET | `/inspection/scan` | shows which sanitiser patterns fire on given text |
| POST | `/ingest` | staff, rate-limited |

`/inspection/scan` exists so an operator can see *why* a document was flagged
instead of trusting a boolean.

---

## 6. Schema

Alembic `2aa5b5092d0b` adds nine tables (30 total):

`research_sources`, `research_documents`, `research_chunks`, `document_entities`,
`research_ingest_runs`, `research_reports`, `report_claims`, `report_statements`,
`report_citations`.

`research_chunks.embedding` stores the vector as comma-separated floats with
`embedding_model` beside it. On Postgres this column becomes `vector(256)` with
pgvector; the service layer does not change.

---

## 7. Frontend

The Research Lab screen (`/research`) shows:

- the **source register** with tier, jurisdiction and licence handling per
  source, including the failing source's actual error;
- **corpus health** (sources, documents, chunks, flagged);
- the **report**: each claim with its verdict badge, the quoted passage, the
  source link, the date and the locator;
- a **facts / interpretation / uncertainty / contradiction** split;
- the **evidence list** with retrieval scores and injection flags;
- the **document list** with `stored` vs `link only` per row.

The static demo runs the same screen against a **synthetic corpus of 12
fabricated documents**, labelled as such in the page banner and in every report's
limitations. A demo that showed plausible-looking fabricated central-bank figures
without saying so would be exactly the failure mode this phase exists to prevent.

---

## 8. What this phase does not do

- **No neural embeddings.** Paraphrase retrieval is weak. Documented, not hidden.
- **No LLM generation.** Interpretation is labelled and comes from templates,
  not a model.
- **No strategy consumption of reports.** Reports are `draft` and are not wired
  to any trading logic. That is Phase 5/6 work, and it needs a validation
  workflow first.
- **No PDF table extraction.** ECB press releases that are PDFs are linked, not
  parsed.
- **No authenticated-paywall sources.** Only material whose terms permit this
  use.

---

## 9. Tests

63 Phase 4 tests (140 backend total, 57 frontend):

- `tests/test_research.py` — feed parsing incl. Atom and hostile DOCTYPE,
  injection detection and false positives, chunk offsets and word boundaries,
  simhash near-duplicates, entity extraction, dedupe, chrome rejection,
  link-only storage, retrieval relevance/diversity/determinism, claim
  extraction rejection cases, report verdicts, listing-page rejection, SEC CIK
  parsing, vector determinism, registry idempotency and licence completeness.
- `tests/test_research_api.py` — auth and staff gating, ingest, search, report
  creation, rate limiting, 404s, sanitiser inspection.
- `src/lib/demo/researchEngine.test.ts` — corpus labelling, licence invariants,
  retrieval behaviour, claim verification symmetry.
- `src/pages/Research.test.tsx` — the screen renders, runs research and shows
  quoted citations, shows the flagged document, and reports ingestion honestly
  as unavailable.
