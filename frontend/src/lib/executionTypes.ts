// Execution contracts — mirrors backend `app/schemas/execution.py`.

export type OrderSide = "buy" | "sell";
export type OrderType = "market" | "limit";
export type OrderStatus =
  | "new"
  | "validated"
  | "submitted"
  | "partially_filled"
  | "filled"
  | "rejected"
  | "cancelled"
  | "expired";
export type PositionStatus = "open" | "closed";
export type ExecutionEventType =
  | "submitted"
  | "validated"
  | "rejected"
  | "acknowledged"
  | "partial_fill"
  | "filled"
  | "cancelled"
  | "expired"
  | "position_opened"
  | "position_closed"
  | "marked";

export interface Fill {
  sequence: number;
  quantity: number;
  price: number;
  reference_price: number;
  slippage: number;
  commission: number;
  notional: number;
  filled_at: string;
  venue: string;
  ledger_transaction_id: string | null;
}

export interface ExecutionEvent {
  id: string;
  event_type: ExecutionEventType;
  message: string;
  occurred_at: string;
  actor_id: string | null;
}

export interface Order {
  id: string;
  trading_account_id: string;
  instrument_symbol: string;
  client_order_id: string;
  side: OrderSide;
  order_type: OrderType;
  time_in_force: string;
  status: OrderStatus;
  quantity: number;
  filled_quantity: number;
  limit_price: number | null;
  average_fill_price: number | null;
  total_commission: number;
  total_slippage: number;
  reject_reason: string;
  origin: string;
  rationale: string;
  submitted_at: string | null;
  completed_at: string | null;
  created_at: string;
  fills: Fill[];
  events: ExecutionEvent[];
}

export interface Position {
  id: string;
  instrument_symbol: string;
  status: PositionStatus;
  quantity: number;
  /** Quantity at the moment the position was closed; zero while open. */
  closed_quantity: number;
  average_entry_price: number;
  cost_basis: number;
  realised_pnl: number;
  total_commission: number;
  last_mark_price: number | null;
  last_marked_at: string | null;
  opened_at: string;
  closed_at: string | null;
  strategy_code: string | null;
  opening_rationale: string;
  market_value: number | null;
  unrealised_pnl: number | null;
}

export interface ExecutionOverview {
  open_positions: number;
  closed_positions: number;
  orders_total: number;
  orders_rejected: number;
  fills_total: number;
  cash: number;
  positions_value: number;
  unrealised_pnl: number;
  realised_pnl: number;
  equity: number;
  total_commission: number;
  total_slippage: number;
  rejection_rate: number;
  reconciliation: {
    investment_agrees: boolean;
    realised_agrees: boolean;
    positions_value: number;
    ledger_investment: number;
  };
}
