// Transport layer.
//
// In a normal build this talks to the FastAPI backend over HTTP. In the GitHub
// Pages demo build (`vite build --mode demo`, or VITE_DEMO_MODE=true) the same
// interface is served by the in-browser demo engine so the product can be
// reviewed without a server.

import type {
  AccountDetail,
  AuditLog,
  Integration,
  LedgerAccount,
  LedgerStatement,
  Notification,
  Reconciliation,
  SystemMeta,
  SystemStatus,
  TokenPair,
  User,
  UserRole,
} from "../lib/types";
import { DemoApiError, demoApi } from "../lib/demo/engine";
import { demoMarketApi } from "../lib/demo/marketEngine";
import { demoResearchApi } from "../lib/demo/researchEngine";
import type {
  IngestResponse,
  ResearchDocument,
  ResearchOverview,
  ResearchReport,
  ResearchSource,
  Claim,
  SearchHit,
} from "../lib/researchTypes";
import type {
  CandleSeries,
  Instrument,
  MarketRow,
  ProviderHealth,
  QualitySummary,
  SyncResponse,
  Timeframe,
} from "../lib/marketTypes";

const ACCESS_KEY = "aurex.access";
const REFRESH_KEY = "aurex.refresh";

export const IS_DEMO = __DEMO_MODE__ || import.meta.env.VITE_DEMO_MODE === "true";
const API_BASE = import.meta.env.VITE_API_BASE_URL ?? "/api/v1";

export class ApiError extends Error {
  status: number;
  fields?: Record<string, string>;

  constructor(status: number, message: string, fields?: Record<string, string>) {
    super(message);
    this.status = status;
    this.fields = fields;
  }
}

export const tokenStore = {
  get access(): string | null {
    try {
      return localStorage.getItem(ACCESS_KEY);
    } catch {
      return null;
    }
  },
  get refresh(): string | null {
    try {
      return localStorage.getItem(REFRESH_KEY);
    } catch {
      return null;
    }
  },
  set(tokens: TokenPair): void {
    try {
      localStorage.setItem(ACCESS_KEY, tokens.access_token);
      localStorage.setItem(REFRESH_KEY, tokens.refresh_token);
    } catch {
      /* ignore */
    }
  },
  clear(): void {
    try {
      localStorage.removeItem(ACCESS_KEY);
      localStorage.removeItem(REFRESH_KEY);
      localStorage.removeItem("aurex.demo.session");
    } catch {
      /* ignore */
    }
  },
};

function authHeader(): Record<string, string> {
  const token = tokenStore.access;
  return token ? { Authorization: `Bearer ${token}` } : {};
}

async function http<T>(path: string, init: RequestInit = {}): Promise<T> {
  const response = await fetch(`${API_BASE}${path}`, {
    ...init,
    headers: {
      "Content-Type": "application/json",
      ...authHeader(),
      ...(init.headers ?? {}),
    },
  });

  if (response.status === 204) return undefined as T;

  let body: unknown = null;
  try {
    body = await response.json();
  } catch {
    body = null;
  }

  if (!response.ok) {
    const detail = (body as { detail?: string })?.detail ?? "Something went wrong. Please try again.";
    throw new ApiError(response.status, String(detail));
  }
  return body as T;
}

function demoSessionToken(): string {
  return tokenStore.access ?? "";
}

function wrapDemo<T>(fn: () => Promise<T>): Promise<T> {
  return fn().catch((error: unknown) => {
    if (error instanceof DemoApiError) throw new ApiError(error.status, error.message, error.fields);
    throw error;
  });
}

// ---------------------------------------------------------------------------
// Public client — one method per backend endpoint.
// ---------------------------------------------------------------------------
export const api = {
  isDemo: IS_DEMO,

  async meta(): Promise<SystemMeta> {
    if (IS_DEMO) return demoApi.meta();
    return http<SystemMeta>("/system/meta");
  },

  async health(): Promise<{ status: string; database: string }> {
    if (IS_DEMO) return { status: "ok", database: "demo" };
    return http<{ status: string; database: string }>("/system/health");
  },

  async register(input: { email: string; full_name: string; password: string }): Promise<User> {
    if (IS_DEMO) return wrapDemo(() => demoApi.register(input));
    return http<User>("/auth/register", { method: "POST", body: JSON.stringify(input) });
  },

  async login(input: { email: string; password: string }): Promise<TokenPair> {
    if (IS_DEMO) {
      const tokens = await wrapDemo(() => demoApi.login(input));
      tokenStore.set(tokens);
      return tokens;
    }
    const tokens = await http<TokenPair>("/auth/login", {
      method: "POST",
      body: JSON.stringify(input),
    });
    tokenStore.set(tokens);
    return tokens;
  },

  async logout(): Promise<void> {
    const refresh = tokenStore.refresh;
    if (IS_DEMO) {
      await demoApi.logout();
      tokenStore.clear();
      return;
    }
    try {
      await http("/auth/logout", { method: "POST", body: JSON.stringify({ refresh_token: refresh }) });
    } finally {
      tokenStore.clear();
    }
  },

  async me(): Promise<User> {
    if (IS_DEMO) return wrapDemo(() => demoApi.me(demoSessionToken()));
    return http<User>("/auth/me");
  },

  async updateProfile(patch: Partial<NonNullable<User["preferences"]>> & { full_name?: string }): Promise<User> {
    if (IS_DEMO) return wrapDemo(() => demoApi.updateProfile(demoSessionToken(), patch));
    return http<User>("/users/me", { method: "PATCH", body: JSON.stringify(patch) });
  },

  async changePassword(current: string, next: string): Promise<void> {
    if (IS_DEMO) return wrapDemo(() => demoApi.changePassword(demoSessionToken(), current, next));
    await http("/users/me/password", {
      method: "POST",
      body: JSON.stringify({ current_password: current, new_password: next }),
    });
  },

  async listAccounts(): Promise<AccountDetail[]> {
    if (IS_DEMO) return wrapDemo(() => demoApi.listAccounts(demoSessionToken()));
    const accounts = await http<Array<Omit<AccountDetail, "balances" | "open_positions">>>("/accounts");
    // The list endpoint omits balances; hydrate each account in parallel.
    return Promise.all(accounts.map((a) => http<AccountDetail>(`/accounts/${a.id}`)));
  },

  async getAccount(id: string): Promise<AccountDetail> {
    if (IS_DEMO) return wrapDemo(() => demoApi.getAccount(demoSessionToken(), id));
    return http<AccountDetail>(`/accounts/${id}`);
  },

  async createAccount(input: {
    name: string;
    base_currency: string;
    opening_balance: number;
  }): Promise<AccountDetail> {
    if (IS_DEMO) return wrapDemo(() => demoApi.createAccount(demoSessionToken(), input));
    return http<AccountDetail>("/accounts", { method: "POST", body: JSON.stringify(input) });
  },

  async deposit(accountId: string, amount: number, description = ""): Promise<AccountDetail> {
    if (IS_DEMO) return wrapDemo(() => demoApi.deposit(demoSessionToken(), accountId, amount, description));
    return http<AccountDetail>(`/accounts/${accountId}/deposit`, {
      method: "POST",
      body: JSON.stringify({ amount, description }),
    });
  },

  async withdraw(accountId: string, amount: number, description = ""): Promise<AccountDetail> {
    if (IS_DEMO) return wrapDemo(() => demoApi.withdraw(demoSessionToken(), accountId, amount, description));
    return http<AccountDetail>(`/accounts/${accountId}/withdraw`, {
      method: "POST",
      body: JSON.stringify({ amount, description }),
    });
  },

  async ledger(accountId: string, limit = 50): Promise<LedgerStatement> {
    if (IS_DEMO) return wrapDemo(() => demoApi.ledger(demoSessionToken(), accountId, limit));
    return http<LedgerStatement>(`/accounts/${accountId}/ledger?limit=${limit}`);
  },

  async ledgerAccounts(accountId: string): Promise<LedgerAccount[]> {
    if (IS_DEMO) return wrapDemo(() => demoApi.ledgerAccounts(demoSessionToken(), accountId));
    return http<LedgerAccount[]>(`/accounts/${accountId}/ledger/accounts`);
  },

  async reconcile(accountId: string): Promise<Reconciliation> {
    if (IS_DEMO) return wrapDemo(() => demoApi.reconcile(demoSessionToken(), accountId));
    return http<Reconciliation>(`/accounts/${accountId}/reconcile`, { method: "POST" });
  },

  async auditLogs(limit = 100): Promise<AuditLog[]> {
    if (IS_DEMO) return wrapDemo(() => demoApi.auditLogs(demoSessionToken(), limit));
    return http<AuditLog[]>(`/admin/audit-logs?limit=${limit}`);
  },

  async listUsers(): Promise<User[]> {
    if (IS_DEMO) return wrapDemo(() => demoApi.listUsers(demoSessionToken()));
    return http<User[]>("/admin/users");
  },

  async updateUserRole(userId: string, role: UserRole, isActive?: boolean): Promise<User> {
    if (IS_DEMO) return wrapDemo(() => demoApi.updateUserRole(demoSessionToken(), userId, role, isActive));
    return http<User>(`/admin/users/${userId}`, {
      method: "PATCH",
      body: JSON.stringify({ role, is_active: isActive }),
    });
  },

  async integrations(): Promise<Integration[]> {
    if (IS_DEMO) return wrapDemo(() => demoApi.integrations(demoSessionToken()));
    return http<Integration[]>("/admin/integrations");
  },

  async notifications(): Promise<Notification[]> {
    if (IS_DEMO) return wrapDemo(() => demoApi.notifications());
    return http<Notification[]>("/notifications");
  },

  async systemStatus(): Promise<SystemStatus> {
    if (IS_DEMO) return wrapDemo(() => demoApi.systemStatus(demoSessionToken()));
    return http<SystemStatus>("/admin/system-status");
  },

  // ------------------------------------------------------------------ markets

  async marketInstruments(): Promise<Instrument[]> {
    if (IS_DEMO) return wrapDemo(() => demoMarketApi.instruments());
    return http<Instrument[]>("/markets/instruments");
  },

  async marketOverview(): Promise<MarketRow[]> {
    if (IS_DEMO) return wrapDemo(() => demoMarketApi.overview());
    return http<MarketRow[]>("/markets/overview");
  },

  async candles(symbol: string, timeframe: Timeframe = "1d", limit = 180): Promise<CandleSeries> {
    if (IS_DEMO) return wrapDemo(() => demoMarketApi.candles(symbol, timeframe, limit));
    // '/' is legal in our symbols (BTC/USD), so encode it for the path.
    return http<CandleSeries>(
      `/markets/${encodeURIComponent(symbol)}/candles?timeframe=${timeframe}&limit=${limit}`,
    );
  },

  async marketQuality(): Promise<QualitySummary> {
    if (IS_DEMO) return wrapDemo(() => demoMarketApi.qualitySummary());
    return http<QualitySummary>("/markets/quality/summary");
  },

  async marketProviders(): Promise<ProviderHealth[]> {
    if (IS_DEMO) return wrapDemo(() => demoMarketApi.providers());
    return http<ProviderHealth[]>("/markets/providers");
  },

  // ------------------------------------------------------------------ research

  async researchSources(): Promise<ResearchSource[]> {
    if (IS_DEMO) return wrapDemo(() => demoResearchApi.sources());
    return http<ResearchSource[]>("/research/sources");
  },

  async researchOverview(): Promise<ResearchOverview> {
    if (IS_DEMO) return wrapDemo(() => demoResearchApi.overview());
    return http<ResearchOverview>("/research/overview");
  },

  async researchDocuments(params: { source?: string; instrument?: string; flaggedOnly?: boolean; limit?: number } = {}): Promise<ResearchDocument[]> {
    if (IS_DEMO) return wrapDemo(() => demoResearchApi.documents(params));
    const query = new URLSearchParams();
    if (params.source) query.set("source", params.source);
    if (params.instrument) query.set("instrument", params.instrument);
    if (params.flaggedOnly) query.set("flagged_only", "true");
    query.set("limit", String(params.limit ?? 40));
    return http<ResearchDocument[]>(`/research/documents?${query}`);
  },

  async researchSearch(q: string, params: { source?: string; instrument?: string; days?: number } = {}): Promise<SearchHit[]> {
    if (IS_DEMO) return wrapDemo(() => demoResearchApi.search(q, params));
    const query = new URLSearchParams({ q });
    if (params.source) query.set("source", params.source);
    if (params.instrument) query.set("instrument", params.instrument);
    if (params.days) query.set("days", String(params.days));
    const body = await http<{ hits: SearchHit[] }>(`/research/search?${query}`);
    return body.hits;
  },

  async researchReports(): Promise<ResearchReport[]> {
    if (IS_DEMO) return wrapDemo(() => demoResearchApi.reports());
    return http<ResearchReport[]>("/research/reports");
  },

  async researchReport(id: string): Promise<ResearchReport & { claims: Claim[] }> {
    if (IS_DEMO) return wrapDemo(() => demoResearchApi.report(id));
    return http<ResearchReport & { claims: Claim[] }>(`/research/reports/${id}`);
  },

  async createResearchReport(input: {
    query: string;
    topic?: string;
    instrument_symbol?: string | null;
    days?: number;
  }): Promise<ResearchReport & { claims: Claim[] }> {
    if (IS_DEMO) return wrapDemo(() => demoResearchApi.createReport(input));
    return http<ResearchReport & { claims: Claim[] }>("/research/reports", {
      method: "POST",
      body: JSON.stringify(input),
    });
  },

  async ingestResearch(sources: string[] = []): Promise<IngestResponse> {
    if (IS_DEMO) return wrapDemo(() => demoResearchApi.ingest(sources));
    return http<IngestResponse>("/research/ingest", {
      method: "POST",
      body: JSON.stringify({ sources, limit_per_source: 25 }),
    });
  },

  async syncMarkets(symbols: string[], timeframe: Timeframe = "1d"): Promise<SyncResponse> {
    if (IS_DEMO) return wrapDemo(() => demoMarketApi.sync(symbols, timeframe));
    return http<SyncResponse>("/markets/sync", {
      method: "POST",
      body: JSON.stringify({ symbols, timeframe, limit: 180, with_quotes: true }),
    });
  },
};
