import { useCallback, useEffect, useMemo, useState } from "react";
import { Badge, Button, Dot, EmptyState, Panel, PageHeader, Stat, useToast } from "../components/ui";
import { api } from "../services/api";
import type { ExecutionOverview, Order, Position } from "../lib/executionTypes";
import { titleCase } from "../lib/format";

const SYMBOLS = ["GBP/USD", "EUR/USD", "SPY", "BTC/USD"];

const STATUS_TONE: Record<string, "positive" | "negative" | "accent" | "muted"> = {
  filled: "positive",
  partially_filled: "accent",
  submitted: "accent",
  new: "muted",
  validated: "accent",
  rejected: "negative",
  cancelled: "muted",
  expired: "muted",
};

function money(value: number | null | undefined, currency = "GBP"): string {
  if (value === null || value === undefined) return "—";
  return `${value < 0 ? "-" : ""}${Math.abs(value).toLocaleString("en-GB", {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  })} ${currency}`;
}

function qty(value: number): string {
  return value.toLocaleString("en-GB", { maximumFractionDigits: 6 });
}

export function Execution() {
  const [overview, setOverview] = useState<ExecutionOverview | null>(null);
  const [positions, setPositions] = useState<Position[]>([]);
  const [orders, setOrders] = useState<Order[]>([]);
  const [symbol, setSymbol] = useState(SYMBOLS[0]);
  const [side, setSide] = useState<"buy" | "sell">("buy");
  const [quantity, setQuantity] = useState("1");
  const [orderType, setOrderType] = useState<"market" | "limit">("market");
  const [limitPrice, setLimitPrice] = useState("");
  const [rationale, setRationale] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const toast = useToast();

  const refresh = useCallback(async () => {
    try {
      const [nextOverview, nextPositions, nextOrders] = await Promise.all([
        api.executionOverview(),
        api.executionPositions("open"),
        api.executionOrders(15),
      ]);
      setOverview(nextOverview);
      setPositions(nextPositions);
      setOrders(nextOrders);
      setError(null);
    } catch (cause) {
      setError((cause as Error).message);
    }
  }, []);

  useEffect(() => {
    void refresh();
    // Polling keeps the marks fresh without a websocket; the demo has no server
    // pushing ticks, and pretending otherwise would be worse than a 15s refresh.
    const timer = setInterval(() => void refresh(), 15_000);
    return () => clearInterval(timer);
  }, [refresh]);

  const submit = async () => {
    const parsed = Number(quantity);
    if (!Number.isFinite(parsed) || parsed <= 0) {
      toast({ tone: "error", title: "Invalid quantity", description: "Quantity must be positive." });
      return;
    }
    setBusy(true);
    try {
      const order = await api.executionPlace({
        symbol,
        side,
        quantity: parsed,
        order_type: orderType,
        limit_price: orderType === "limit" ? Number(limitPrice) : null,
        rationale,
      });
      if (order.status === "rejected") {
        // A refusal is a result, not a crash: show the reason the validator gave.
        toast({ tone: "error", title: "Order rejected", description: order.reject_reason });
      } else if (order.status === "filled") {
        toast({
          tone: "success",
          title: "Filled on the paper venue",
          description: `${qty(order.filled_quantity)} ${order.instrument_symbol} at ${order.average_fill_price?.toFixed(4)} in ${order.fills.length} fill(s).`,
        });
      } else {
        toast({
          tone: "info",
          title: `Order ${order.status.replace("_", " ")}`,
          description: order.fills.length
            ? `${qty(order.filled_quantity)} of ${qty(order.quantity)} filled; the remainder is resting.`
            : "The order rests unfilled against the current market.",
        });
      }
      setRationale("");
      await refresh();
    } catch (cause) {
      toast({ tone: "error", title: "Order failed", description: (cause as Error).message });
    } finally {
      setBusy(false);
    }
  };

  const cancel = async (orderId: string) => {
    try {
      await api.executionCancel(orderId);
      toast({ tone: "info", title: "Order cancelled" });
      await refresh();
    } catch (cause) {
      toast({ tone: "error", title: "Cancel failed", description: (cause as Error).message });
    }
  };

  const heldForSymbol = useMemo(
    () => positions.filter((position) => position.instrument_symbol === symbol).reduce((sum, p) => sum + p.quantity, 0),
    [positions, symbol],
  );

  return (
    <>
      <PageHeader
        eyebrow="Paper execution"
        title="Execution"
        description="The only place in this platform that can move money — and it moves simulated money. Orders are validated deterministically, filled against a paper venue with commission and slippage, and every fill posts a balanced ledger entry."
        actions={
          <Badge tone="caution">
            <Dot tone="caution" />
            Paper venue · no broker connected
          </Badge>
        }
      />

      <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
        <Stat label="Equity" value={money(overview?.equity)} hint="Cash plus marked positions" tone="accent" />
        <Stat
          label="Unrealised"
          value={money(overview?.unrealised_pnl)}
          hint="Marked to the latest close"
          tone={(overview?.unrealised_pnl ?? 0) >= 0 ? "positive" : "negative"}
        />
        <Stat label="Realised" value={money(overview?.realised_pnl)} hint={`${overview?.closed_positions ?? 0} closed position(s)`} />
        <Stat
          label="Rejected orders"
          value={overview?.orders_rejected ?? 0}
          hint={overview ? `${(overview.rejection_rate * 100).toFixed(0)}% of ${overview.orders_total} orders` : "—"}
          tone={(overview?.orders_rejected ?? 0) > 0 ? "negative" : "neutral"}
        />
      </div>

      {error && (
        <div className="mt-4 rounded-lg border border-negative/30 bg-negative/10 px-3 py-2.5 text-xs text-negative">
          {error}
        </div>
      )}

      <div className="mt-4 grid gap-4 lg:grid-cols-3">
        <Panel title="New order" subtitle="Validated before it reaches the venue">
          <div className="space-y-3">
            <div>
              <span className="label mb-1 block">Instrument</span>
              <select
                aria-label="Instrument"
                value={symbol}
                onChange={(event) => setSymbol(event.target.value)}
                className="h-9 w-full rounded-lg border border-line-strong bg-surface-sunken px-2.5 text-xs text-ink"
              >
                {SYMBOLS.map((value) => (
                  <option key={value} value={value}>
                    {value}
                  </option>
                ))}
              </select>
              <p className="mt-1 text-[10px] text-ink-faint">Held: {qty(heldForSymbol)}</p>
            </div>

            <div className="grid grid-cols-2 gap-2">
              <button
                type="button"
                onClick={() => setSide("buy")}
                aria-pressed={side === "buy"}
                className={
                  side === "buy"
                    ? "h-9 rounded-lg border border-positive/50 bg-positive/15 text-xs font-semibold text-positive"
                    : "h-9 rounded-lg border border-line-strong bg-surface-sunken text-xs text-ink-muted"
                }
              >
                Buy
              </button>
              <button
                type="button"
                onClick={() => setSide("sell")}
                aria-pressed={side === "sell"}
                className={
                  side === "sell"
                    ? "h-9 rounded-lg border border-negative/50 bg-negative/15 text-xs font-semibold text-negative"
                    : "h-9 rounded-lg border border-line-strong bg-surface-sunken text-xs text-ink-muted"
                }
              >
                Sell
              </button>
            </div>

            <div className="grid grid-cols-2 gap-2">
              <label className="block">
                <span className="label mb-1 block">Quantity</span>
                <input
                  aria-label="Quantity"
                  type="number"
                  min="0"
                  step="0.01"
                  value={quantity}
                  onChange={(event) => setQuantity(event.target.value)}
                  className="h-9 w-full rounded-lg border border-line-strong bg-surface-sunken px-2.5 text-xs text-ink"
                />
              </label>
              <label className="block">
                <span className="label mb-1 block">Type</span>
                <select
                  aria-label="Order type"
                  value={orderType}
                  onChange={(event) => setOrderType(event.target.value as "market" | "limit")}
                  className="h-9 w-full rounded-lg border border-line-strong bg-surface-sunken px-2.5 text-xs text-ink"
                >
                  <option value="market">Market</option>
                  <option value="limit">Limit</option>
                </select>
              </label>
            </div>

            {orderType === "limit" && (
              <label className="block">
                <span className="label mb-1 block">Limit price</span>
                <input
                  aria-label="Limit price"
                  type="number"
                  min="0"
                  step="0.0001"
                  value={limitPrice}
                  onChange={(event) => setLimitPrice(event.target.value)}
                  className="h-9 w-full rounded-lg border border-line-strong bg-surface-sunken px-2.5 text-xs text-ink"
                />
              </label>
            )}

            <label className="block">
              <span className="label mb-1 block">Rationale (stored on the position)</span>
              <input
                aria-label="Rationale"
                value={rationale}
                onChange={(event) => setRationale(event.target.value)}
                placeholder="Why this trade exists"
                className="h-9 w-full rounded-lg border border-line-strong bg-surface-sunken px-2.5 text-xs text-ink"
              />
            </label>

            <Button onClick={submit} disabled={busy} className="w-full">
              {busy ? "Submitting…" : `Submit ${side} order`}
            </Button>

            <div className="rounded-lg border border-caution/25 bg-caution/[0.06] px-3 py-2.5 text-2xs leading-relaxed text-caution">
              <strong>Simulated only.</strong> Nothing here reaches a broker. Validation refuses
              stale data, unaffordable orders and short selling; a refusal is shown with its
              reason rather than being retried silently.
            </div>
          </div>
        </Panel>

        <Panel
          className="lg:col-span-2"
          title="Open positions"
          subtitle={`${positions.length} open · marked to the latest close`}
          bodyClassName="p-0"
         scrollBody>
          {positions.length === 0 ? (
            <div className="px-4 py-4">
              <EmptyState
                title="No open positions"
                description="Submit an order to open one. Positions are long-only and marked to the latest close."
              />
            </div>
          ) : (
            <table className="table-base">
              <thead>
                <tr>
                  <th>Instrument</th>
                  <th className="text-right">Qty</th>
                  <th className="text-right">Avg entry</th>
                  <th className="text-right">Mark</th>
                  <th className="text-right">Unrealised</th>
                  <th>Origin</th>
                </tr>
              </thead>
              <tbody>
                {positions.map((position) => (
                  <tr key={position.id}>
                    <td className="text-xs font-medium">{position.instrument_symbol}</td>
                    <td className="text-right text-xs text-ink-muted">{qty(position.quantity)}</td>
                    <td className="text-right text-xs text-ink-muted">{position.average_entry_price.toFixed(4)}</td>
                    <td className="text-right text-xs text-ink-muted">
                      {position.last_mark_price?.toFixed(4) ?? "—"}
                    </td>
                    <td
                      className={
                        (position.unrealised_pnl ?? 0) >= 0
                          ? "text-right text-xs text-positive"
                          : "text-right text-xs text-negative"
                      }
                    >
                      {money(position.unrealised_pnl)}
                    </td>
                    <td className="text-[10px] text-ink-faint">
                      {position.strategy_code ?? "manual"}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
          {overview && (
            <div className="border-t border-line px-4 py-3 text-[11px] leading-relaxed text-ink-faint">
              Cash {money(overview.cash)} · positions {money(overview.positions_value)} ·
              commission paid {money(overview.total_commission)} · slippage{" "}
              {money(overview.total_slippage)} · {overview.fills_total} fill(s).
              {overview.reconciliation.investment_agrees && overview.reconciliation.realised_agrees
                ? " Positions reconcile against the ledger."
                : " ⚠ Position and ledger records disagree."}
            </div>
          )}
        </Panel>
      </div>

      <Panel className="mt-4" title="Order history" subtitle="Every state change is recorded" bodyClassName="p-0" scrollBody>
        {orders.length === 0 ? (
          <div className="px-4 py-4">
            <EmptyState title="No orders yet" description="Submit one above to see the lifecycle." />
          </div>
        ) : (
          <table className="table-base">
            <thead>
              <tr>
                <th>Order</th>
                <th>Side</th>
                <th className="text-right">Qty</th>
                <th className="text-right">Filled</th>
                <th className="text-right">Avg price</th>
                <th className="text-right">Costs</th>
                <th>Status</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {orders.map((order) => (
                <tr key={order.id}>
                  <td className="font-mono text-[10px] text-ink-faint">{order.id.slice(0, 10)}</td>
                  <td className="text-xs font-medium">
                    <span className={order.side === "buy" ? "text-positive" : "text-negative"}>
                      {titleCase(order.side)}
                    </span>{" "}
                    <span className="text-ink-muted">{order.instrument_symbol}</span>
                  </td>
                  <td className="text-right text-xs text-ink-muted">{qty(order.quantity)}</td>
                  <td className="text-right text-xs text-ink-muted">{qty(order.filled_quantity)}</td>
                  <td className="text-right text-xs text-ink-muted">
                    {order.average_fill_price?.toFixed(4) ?? "—"}
                  </td>
                  <td className="text-right text-xs text-ink-muted">
                    {money(order.total_commission + order.total_slippage)}
                  </td>
                  <td>
                    <Badge tone={STATUS_TONE[order.status] ?? "muted"}>{titleCase(order.status)}</Badge>
                    {order.status === "rejected" && (
                      <p className="mt-1 max-w-md text-[10px] leading-relaxed text-negative">
                        {order.reject_reason}
                      </p>
                    )}
                  </td>
                  <td className="text-right">
                    {["submitted", "validated", "partially_filled"].includes(order.status) && (
                      <button
                        type="button"
                        onClick={() => void cancel(order.id)}
                        className="text-[11px] font-medium text-accent-soft hover:underline"
                      >
                        Cancel
                      </button>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </Panel>

      <div className="mt-4 grid gap-4 lg:grid-cols-2">
        <Panel title="How a fill is priced">
          <ul className="space-y-2.5 text-xs text-ink-muted">
            {[
              ["Signals are not commands", "An order is validated deterministically before it reaches the venue: data freshness, cash, and position are all checked."],
              ["Partial fills are real", "An order larger than 15% of median bar volume fills in up to four bites, each at a worse price. Slippage is stored per fill, not claimed."],
              ["The ledger is the record", "Every fill posts a balanced double-entry transaction. Position rows are a view over it and are reconciled, not trusted."],
              ["Refusals are results", "A stale price, an unaffordable order or a short is rejected with the reason shown — never retried silently."],
            ].map(([title, detail]) => (
              <li key={title} className="rounded-md border border-line bg-surface-sunken px-3 py-2">
                <p className="font-medium text-ink">{title}</p>
                <p className="mt-0.5 text-[11px] leading-relaxed text-ink-faint">{detail}</p>
              </li>
            ))}
          </ul>
        </Panel>

        <Panel title="What Phase 8 does not do">
          <ul className="space-y-2.5 text-xs text-ink-muted">
            {[
              ["No broker", "No adapter, no credentials, no live order path. The venue is a simulator."],
              ["No autonomous loop", "Nothing submits orders on a timer. The independent risk controller arrives in Phase 9, and it is what an autonomous loop needs to be safe."],
              ["No short selling", "Leverage is disabled by blueprint default; a short needs borrow and financing rules."],
              ["No stop-loss enforcement yet", "Stops are a risk-control concern and belong with the limits engine in Phase 9."],
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
