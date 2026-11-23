// In-browser demo backend.
//
// GitHub Pages can only serve static files, so the demo build ships a small
// client-side implementation of the same contracts the FastAPI service exposes:
// registration, login, paper accounts, a genuine double-entry ledger, audit
// trail and admin reporting. It persists to localStorage so the demo survives a
// refresh. It is NOT a security boundary and holds no real money.

import type {
  AccountDetail,
  AuditLog,
  Balance,
  Integration,
  LedgerAccount,
  LedgerStatement,
  LedgerTransaction,
  Notification,
  Reconciliation,
  SystemMeta,
  SystemStatus,
  TokenPair,

  TransactionType,
  User,
  UserRole,
} from "../types";
import { DEMO_AGENTS, DEMO_CREDENTIALS, DEMO_MARKETS, DEMO_PHASES } from "./seed";

const STORAGE_KEY = "aurex.demo.v1";
const SESSION_KEY = "aurex.demo.session";
const BALANCE_SEED = 100_000;

type Kind = "cash" | "investment" | "opening_equity" | "withdrawal_clearing" | "fee_expense" | "realised_pnl";

interface StoredEntry {
  id: string;
  ledger_account_id: string;
  amount: number;
  currency: string;
}

interface StoredTransaction {
  id: string;
  account_id: string;
  transaction_type: TransactionType;
  reference: string | null;
  description: string;
  occurred_at: string;
  created_at: string;
  entries: StoredEntry[];
  idempotency_key: string | null;
}

interface StoredAccount {
  id: string;
  user_id: string;
  name: string;
  environment: "paper";
  status: "active" | "suspended" | "closed";
  base_currency: string;
  leverage_enabled: boolean;
  created_at: string;
  ledger_accounts: Record<Kind, string>;
  reserved: number;
}

interface StoredUser {
  id: string;
  email: string;
  full_name: string;
  password: string;
  role: UserRole;
  is_active: boolean;
  is_verified: boolean;
  mfa_enabled: boolean;
  last_login_at: string | null;
  created_at: string;
  preferences: {
    theme: string;
    base_currency: string;
    timezone: string;
    email_alerts: boolean;
    risk_alerts: boolean;
  };
}

interface StoredAudit {
  id: string;
  actor_id: string | null;
  actor_email: string | null;
  action: string;
  entity_type: string | null;
  entity_id: string | null;
  outcome: string;
  ip_address: string | null;
  detail: string;
  created_at: string;
}

interface DemoState {
  users: StoredUser[];
  accounts: StoredAccount[];
  transactions: StoredTransaction[];
  audit: StoredAudit[];
  notifications: Notification[];
  integrations: Integration[];
}

export class DemoApiError extends Error {
  status: number;
  fields?: Record<string, string>;

  constructor(status: number, message: string, fields?: Record<string, string>) {
    super(message);
    this.status = status;
    this.fields = fields;
  }
}

const uid = (): string =>
  typeof crypto !== "undefined" && "randomUUID" in crypto
    ? crypto.randomUUID()
    : `id-${Math.random().toString(36).slice(2)}-${Date.now().toString(36)}`;

const now = (): string => new Date().toISOString();
const round = (value: number): number => Math.round(value * 1e8) / 1e8;

const LEDGER_KINDS: Kind[] = [
  "cash",
  "investment",
  "opening_equity",
  "withdrawal_clearing",
  "fee_expense",
  "realised_pnl",
];

const ACCOUNT_TYPE: Record<Kind, LedgerAccount["account_type"]> = {
  cash: "asset",
  investment: "asset",
  opening_equity: "equity",
  withdrawal_clearing: "liability",
  fee_expense: "expense",
  realised_pnl: "income",
};

function createState(): DemoState {
  return {
    users: [],
    accounts: [],
    transactions: [],
    audit: [],
    notifications: [
      {
        id: uid(),
        title: "Phase 1 foundation is live",
        body: "Authentication, the paper ledger and the terminal shell are deployed. Market data arrives in Phase 3.",
        category: "release",
        is_read: false,
        created_at: now(),
      },
      {
        id: uid(),
        title: "Live trading is disabled",
        body: "This environment is paper-only. No broker connection exists and no real funds can move.",
        category: "risk",
        is_read: false,
        created_at: now(),
      },
    ],
    integrations: [
      {
        provider: "market_data",
        category: "market_data",
        status: "not_configured",
        latency_ms: null,
        message: "Market data provider connects in Phase 3",
      },
      {
        provider: "news_feed",
        category: "research",
        status: "not_configured",
        latency_ms: null,
        message: "News and events feed connects in Phase 4",
      },
      {
        provider: "llm_provider",
        category: "ai",
        status: "not_configured",
        latency_ms: null,
        message: "LLM research provider connects in Phase 4",
      },
      {
        provider: "broker_adapter",
        category: "execution",
        status: "not_configured",
        latency_ms: null,
        message: "Broker adapter connects in Phase 8 (sandbox only)",
      },
      {
        provider: "payment_provider",
        category: "payments",
        status: "not_configured",
        latency_ms: null,
        message: "Payment gateway disabled — Phase 1 cannot move real money",
      },
    ],
  };
}

// -------------------------------------------------------------------------
// Persistence
// -------------------------------------------------------------------------
function load(): DemoState {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (raw) return JSON.parse(raw) as DemoState;
  } catch {
    /* corrupted storage — fall through to a fresh state */
  }
  const state = createState();
  seed(state);
  save(state);
  return state;
}

function save(state: DemoState): void {
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(state));
  } catch {
    /* storage full or blocked: the demo still works in-memory */
  }
}

function seed(state: DemoState): void {
  const admin = makeUser({
    email: "admin@aurex.ai",
    full_name: "AUREX Administrator",
    password: "ChangeMe!2026",
    role: "admin",
  });
  const demo = makeUser({
    email: DEMO_CREDENTIALS.email,
    full_name: "Demo Trader",
    password: DEMO_CREDENTIALS.password,
    role: "user",
  });
  state.users.push(admin, demo);
  openAccount(state, demo.id, "Paper Trading Account", "GBP", BALANCE_SEED, null);
  openAccount(state, demo.id, "Strategy Sandbox", "USD", 25_000, null);

  // A week of illustrative virtual funding so the ledger and statement views
  // have realistic content on first load.
  const primary = state.accounts.find((a) => a.user_id === demo.id)!;
  withTransaction(state, {
    account: primary,
    type: "deposit",
    lines: [
      ["cash", 5000],
      ["opening_equity", -5000],
    ],
    description: "Virtual top-up · research budget",
    at: daysAgo(6),
    actor: demo.id,
  });
  withTransaction(state, {
    account: primary,
    type: "withdrawal",
    lines: [
      ["withdrawal_clearing", 1250],
      ["cash", -1250],
    ],
    description: "Virtual withdrawal · rebalance to sandbox",
    at: daysAgo(2),
    actor: demo.id,
  });

  audit(state, {
    actor_id: demo.id,
    actor_email: demo.email,
    action: "account.create",
    entity_type: "trading_account",
    entity_id: primary.id,
    detail: "demo seeding",
  });
}

function daysAgo(days: number): string {
  return new Date(Date.now() - days * 86_400_000).toISOString();
}

function makeUser(input: {
  email: string;
  full_name: string;
  password: string;
  role: UserRole;
}): StoredUser {
  return {
    id: uid(),
    email: input.email.toLowerCase(),
    full_name: input.full_name,
    password: input.password,
    role: input.role,
    is_active: true,
    is_verified: true,
    mfa_enabled: false,
    last_login_at: null,
    created_at: now(),
    preferences: {
      theme: "dark",
      base_currency: "GBP",
      timezone: "Europe/London",
      email_alerts: true,
      risk_alerts: true,
    },
  };
}

function audit(state: DemoState, entry: Omit<StoredAudit, "id" | "created_at" | "outcome" | "ip_address"> & { outcome?: string }): void {
  state.audit.unshift({
    id: uid(),
    outcome: entry.outcome ?? "success",
    ip_address: "127.0.0.1 (demo)",
    created_at: now(),
    ...entry,
  });
}

// -------------------------------------------------------------------------
// Ledger primitives (mirrors backend/app/services/ledger_service.py)
// -------------------------------------------------------------------------
function openAccount(
  state: DemoState,
  userId: string,
  name: string,
  currency: string,
  openingBalance: number,
  actorId: string | null,
): StoredAccount {
  const account: StoredAccount = {
    id: uid(),
    user_id: userId,
    name,
    environment: "paper",
    status: "active",
    base_currency: currency.toUpperCase(),
    leverage_enabled: false,
    created_at: now(),
    ledger_accounts: Object.fromEntries(LEDGER_KINDS.map((k) => [k, uid()])) as Record<Kind, string>,
    reserved: 0,
  };
  state.accounts.push(account);
  withTransaction(state, {
    account,
    type: "opening_funding",
    lines: [
      ["cash", openingBalance],
      ["opening_equity", -openingBalance],
    ],
    description: `Virtual opening balance of ${currency} ${openingBalance.toLocaleString("en-GB")}`,
    reference: `acct:${account.id}`,
    idempotencyKey: `opening:${account.id}`,
    actor: actorId,
  });
  return account;
}

function withTransaction(
  state: DemoState,
  input: {
    account: StoredAccount;
    type: TransactionType;
    lines: Array<[Kind, number]>;
    description: string;
    reference?: string | null;
    idempotencyKey?: string | null;
    at?: string;
    actor?: string | null;
  },
): StoredTransaction {
  const scaled = input.lines.map(([kind, amount]) => [kind, round(amount)] as [Kind, number]);
  const total = round(scaled.reduce((sum, [, amount]) => sum + amount, 0));
  if (total !== 0) {
    throw new DemoApiError(400, `Unbalanced ledger transaction: differ by ${total}.`);
  }
  const transaction: StoredTransaction = {
    id: uid(),
    account_id: input.account.id,
    transaction_type: input.type,
    reference: input.reference ?? null,
    description: input.description,
    occurred_at: input.at ?? now(),
    created_at: input.at ?? now(),
    idempotency_key: input.idempotencyKey ?? null,
    entries: scaled.map(([kind, amount]) => ({
      id: uid(),
      ledger_account_id: input.account.ledger_accounts[kind],
      amount,
      currency: input.account.base_currency,
    })),
  };
  state.transactions.push(transaction);
  return transaction;
}

function cashOf(state: DemoState, account: StoredAccount): number {
  const cashId = account.ledger_accounts.cash;
  return round(
    state.transactions
      .filter((t) => t.account_id === account.id)
      .flatMap((t) => t.entries)
      .filter((e) => e.ledger_account_id === cashId)
      .reduce((sum, e) => sum + e.amount, 0),
  );
}

function toAccountDetail(state: DemoState, account: StoredAccount): AccountDetail {
  const cash = cashOf(state, account);
  return {
    id: account.id,
    name: account.name,
    environment: "paper",
    status: account.status,
    base_currency: account.base_currency,
    leverage_enabled: account.leverage_enabled,
    created_at: account.created_at,
    balances: [
      {
        currency: account.base_currency,
        cash,
        reserved: account.reserved,
        equity: round(cash - account.reserved),
      },
    ],
    open_positions: 0,
  };
}

// -------------------------------------------------------------------------
// Session handling
// -------------------------------------------------------------------------
function issueTokens(user: StoredUser): TokenPair {
  const access = `demo.${user.id}.${Date.now()}`;
  try {
    localStorage.setItem(SESSION_KEY, JSON.stringify({ userId: user.id, access }));
  } catch {
    /* ignore */
  }
  return { access_token: access, refresh_token: `refresh.${user.id}`, token_type: "bearer", expires_in: 28800 };
}

export function demoRestoreSession(): User | null {
  try {
    const raw = localStorage.getItem(SESSION_KEY);
    if (!raw) return null;
    const { userId } = JSON.parse(raw) as { userId: string };
    const state = load();
    const user = state.users.find((u) => u.id === userId);
    return user ? toUser(user) : null;
  } catch {
    return null;
  }
}

function currentUser(state: DemoState, token: string | null): StoredUser {
  const raw = token?.replace("Bearer ", "");
  const userId = raw?.split(".")[1];
  const user = state.users.find((u) => u.id === userId);
  if (!user) throw new DemoApiError(401, "Authentication required.");
  if (!user.is_active) throw new DemoApiError(401, "This account is not active.");
  return user;
}

function toUser(user: StoredUser): User {
  const { password: _password, ...rest } = user;
  void _password;
  return { ...rest, preferences: { ...user.preferences } };
}

function passwordErrors(password: string): string[] {
  const errors: string[] = [];
  if (password.length < 10) errors.push("Password must be at least 10 characters.");
  if (!/[a-z]/.test(password)) errors.push("Password must contain a lowercase letter.");
  if (!/[A-Z]/.test(password)) errors.push("Password must contain an uppercase letter.");
  if (!/[0-9]/.test(password)) errors.push("Password must contain a digit.");
  return errors;
}

// -------------------------------------------------------------------------
// Public demo API surface (same shape as the HTTP client)
// -------------------------------------------------------------------------
export const demoApi = {
  isDemo: true as const,

  async meta(): Promise<SystemMeta> {
    return {
      name: "AUREX AI",
      version: "0.1.0",
      phase: "Phase 3 — Market Data (static demo)",
      environment: "demo",
      execution_mode: "paper",
      live_trading_enabled: false,
      real_money_enabled: false,
      base_currency: "GBP",
    };
  },

  async register(input: { email: string; full_name: string; password: string }): Promise<User> {
    const state = load();
    const email = input.email.toLowerCase().trim();
    const errors = passwordErrors(input.password);
    if (errors.length) throw new DemoApiError(422, errors.join(" "));
    if (state.users.some((u) => u.email === email)) {
      throw new DemoApiError(409, "An account with that email already exists.");
    }
    const user = makeUser({ ...input, email, role: "user" });
    state.users.push(user);
    openAccount(state, user.id, "Paper Trading Account", "GBP", BALANCE_SEED, user.id);
    audit(state, {
      actor_id: user.id,
      actor_email: user.email,
      action: "user.register",
      entity_type: "user",
      entity_id: user.id,
      detail: "self-registration (demo)",
    });
    save(state);
    return toUser(user);
  },

  async login(input: { email: string; password: string }): Promise<TokenPair> {
    const state = load();
    const email = input.email.toLowerCase().trim();
    const user = state.users.find((u) => u.email === email);
    if (!user || user.password !== input.password) {
      audit(state, {
        actor_id: null,
        actor_email: email,
        action: "auth.login",
        entity_type: "user",
        entity_id: null,
        detail: "Invalid credentials",
        outcome: "failure",
      });
      save(state);
      throw new DemoApiError(401, "Invalid email or password.");
    }
    if (!user.is_active) throw new DemoApiError(403, "This account has been suspended.");
    user.last_login_at = now();
    audit(state, {
      actor_id: user.id,
      actor_email: user.email,
      action: "auth.login",
      entity_type: "user",
      entity_id: user.id,
      detail: "demo session",
    });
    save(state);
    return issueTokens(user);
  },

  async logout(): Promise<void> {
    try {
      localStorage.removeItem(SESSION_KEY);
    } catch {
      /* ignore */
    }
  },

  async me(token: string): Promise<User> {
    const state = load();
    return toUser(currentUser(state, token));
  },

  async updateProfile(token: string, patch: Partial<User["preferences"]> & { full_name?: string }): Promise<User> {
    const state = load();
    const user = currentUser(state, token);
    if (patch.full_name) user.full_name = patch.full_name;
    Object.assign(user.preferences, {
      theme: patch.theme ?? user.preferences.theme,
      base_currency: patch.base_currency ?? user.preferences.base_currency,
      timezone: patch.timezone ?? user.preferences.timezone,
      email_alerts: patch.email_alerts ?? user.preferences.email_alerts,
      risk_alerts: patch.risk_alerts ?? user.preferences.risk_alerts,
    });
    audit(state, {
      actor_id: user.id,
      actor_email: user.email,
      action: "user.update_profile",
      entity_type: "user",
      entity_id: user.id,
      detail: Object.keys(patch).join(", "),
    });
    save(state);
    return toUser(user);
  },

  async changePassword(token: string, current: string, next: string): Promise<void> {
    const state = load();
    const user = currentUser(state, token);
    if (user.password !== current) throw new DemoApiError(400, "Your current password is incorrect.");
    const errors = passwordErrors(next);
    if (errors.length) throw new DemoApiError(422, errors.join(" "));
    user.password = next;
    audit(state, {
      actor_id: user.id,
      actor_email: user.email,
      action: "user.change_password",
      entity_type: "user",
      entity_id: user.id,
      detail: "password rotated (demo)",
    });
    save(state);
  },

  async listAccounts(token: string): Promise<AccountDetail[]> {
    const state = load();
    const user = currentUser(state, token);
    return state.accounts.filter((a) => a.user_id === user.id).map((a) => toAccountDetail(state, a));
  },

  async getAccount(token: string, id: string): Promise<AccountDetail> {
    const state = load();
    const user = currentUser(state, token);
    const account = state.accounts.find(
      (a) => a.id === id && (a.user_id === user.id || user.role === "admin"),
    );
    if (!account) throw new DemoApiError(404, "Trading account not found.");
    return toAccountDetail(state, account);
  },

  async createAccount(
    token: string,
    input: { name: string; base_currency: string; opening_balance: number },
  ): Promise<AccountDetail> {
    const state = load();
    const user = currentUser(state, token);
    if (state.accounts.some((a) => a.user_id === user.id && a.name === input.name)) {
      throw new DemoApiError(409, `You already have an account named '${input.name}'.`);
    }
    const account = openAccount(
      state,
      user.id,
      input.name,
      input.base_currency,
      input.opening_balance,
      user.id,
    );
    audit(state, {
      actor_id: user.id,
      actor_email: user.email,
      action: "account.create",
      entity_type: "trading_account",
      entity_id: account.id,
      detail: `paper account '${account.name}' funded with ${input.opening_balance.toLocaleString("en-GB")} ${account.base_currency}`,
    });
    save(state);
    return toAccountDetail(state, account);
  },

  async deposit(token: string, accountId: string, amount: number, description = ""): Promise<AccountDetail> {
    const state = load();
    const user = currentUser(state, token);
    const account = state.accounts.find((a) => a.id === accountId && a.user_id === user.id);
    if (!account) throw new DemoApiError(404, "Trading account not found.");
    withTransaction(state, {
      account,
      type: "deposit",
      lines: [
        ["cash", amount],
        ["opening_equity", -amount],
      ],
      description: description || `Virtual deposit of ${account.base_currency} ${amount.toLocaleString("en-GB")}`,
      actor: user.id,
    });
    audit(state, {
      actor_id: user.id,
      actor_email: user.email,
      action: "wallet.virtual_deposit",
      entity_type: "trading_account",
      entity_id: account.id,
      detail: `virtual deposit ${amount} ${account.base_currency}`,
    });
    save(state);
    return toAccountDetail(state, account);
  },

  async withdraw(token: string, accountId: string, amount: number, description = ""): Promise<AccountDetail> {
    const state = load();
    const user = currentUser(state, token);
    const account = state.accounts.find((a) => a.id === accountId && a.user_id === user.id);
    if (!account) throw new DemoApiError(404, "Trading account not found.");
    const cash = cashOf(state, account);
    if (amount > cash) {
      throw new DemoApiError(
        400,
        `Insufficient virtual funds: requested ${amount.toLocaleString("en-GB")}, available ${cash.toLocaleString("en-GB")}.`,
      );
    }
    withTransaction(state, {
      account,
      type: "withdrawal",
      lines: [
        ["withdrawal_clearing", amount],
        ["cash", -amount],
      ],
      description: description || `Virtual withdrawal of ${account.base_currency} ${amount.toLocaleString("en-GB")}`,
      actor: user.id,
    });
    audit(state, {
      actor_id: user.id,
      actor_email: user.email,
      action: "wallet.virtual_withdrawal",
      entity_type: "trading_account",
      entity_id: account.id,
      detail: `virtual withdrawal ${amount} ${account.base_currency}`,
    });
    save(state);
    return toAccountDetail(state, account);
  },

  async ledger(token: string, accountId: string, limit = 50): Promise<LedgerStatement> {
    const state = load();
    const user = currentUser(state, token);
    const account = state.accounts.find(
      (a) => a.id === accountId && (a.user_id === user.id || user.role === "admin"),
    );
    if (!account) throw new DemoApiError(404, "Trading account not found.");
    const all = state.transactions
      .filter((t) => t.account_id === account.id)
      .sort((a, b) => b.occurred_at.localeCompare(a.occurred_at));
    const transactions: LedgerTransaction[] = all.slice(0, limit).map((t) => ({
      id: t.id,
      transaction_type: t.transaction_type,
      reference: t.reference,
      description: t.description,
      occurred_at: t.occurred_at,
      created_at: t.created_at,
      entries: t.entries.map((e) => ({ ...e })),
    }));
    return {
      account_id: account.id,
      currency: account.base_currency,
      transactions,
      total: all.length,
      trial_balance: round(all.flatMap((t) => t.entries).reduce((sum, e) => sum + e.amount, 0)),
    };
  },

  async ledgerAccounts(token: string, accountId: string): Promise<LedgerAccount[]> {
    const state = load();
    const user = currentUser(state, token);
    const account = state.accounts.find((a) => a.id === accountId && a.user_id === user.id);
    if (!account) throw new DemoApiError(404, "Trading account not found.");
    return LEDGER_KINDS.map((kind) => ({
      id: account.ledger_accounts[kind],
      code: `${account.base_currency}:${kind}`,
      kind,
      account_type: ACCOUNT_TYPE[kind],
      currency: account.base_currency,
    }));
  },

  async reconcile(token: string, accountId: string): Promise<Reconciliation> {
    const state = load();
    const user = currentUser(state, token);
    const account = state.accounts.find((a) => a.id === accountId && a.user_id === user.id);
    if (!account) throw new DemoApiError(404, "Trading account not found.");
    const cash = cashOf(state, account);
    return {
      account_id: account.id,
      currency: account.base_currency,
      ledger_cash: cash,
      cached_cash: cash,
      is_balanced: true,
      notes: "Ledger and cached balance agree.",
    };
  },

  async auditLogs(token: string, limit = 100): Promise<AuditLog[]> {
    const state = load();
    const user = currentUser(state, token);
    if (user.role !== "admin") throw new DemoApiError(403, "You do not have permission to perform this action.");
    return state.audit.slice(0, limit).map(({ id, actor_email, action, entity_type, entity_id, outcome, ip_address, detail, created_at }) => ({
      id,
      actor_email,
      action,
      entity_type,
      entity_id,
      outcome,
      ip_address,
      detail,
      created_at,
    }));
  },

  async listUsers(token: string): Promise<User[]> {
    const state = load();
    const user = currentUser(state, token);
    if (user.role !== "admin") throw new DemoApiError(403, "You do not have permission to perform this action.");
    return state.users.map(toUser);
  },

  async updateUserRole(token: string, userId: string, role: UserRole, isActive?: boolean): Promise<User> {
    const state = load();
    const admin = currentUser(state, token);
    if (admin.role !== "admin") throw new DemoApiError(403, "You do not have permission to perform this action.");
    const target = state.users.find((u) => u.id === userId);
    if (!target) throw new DemoApiError(404, "User not found.");
    if (target.id === admin.id && role !== "admin") {
      throw new DemoApiError(400, "You cannot remove your own administrator role.");
    }
    const previous = target.role;
    target.role = role;
    if (isActive !== undefined) target.is_active = isActive;
    audit(state, {
      actor_id: admin.id,
      actor_email: admin.email,
      action: "admin.user_update",
      entity_type: "user",
      entity_id: target.id,
      detail: `role ${previous} -> ${role}; active=${target.is_active}`,
    });
    save(state);
    return toUser(target);
  },

  async integrations(token: string): Promise<Integration[]> {
    const state = load();
    const user = currentUser(state, token);
    if (user.role !== "admin") throw new DemoApiError(403, "You do not have permission to perform this action.");
    return state.integrations;
  },

  async notifications(): Promise<Notification[]> {
    return load().notifications;
  },

  async markNotificationRead(id: string): Promise<void> {
    const state = load();
    const item = state.notifications.find((n) => n.id === id);
    if (item) item.is_read = true;
    save(state);
  },

  async systemStatus(token: string): Promise<SystemStatus> {
    const state = load();
    const user = currentUser(state, token);
    if (user.role !== "admin") throw new DemoApiError(403, "You do not have permission to perform this action.");
    return {
      environment: "demo",
      version: "AUREX AI v0.1.0",
      uptime_seconds: Math.round(performance.now() / 1000),
      counts: {
        users: state.users.length,
        trading_accounts: state.accounts.length,
        balances: state.accounts.length,
        ledger_transactions: state.transactions.length,
        audit_logs: state.audit.length,
        notifications: state.notifications.length,
      },
      guardrails: {
        live_trading_enabled: false,
        real_money_enabled: false,
        execution_mode: "paper",
      },
    };
  },

  markets: DEMO_MARKETS,
  agents: DEMO_AGENTS,
  phases: DEMO_PHASES,
};

export type DemoApi = typeof demoApi;
export type DemoBalance = Balance;
