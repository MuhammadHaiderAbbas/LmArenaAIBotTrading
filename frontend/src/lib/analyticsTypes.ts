// Analytics contracts — mirrors backend `app/schemas/analytics.py`.

export interface AnalyticMetric {
  key: string;
  label: string;
  /** null means the metric is refused, never zero. `note` says why. */
  value: number | null;
  unit: "pct" | "ratio" | "";
  definition: string;
  note: string;
}

export interface CurvePoint {
  at: string;
  equity: number;
}

export interface ClosedTrade {
  symbol: string;
  quantity: number;
  entry_price: number;
  exit_price: number;
  realised_pnl: number;
  commission: number;
  holding_days: number;
  return_pct: number;
  opened_at: string;
  closed_at: string;
  strategy_code: string | null;
}

export interface PerformanceReport {
  as_of: string;
  equity: number;
  cash: number;
  starting_equity: number;
  net_return: number | null;
  metrics: AnalyticMetric[];
  equity_curve: CurvePoint[];
  benchmark_curve: CurvePoint[];
  benchmark_return: number | null;
  attribution: { by_symbol: Record<string, number>; by_strategy: Record<string, number> };
  costs: {
    commission: number;
    slippage: number;
    total: number;
    orders: number;
    fills: number;
    rejected_orders: number;
  };
  trades: ClosedTrade[];
  notes: string[];
  forward_looking: boolean;
}
