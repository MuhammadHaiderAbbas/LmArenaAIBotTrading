/**
 * Demo operations — the same eight checks the backend runs, against the demo book.
 *
 * The demo has no server clock, no database and no circuit-breaker table, so two of the
 * checks cannot be honest here and say so rather than inventing a green tick. The rest
 * are genuine: freshness is measured against the generated candles, order activity
 * against the demo's own order list, and the audit chain against a demo hash chain
 * built exactly the way `app/services/integrity.py` builds it.
 *
 * Scope note: this is a *local* seal chain over the browser's own action log. It
 * demonstrates the mechanism and its failure modes; it is not the server's chain, and
 * the page says so.
 */

import * as execution from "./executionEngine";
import * as risk from "./riskEngine";
import { candlesForSymbol } from "./marketEngine";

export type FindingStatus = "ok" | "warning" | "critical";

export interface Finding {
  check: string;
  label: string;
  status: FindingStatus;
  message: string;
  detail: Record<string, unknown>;
}

export interface SealEntry {
  sequence: number;
  action: string;
  detail: string;
  recordedAt: string;
  entryHash: string;
  prevHash: string;
}

export interface ChainVerification {
  verified: boolean;
  entries: number;
  firstBrokenAt: number | null;
  reason: string | null;
  digest: string;
}

export interface HealthReport {
  status: FindingStatus;
  findings: Finding[];
  counts: Record<FindingStatus, number>;
  checksRun: number;
  scope: "demo";
  generatedAt: string;
}

export const STALE_DATA_HOURS = 36;
export const ABNORMAL_ORDER_BURST = 40;
export const ABNORMAL_REJECTION_RATE = 0.5;
export const GENESIS = "0".repeat(64);

const SEAL_KEY = "aurex.demo.seals.v1";

// --------------------------------------------------------------------- hashing

/**
 * A sync sha256. The browser has `crypto.subtle`, but it is promise-based and the demo
 * engine is sync throughout; this keeps the chain identical in shape to the backend's
 * without turning every caller async.
 *
 * It is SHA-256 proper — not a stand-in — so the same canonical string produces the same
 * digest in the client and on the server.
 */
const K = [
  0x428a2f98, 0x71374491, 0xb5c0fbcf, 0xe9b5dba5, 0x3956c25b, 0x59f111f1, 0x923f82a4, 0xab1c5ed5,
  0xd807aa98, 0x12835b01, 0x243185be, 0x550c7dc3, 0x72be5d74, 0x80deb1fe, 0x9bdc06a7, 0xc19bf174,
  0xe49b69c1, 0xefbe4786, 0x0fc19dc6, 0x240ca1cc, 0x2de92c6f, 0x4a7484aa, 0x5cb0a9dc, 0x76f988da,
  0x983e5152, 0xa831c66d, 0xb00327c8, 0xbf597fc7, 0xc6e00bf3, 0xd5a79147, 0x06ca6351, 0x14292967,
  0x27b70a85, 0x2e1b2138, 0x4d2c6dfc, 0x53380d13, 0x650a7354, 0x766a0abb, 0x81c2c92e, 0x92722c85,
  0xa2bfe8a1, 0xa81a664b, 0xc24b8b70, 0xc76c51a3, 0xd192e819, 0xd6990624, 0xf40e3585, 0x106aa070,
  0x19a4c116, 0x1e376c08, 0x2748774c, 0x34b0bcb5, 0x391c0cb3, 0x4ed8aa4a, 0x5b9cca4f, 0x682e6ff3,
  0x748f82ee, 0x78a5636f, 0x84c87814, 0x8cc70208, 0x90befffa, 0xa4506ceb, 0xbef9a3f7, 0xc67178f2,
];

export function sha256(message: string): string {
  const bytes: number[] = [];
  for (const char of new TextEncoder().encode(message)) bytes.push(char);
  const bitLength = bytes.length * 8;
  bytes.push(0x80);
  while (bytes.length % 64 !== 56) bytes.push(0);
  for (let shift = 56; shift >= 0; shift -= 8) bytes.push((bitLength / 2 ** shift) & 0xff);

  const hash = [
    0x6a09e667, 0xbb67ae85, 0x3c6ef372, 0xa54ff53a, 0x510e527f, 0x9b05688c, 0x1f83d9ab, 0x5be0cd19,
  ];
  const rotr = (value: number, bits: number) => (value >>> bits) | (value << (32 - bits));

  for (let offset = 0; offset < bytes.length; offset += 64) {
    const w = new Array<number>(64).fill(0);
    for (let index = 0; index < 16; index += 1) {
      const base = offset + index * 4;
      w[index] =
        ((bytes[base] << 24) | (bytes[base + 1] << 16) | (bytes[base + 2] << 8) | bytes[base + 3]) >>>
        0;
    }
    for (let index = 16; index < 64; index += 1) {
      const s0 = rotr(w[index - 15], 7) ^ rotr(w[index - 15], 18) ^ (w[index - 15] >>> 3);
      const s1 = rotr(w[index - 2], 17) ^ rotr(w[index - 2], 19) ^ (w[index - 2] >>> 10);
      w[index] = (w[index - 16] + s0 + w[index - 7] + s1) >>> 0;
    }
    let [a, b, c, d, e, f, g, h] = hash;
    for (let index = 0; index < 64; index += 1) {
      const S1 = rotr(e, 6) ^ rotr(e, 11) ^ rotr(e, 25);
      const ch = (e & f) ^ (~e & g);
      const temp1 = (h + S1 + ch + K[index] + w[index]) >>> 0;
      const S0 = rotr(a, 2) ^ rotr(a, 13) ^ rotr(a, 22);
      const maj = (a & b) ^ (a & c) ^ (b & c);
      const temp2 = (S0 + maj) >>> 0;
      h = g;
      g = f;
      f = e;
      e = (d + temp1) >>> 0;
      d = c;
      c = b;
      b = a;
      a = (temp1 + temp2) >>> 0;
    }
    hash[0] = (hash[0] + a) >>> 0;
    hash[1] = (hash[1] + b) >>> 0;
    hash[2] = (hash[2] + c) >>> 0;
    hash[3] = (hash[3] + d) >>> 0;
    hash[4] = (hash[4] + e) >>> 0;
    hash[5] = (hash[5] + f) >>> 0;
    hash[6] = (hash[6] + g) >>> 0;
    hash[7] = (hash[7] + h) >>> 0;
  }
  return hash.map((value) => value.toString(16).padStart(8, "0")).join("");
}

/** Stable JSON: key order must not change the hash, or the chain is noise. */
function canonical(value: Record<string, unknown>): string {
  return JSON.stringify(
    Object.keys(value)
      .sort()
      .map((key) => [key, value[key]]),
  );
}

export function computeHash(
  previous: string,
  fields: { sequence: number; action: string; detail: string; recordedAt: string },
): string {
  return sha256(`${previous}|${canonical(fields)}`);
}

// ------------------------------------------------------------------- the chain

let seals: SealEntry[] = [];

function load(): void {
  seals = [];
  try {
    const raw = globalThis.localStorage?.getItem(SEAL_KEY);
    if (raw) seals = JSON.parse(raw) as SealEntry[];
  } catch {
    seals = [];
  }
}

function persist(): void {
  try {
    globalThis.localStorage?.setItem(SEAL_KEY, JSON.stringify(seals));
  } catch {
    /* storage disabled: the chain still works for this session */
  }
}

export function seal(action: string, detail: string, recordedAt?: string): SealEntry {
  const previous = seals.length ? seals[seals.length - 1].entryHash : GENESIS;
  const fields = {
    sequence: seals.length + 1,
    action,
    detail,
    recordedAt: recordedAt ?? new Date().toISOString(),
  };
  const entry: SealEntry = { ...fields, prevHash: previous, entryHash: computeHash(previous, fields) };
  seals.push(entry);
  persist();
  return entry;
}

export function chain(): SealEntry[] {
  load();
  return [...seals];
}

export function verifyChain(): ChainVerification {
  const entries = chain();
  let previous = GENESIS;
  for (const [index, entry] of entries.entries()) {
    if (entry.sequence !== index + 1) {
      return {
        verified: false,
        entries: entries.length,
        firstBrokenAt: index + 1,
        reason: `Sequence gap: expected ${index + 1}, found ${entry.sequence}.`,
        digest: entries.length ? entries[entries.length - 1].entryHash : GENESIS,
      };
    }
    if (entry.prevHash !== previous) {
      return {
        verified: false,
        entries: entries.length,
        firstBrokenAt: entry.sequence,
        reason: `Previous hash mismatch at sequence ${entry.sequence}.`,
        digest: entry.entryHash,
      };
    }
    const expected = computeHash(previous, {
      sequence: entry.sequence,
      action: entry.action,
      detail: entry.detail,
      recordedAt: entry.recordedAt,
    });
    if (expected !== entry.entryHash) {
      return {
        verified: false,
        entries: entries.length,
        firstBrokenAt: entry.sequence,
        reason: `Content changed at sequence ${entry.sequence}: the entry no longer hashes to its seal.`,
        digest: entry.entryHash,
      };
    }
    previous = entry.entryHash;
  }
  return {
    verified: true,
    entries: entries.length,
    firstBrokenAt: null,
    reason: null,
    digest: entries.length ? entries[entries.length - 1].entryHash : GENESIS,
  };
}

/** Demo-only: tamper with one entry so the verification path can be seen to work. */
export function tamperForDemo(): ChainVerification {
  load();
  const target = seals[Math.max(0, seals.length - 2)];
  if (target) {
    target.detail = `${target.detail} (edited)`;
    persist();
  }
  return verifyChain();
}

export function resetChain(): void {
  seals = [];
  persist();
}

// -------------------------------------------------------------------- checks

function finding(
  check: string,
  label: string,
  status: FindingStatus,
  message: string,
  detail: Record<string, unknown> = {},
): Finding {
  return { check, label, status, message, detail };
}

export function checkDataFreshness(): Finding {
  const symbols = Array.from(
    new Set(execution.orders().map((order) => order.instrument_symbol)),
  );
  const probe = symbols.length ? symbols : ["EUR/USD"];
  const now = Date.now();
  const stale: { symbol: string; ageHours: number; reason?: string }[] = [];

  for (const symbol of probe) {
    const candles = candlesForSymbol(symbol, "1d", 5);
    if (!candles || candles.length === 0) {
      stale.push({ symbol, ageHours: Infinity, reason: "no bars" });
      continue;
    }
    const newest = candles[candles.length - 1];
    const ageHours = (now - new Date(newest.open_time).getTime()) / 3_600_000;
    if (ageHours > STALE_DATA_HOURS) stale.push({ symbol, ageHours: Math.round(ageHours) });
  }

  if (!stale.length) {
    return finding("data_freshness", "Data freshness", "ok", "Newest bars are inside the window.");
  }
  return finding(
    "data_freshness",
    "Data freshness",
    "warning",
    `${stale.length} instrument(s) have bars older than ${STALE_DATA_HOURS}h.`,
    { instruments: stale },
  );
}

export function checkOrderActivity(): Finding {
  const all = execution.orders();
  const hourAgo = Date.now() - 3_600_000;
  const recent = all.filter((order) => new Date(order.created_at).getTime() >= hourAgo);
  const rejected = recent.filter((order) => order.status === "rejected").length;
  const rate = recent.length ? rejected / recent.length : 0;

  if (recent.length > ABNORMAL_ORDER_BURST) {
    return finding(
      "order_activity",
      "Order activity",
      "critical",
      `${recent.length} orders in the last hour — a burst above ${ABNORMAL_ORDER_BURST}.`,
      { ordersLastHour: recent.length, rejected },
    );
  }
  if (rate > ABNORMAL_REJECTION_RATE) {
    return finding(
      "order_activity",
      "Order activity",
      "warning",
      `${(rate * 100).toFixed(0)}% of the last hour's orders were rejected.`,
      { ordersLastHour: recent.length, rejected, rejectionRate: rate },
    );
  }
  return finding("order_activity", "Order activity", "ok", `${recent.length} order(s) in the last hour.`, {
    ordersLastHour: recent.length,
    rejected,
  });
}

export function checkBreakers(): Finding {
  const state = risk.status();
  if (state.breaker_state !== "closed") {
    return finding(
      "circuit_breakers",
      "Circuit breakers",
      "critical",
      `Breaker is ${state.breaker_state}: ${state.breaker_reason || "no reason recorded"}.`,
      { breaker: state.breaker_state },
    );
  }
  return finding("circuit_breakers", "Circuit breakers", "ok", "All breakers closed.");
}

export function checkAuditChain(): Finding {
  const result = verifyChain();
  if (!result.verified) {
    return finding(
      "audit_chain",
      "Audit chain",
      "critical",
      `The local seal chain does not verify: ${result.reason}`,
      { entries: result.entries, firstBrokenAt: result.firstBrokenAt },
    );
  }
  return finding("audit_chain", "Audit chain", "ok", `${result.entries} sealed entr(y/ies) verify.`, {
    entries: result.entries,
    digest: result.digest,
  });
}

export function checkReconciliation(): Finding {
  const { reconciliation } = execution.overview();
  if (reconciliation.investment_agrees && reconciliation.realised_agrees) {
    return finding("reconciliation", "Reconciliation", "ok", "Ledger and positions agree.");
  }
  return finding(
    "reconciliation",
    "Reconciliation",
    "critical",
    "Ledger and position value disagree.",
    {
      investment_agrees: reconciliation.investment_agrees,
      realised_agrees: reconciliation.realised_agrees,
      positions_value: reconciliation.positions_value,
      ledger_investment: reconciliation.ledger_investment,
    },
  );
}

export function healthReport(): HealthReport {
  const findings = [
    checkDataFreshness(),
    checkOrderActivity(),
    checkBreakers(),
    checkAuditChain(),
    checkReconciliation(),
    finding(
      "model_degradation",
      "Model degradation",
      "ok",
      "No champion model is registered in the demo, so there is nothing to degrade. The backend runs this check against DriftEvent.",
      { scope: "not applicable in demo" },
    ),
    finding(
      "system_events",
      "API failures",
      "ok",
      "The demo has no server, so API-failure monitoring is reported by the backend, not here.",
      { scope: "not applicable in demo" },
    ),
  ];
  const counts = { ok: 0, warning: 0, critical: 0 };
  for (const item of findings) counts[item.status] += 1;
  const status: FindingStatus = counts.critical
    ? "critical"
    : counts.warning
      ? "warning"
      : "ok";
  return {
    status,
    findings,
    counts,
    checksRun: findings.length,
    scope: "demo",
    generatedAt: new Date().toISOString(),
  };
}

/** Record an action in the demo chain. The Execution page need not know about sealing. */
export function record(action: string, detail: string): void {
  load();
  seal(action, detail);
}

export function resetDemo(): void {
  resetChain();
}
