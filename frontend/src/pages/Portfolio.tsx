import { Link } from "react-router-dom";
import { useAccounts } from "../features/account/AccountContext";
import { formatDate, formatMoney } from "../lib/format";
import { Badge, EmptyState, Panel, PageHeader, Stat } from "../components/ui";
import { riskLimits } from "../lib/demoStatic";

export function Portfolio() {
  const { accounts, activeAccount, loading } = useAccounts();
  const balance = activeAccount?.balances[0];

  return (
    <>
      <PageHeader
        eyebrow="Capital allocation"
        title="Portfolio"
        description="Virtual balances by account, exposure limits and the constraints the portfolio manager will operate inside."
      />

      <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
        <Stat label="Accounts" value={accounts.length} hint="All paper environment" />
        <Stat
          label="Active currency"
          value={balance?.currency ?? "—"}
          hint={activeAccount?.name ?? "No account selected"}
        />
        <Stat
          label="Virtual equity"
          value={formatMoney(balance?.equity ?? 0, balance?.currency ?? "GBP")}
          tone="accent"
          hint="Cash less reserved"
        />
        <Stat label="Open positions" value={0} hint="Positions engine lands in Phase 8" />
      </div>

      <div className="mt-4 grid gap-4 lg:grid-cols-3">
        <Panel
          className="lg:col-span-2"
          title="Accounts"
          subtitle="Each account keeps its own chart of accounts and journal"
          bodyClassName="p-0"
         scrollBody>
          {loading ? (
            <div className="p-5 text-xs text-ink-muted">Loading accounts…</div>
          ) : accounts.length === 0 ? (
            <div className="p-5">
              <EmptyState title="No accounts" description="Create a paper account from the Wallet screen." />
            </div>
          ) : (
            <table className="table-base">
              <thead>
                <tr>
                  <th>Account</th>
                  <th>Environment</th>
                  <th>Currency</th>
                  <th className="text-right">Cash</th>
                  <th className="text-right">Equity</th>
                  <th>Opened</th>
                </tr>
              </thead>
              <tbody>
                {accounts.map((account) => (
                  <tr key={account.id}>
                    <td className="text-xs font-medium">{account.name}</td>
                    <td>
                      <Badge tone="caution">{account.environment}</Badge>
                    </td>
                    <td className="font-mono text-xs">{account.base_currency}</td>
                    <td className="cell-num text-right">
                      {formatMoney(account.balances[0]?.cash ?? 0, account.base_currency)}
                    </td>
                    <td className="cell-num text-right">
                      {formatMoney(account.balances[0]?.equity ?? 0, account.base_currency)}
                    </td>
                    <td className="text-xs text-ink-faint sm:whitespace-nowrap">
                      {formatDate(account.created_at)}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </Panel>

        <Panel title="Allocation constraints" subtitle="Applied by the portfolio analyst">
          <ul className="space-y-2 text-xs">
            {riskLimits.slice(0, 6).map((limit) => (
              <li key={limit.label} className="flex items-start justify-between gap-3">
                <span className="text-ink-muted">{limit.label}</span>
                <span className="text-right font-medium text-ink">{limit.value}</span>
              </li>
            ))}
          </ul>
          <Link to="/risk" className="mt-4 inline-flex text-xs font-medium text-accent-soft hover:underline">
            Open Risk Centre →
          </Link>
        </Panel>
      </div>

      <div className="mt-4 grid gap-4 lg:grid-cols-2">
        <Panel title="Positions" subtitle="Paper fills are reconciled to the ledger">
          <EmptyState
            title="No open positions"
            description="Position accounting, unrealised P&L and exposure snapshots are implemented with the paper execution engine in Phase 8. Nothing is simulated here to avoid implying live performance."
            icon={
              <svg width="26" height="26" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" aria-hidden>
                <path d="M4 18V9M10 18V5M16 18v-6M22 18V8" strokeLinecap="round" />
              </svg>
            }
          />
        </Panel>

        <Panel title="Performance" subtitle="Metric definitions are fixed now; values appear once paper trading runs" scrollBody>
          <table className="table-base">
            <thead>
              <tr>
                <th>Metric</th>
                <th>Definition</th>
                <th className="text-right">Value</th>
              </tr>
            </thead>
            <tbody>
              {[
                ["Net return after costs", "Return net of fees, spread, slippage", "—"],
                ["Sharpe ratio", "Risk-adjusted return", "—"],
                ["Sortino ratio", "Downside-adjusted return", "—"],
                ["Maximum drawdown", "Worst peak-to-trough decline", "—"],
                ["Profit factor", "Gross profit ÷ gross loss", "—"],
                ["Turnover", "Traded notional ÷ average equity", "—"],
              ].map(([metric, definition, value]) => (
                <tr key={metric}>
                  <td className="text-xs font-medium">{metric}</td>
                  <td className="text-xs text-ink-muted">{definition}</td>
                  <td className="cell-num text-right text-ink-faint">{value}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </Panel>
      </div>
    </>
  );
}
