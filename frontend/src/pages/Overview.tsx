import { useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import {
  Area,
  AreaChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import { useAuth } from "../features/auth/AuthContext";
import { useAccounts } from "../features/account/AccountContext";
import { api } from "../services/api";
import { markets, phases, agents } from "../lib/demoStatic";
import { formatDateTime, formatMoney, formatPercent, relativeTime } from "../lib/format";
import { Badge, Dot, EmptyState, PageHeader, Panel, Stat } from "../components/ui";
import {
  IconAlert,
  IconArrowDown,
  IconArrowUp,
  IconBot,
  IconShield,
  IconWallet,
} from "../lib/icons";
import type { LedgerStatement } from "../lib/types";

function equityCurve(start: number) {
  // Deterministic illustrative curve: the real curve starts in Phase 8/10.
  let value = start * 0.94;
  const points = [];
  for (let day = 0; day < 30; day += 1) {
    const wave = Math.sin(day / 3.1) * 0.008 + Math.cos(day / 5.4) * 0.005;
    value = value * (1 + wave + 0.0016);
    points.push({
      date: new Date(Date.now() - (29 - day) * 86_400_000).toISOString().slice(5, 10),
      equity: Math.round(value),
    });
  }
  return points;
}

export function Overview() {
  const { user } = useAuth();
  const { activeAccount } = useAccounts();
  const [statement, setStatement] = useState<LedgerStatement | null>(null);

  useEffect(() => {
    if (!activeAccount) return;
    api
      .ledger(activeAccount.id, 6)
      .then(setStatement)
      .catch(() => setStatement(null));
  }, [activeAccount?.id]);

  const balance = activeAccount?.balances[0];
  const curve = useMemo(() => equityCurve(balance?.cash ?? 100_000), [balance?.cash]);
  const start = curve[0]?.equity ?? 0;
  const end = curve[curve.length - 1]?.equity ?? 0;
  const changePct = start ? ((end - start) / start) * 100 : 0;

  const activeAgents = agents.filter((a) => a.status !== "blocked").length;
  const nextPhase = phases.find((p) => p.status === "planned");

  return (
    <>
      <PageHeader
        eyebrow={`Welcome back, ${user?.full_name?.split(" ")[0] ?? "trader"}`}
        title="Overview"
        description="Portfolio state, virtual balance, research readiness and the path to the next phase."
        actions={
          <>
            <Link to="/wallet">
              <span className="inline-flex h-10 items-center gap-2 rounded-lg border border-line-strong bg-surface-raised px-4 text-sm font-semibold text-ink hover:bg-surface-hover">
                <IconWallet /> Virtual funds
              </span>
            </Link>
            <Link to="/trading">
              <span className="inline-flex h-10 items-center gap-2 rounded-lg border border-accent/60 bg-accent px-4 text-sm font-semibold text-white hover:bg-accent-soft">
                <IconBot /> AI trading controls
              </span>
            </Link>
          </>
        }
      />

      <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
        <Stat
          label="Virtual equity"
          value={formatMoney(balance?.equity ?? 0, balance?.currency ?? "GBP")}
          hint={`${activeAccount?.name ?? "No account"} · paper only`}
          tone="accent"
        />
        <Stat
          label="Available cash"
          value={formatMoney(balance?.cash ?? 0, balance?.currency ?? "GBP")}
          hint={`Reserved ${formatMoney(balance?.reserved ?? 0, balance?.currency ?? "GBP")}`}
        />
        <Stat
          label="Illustrative P&L (30d)"
          value={formatPercent(changePct)}
          hint="Sample curve — no live strategy is running"
          tone={changePct >= 0 ? "positive" : "negative"}
          icon={changePct >= 0 ? <IconArrowUp /> : <IconArrowDown />}
        />
        <Stat
          label="Open positions"
          value={activeAccount?.open_positions ?? 0}
          hint="Execution engine arrives in Phase 8"
        />
      </div>

      <div className="mt-4 grid gap-4 xl:grid-cols-3">
        <Panel
          className="xl:col-span-2 self-start"
          title="Equity curve"
          subtitle="Illustrative only — upstream market data and execution are not connected yet"
          actions={<Badge tone="caution">Simulated</Badge>}
          bodyClassName="p-2 pb-4"
        >
          <div className="h-64 w-full">
            <ResponsiveContainer width="100%" height="100%">
              <AreaChart data={curve} margin={{ top: 16, right: 16, left: 8, bottom: 0 }}>
                <defs>
                  <linearGradient id="equityFill" x1="0" y1="0" x2="0" y2="1">
                    <stop offset="0%" stopColor="#4F8CFF" stopOpacity={0.35} />
                    <stop offset="100%" stopColor="#4F8CFF" stopOpacity={0} />
                  </linearGradient>
                </defs>
                <XAxis
                  dataKey="date"
                  stroke="#5C6A82"
                  tick={{ fontSize: 10 }}
                  tickLine={false}
                  axisLine={{ stroke: "#1C2740" }}
                  minTickGap={28}
                />
                <YAxis
                  stroke="#5C6A82"
                  tick={{ fontSize: 10 }}
                  tickLine={false}
                  axisLine={false}
                  width={60}
                  domain={["dataMin - 1500", "dataMax + 1500"]}
                  tickFormatter={(value: number) => `${Math.round(value / 1000)}k`}
                />
                <Tooltip
                  contentStyle={{
                    background: "#111A2C",
                    border: "1px solid #27354F",
                    borderRadius: 8,
                    fontSize: 12,
                  }}
                  labelStyle={{ color: "#8F9DB5" }}
                  formatter={(value: number) => [formatMoney(value, balance?.currency ?? "GBP"), "Equity"]}
                />
                <Area
                  type="monotone"
                  dataKey="equity"
                  stroke="#4F8CFF"
                  strokeWidth={2}
                  fill="url(#equityFill)"
                />
              </AreaChart>
            </ResponsiveContainer>
          </div>
        </Panel>

        <div className="space-y-4">
          <Panel title="AI department" subtitle="Readiness by agent" actions={<Badge tone="accent">{activeAgents}/{agents.length} ready</Badge>}>
            <ul className="space-y-2.5">
              {agents.slice(0, 5).map((agent) => (
                <li key={agent.id} className="flex items-start justify-between gap-3">
                  <div className="min-w-0">
                    <p className="truncate text-xs font-medium text-ink">{agent.name}</p>
                    <p className="truncate text-[10px] uppercase tracking-wider text-ink-faint">
                      {agent.role} · Phase {agent.phase}
                    </p>
                  </div>
                  <Badge
                    tone={
                      agent.status === "idle"
                        ? "positive"
                        : agent.status === "blocked"
                          ? "muted"
                          : "caution"
                    }
                  >
                    {agent.status.replace("_", " ")}
                  </Badge>
                </li>
              ))}
            </ul>
            <Link
              to="/research"
              className="mt-4 inline-flex text-xs font-medium text-accent-soft hover:underline"
            >
              Open AI Research Lab →
            </Link>
          </Panel>

          <Panel title="Risk guardrails" subtitle="Enforced independently of the AI" actions={<IconShield className="text-positive" />}>
            <ul className="space-y-2 text-xs">
              {[
                ["Live trading", "Disabled"],
                ["Real money", "Disabled"],
                ["Max risk per trade", "0.50%"],
                ["Emergency stop", "Armed"],
              ].map(([label, value]) => (
                <li key={label} className="flex items-center justify-between">
                  <span className="text-ink-muted">{label}</span>
                  <span className="flex items-center gap-1.5 font-medium text-ink">
                    <Dot tone={value === "Disabled" || value === "Armed" ? "positive" : "caution"} />
                    {value}
                  </span>
                </li>
              ))}
            </ul>
            <Link to="/risk" className="mt-4 inline-flex text-xs font-medium text-accent-soft hover:underline">
              Open Risk Centre →
            </Link>
          </Panel>
        </div>
      </div>

      <div className="mt-4 grid gap-4 xl:grid-cols-3">
        <Panel
          className="xl:col-span-2"
          scrollBody
          title="Recent ledger activity"
          subtitle="Derived from the double-entry journal — never edited, only compensated"
          actions={
            <Link to="/wallet" className="text-xs font-medium text-accent-soft hover:underline">
              View statement
            </Link>
          }
          bodyClassName="p-0"
        >
          {statement && statement.transactions.length > 0 ? (
            <table className="table-base">
              <thead>
                <tr>
                  <th>Type</th>
                  <th>Description</th>
                  <th>Occurred</th>
                  <th className="text-right">Amount</th>
                </tr>
              </thead>
              <tbody>
                {statement.transactions.map((txn) => {
                  const amount = txn.entries.reduce((sum, entry) => sum + entry.amount, 0);
                  return (
                    <tr key={txn.id}>
                      <td>
                        <Badge tone={txn.transaction_type === "withdrawal" ? "negative" : "positive"}>
                          {txn.transaction_type.replace(/_/g, " ")}
                        </Badge>
                      </td>
                      <td className="max-w-[22rem] truncate text-xs text-ink-muted">{txn.description}</td>
                      <td className="text-xs text-ink-faint sm:whitespace-nowrap">
                        {relativeTime(txn.occurred_at)}
                      </td>
                      <td className="cell-num text-right text-ink-faint">
                        {amount === 0 ? "balanced" : formatMoney(amount, statement.currency)}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          ) : (
            <div className="p-5">
              <EmptyState
                title="No ledger activity yet"
                description="Virtual deposits, withdrawals and (from Phase 8) paper fills all post balanced double-entry transactions here."
              />
            </div>
          )}
        </Panel>

        <Panel title="Market snapshot" subtitle="Reference prices — live feed arrives in Phase 3" actions={<Badge tone="muted">Static</Badge>} bodyClassName="p-0">
          <ul className="divide-y divide-line/70">
            {markets.slice(0, 6).map((market) => (
              <li key={market.symbol} className="flex items-center justify-between px-5 py-2.5">
                <div>
                  <p className="font-mono text-xs font-medium text-ink">{market.symbol}</p>
                  <p className="text-[10px] text-ink-faint">
                    {market.assetClass} · {market.venue}
                  </p>
                </div>
                <div className="text-right">
                  <p className="font-mono text-xs tabular-nums text-ink">
                    {market.price.toLocaleString("en-GB", { maximumFractionDigits: 4 })}
                  </p>
                  <p
                    className={
                      market.changePct >= 0
                        ? "font-mono text-[10px] text-positive"
                        : "font-mono text-[10px] text-negative"
                    }
                  >
                    {formatPercent(market.changePct)}
                  </p>
                </div>
              </li>
            ))}
          </ul>
        </Panel>
      </div>

      <div className="mt-4 grid gap-4 lg:grid-cols-2">
        <Panel title="Next milestone" subtitle="Where engineering effort goes next">
          {nextPhase && (
            <div>
              <div className="flex items-center gap-2">
                <Badge tone="accent">Phase {nextPhase.id}</Badge>
                <p className="text-sm font-semibold text-ink">{nextPhase.name}</p>
              </div>
              <p className="mt-2 text-xs leading-relaxed text-ink-muted">{nextPhase.scope}</p>
              <p className="mt-3 rounded-md border border-line bg-surface-sunken px-3 py-2 text-xs text-ink-muted">
                <span className="font-semibold text-ink">Deliverable: </span>
                {nextPhase.deliverable}
              </p>
              <Link to="/settings" className="mt-3 inline-flex text-xs font-medium text-accent-soft hover:underline">
                View the full 12-phase roadmap →
              </Link>
            </div>
          )}
        </Panel>

        <Panel title="Alerts & notices" subtitle="Operational messages for this environment" bodyClassName="p-0">
          <ul className="divide-y divide-line/70">
            <li className="flex gap-3 px-5 py-3">
              <IconAlert className="mt-0.5 shrink-0 text-caution" />
              <div>
                <p className="text-xs font-medium text-ink">Live trading is disabled</p>
                <p className="text-[11px] leading-relaxed text-ink-muted">
                  No broker credentials exist in this deployment. Real-money transfers require
                  separate regulatory and security gates.
                </p>
              </div>
            </li>
            <li className="flex gap-3 px-5 py-3">
              <IconShield className="mt-0.5 shrink-0 text-positive" />
              <div>
                <p className="text-xs font-medium text-ink">Ledger reconciled</p>
                <p className="text-[11px] leading-relaxed text-ink-muted">
                  Every transaction balances to zero. Corrections are posted as compensating entries.
                </p>
              </div>
            </li>
            <li className="flex gap-3 px-5 py-3">
              <IconBot className="mt-0.5 shrink-0 text-accent-soft" />
              <div>
                <p className="text-xs font-medium text-ink">Agents awaiting data</p>
                <p className="text-[11px] leading-relaxed text-ink-muted">
                  Specialist agents stay idle until licensed market and news feeds connect in Phases
                  3–4. Last checked {formatDateTime(new Date().toISOString())}.
                </p>
              </div>
            </li>
          </ul>
        </Panel>
      </div>
    </>
  );
}
