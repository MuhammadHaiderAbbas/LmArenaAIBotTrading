// Shared API contract types. These mirror the FastAPI Pydantic schemas in
// backend/app/schemas so the transport layer can be swapped without touching UI code.

export type UserRole = "admin" | "analyst" | "user";
export type EnvironmentName = "research" | "paper" | "live";
export type AccountStatus = "active" | "suspended" | "closed";

export type TransactionType =
  | "opening_funding"
  | "deposit"
  | "withdrawal"
  | "trade_buy"
  | "trade_sell"
  | "fee"
  | "adjustment";

export type LedgerAccountKind =
  | "cash"
  | "investment"
  | "opening_equity"
  | "withdrawal_clearing"
  | "fee_expense"
  | "realised_pnl";

export interface UserSettings {
  theme: string;
  base_currency: string;
  timezone: string;
  email_alerts: boolean;
  risk_alerts: boolean;
}

export interface User {
  id: string;
  email: string;
  full_name: string;
  role: UserRole;
  is_active: boolean;
  is_verified: boolean;
  mfa_enabled: boolean;
  last_login_at: string | null;
  created_at: string;
  preferences: UserSettings | null;
}

export interface TokenPair {
  access_token: string;
  refresh_token: string;
  token_type: string;
  expires_in: number;
}

export interface Balance {
  currency: string;
  cash: number;
  reserved: number;
  equity: number;
}

export interface TradingAccount {
  id: string;
  name: string;
  environment: EnvironmentName;
  status: AccountStatus;
  base_currency: string;
  leverage_enabled: boolean;
  created_at: string;
}

export interface AccountDetail extends TradingAccount {
  balances: Balance[];
  open_positions: number;
}

export interface LedgerEntry {
  id: string;
  ledger_account_id: string;
  amount: number;
  currency: string;
}

export interface LedgerTransaction {
  id: string;
  transaction_type: TransactionType;
  reference: string | null;
  description: string;
  occurred_at: string;
  created_at: string;
  entries: LedgerEntry[];
}

export interface LedgerStatement {
  account_id: string;
  currency: string;
  transactions: LedgerTransaction[];
  total: number;
  trial_balance: number;
}

export interface LedgerAccount {
  id: string;
  code: string;
  kind: LedgerAccountKind;
  account_type: "asset" | "liability" | "equity" | "income" | "expense";
  currency: string;
}

export interface Reconciliation {
  account_id: string;
  currency: string;
  ledger_cash: number;
  cached_cash: number;
  is_balanced: boolean;
  notes: string;
}

export interface AuditLog {
  id: string;
  actor_email: string | null;
  action: string;
  entity_type: string | null;
  entity_id: string | null;
  outcome: string;
  ip_address: string | null;
  detail: string;
  created_at: string;
}

export interface Notification {
  id: string;
  title: string;
  body: string;
  category: string;
  is_read: boolean;
  created_at: string;
}

export interface Integration {
  provider: string;
  category: string;
  status: "not_configured" | "ok" | "degraded" | "error";
  latency_ms: number | null;
  message: string;
}

export interface SystemStatus {
  environment: string;
  version: string;
  uptime_seconds: number;
  counts: Record<string, number>;
  guardrails: Record<string, unknown>;
}

export interface SystemMeta {
  name: string;
  version: string;
  phase: string;
  environment: string;
  execution_mode: string;
  live_trading_enabled: boolean;
  real_money_enabled: boolean;
  base_currency: string;
}

export interface ApiError {
  status: number;
  message: string;
  fields?: Record<string, string>;
}
