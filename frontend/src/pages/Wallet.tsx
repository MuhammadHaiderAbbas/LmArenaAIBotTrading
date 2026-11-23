import { useCallback, useEffect, useState } from "react";
import { useAccounts } from "../features/account/AccountContext";
import { api } from "../services/api";
import { formatDateTime, formatMoney, titleCase } from "../lib/format";
import {
  Badge,
  Button,
  EmptyState,
  ErrorNote,
  Field,
  Input,
  PageHeader,
  Panel,
  Spinner,
  Stat,
  useToast,
} from "../components/ui";
import { IconArrowDown, IconArrowUp, IconRefresh, IconWallet } from "../lib/icons";
import type { LedgerAccount, LedgerStatement, Reconciliation } from "../lib/types";

export function Wallet() {
  const { activeAccount, reload } = useAccounts();
  const toast = useToast();
  const [statement, setStatement] = useState<LedgerStatement | null>(null);
  const [ledgerAccounts, setLedgerAccounts] = useState<LedgerAccount[]>([]);
  const [recon, setRecon] = useState<Reconciliation | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [amount, setAmount] = useState("1000");
  const [busy, setBusy] = useState<"deposit" | "withdraw" | "recon" | null>(null);

  const load = useCallback(async () => {
    if (!activeAccount) return;
    setLoading(true);
    setError(null);
    try {
      const [ledger, accounts] = await Promise.all([
        api.ledger(activeAccount.id, 60),
        api.ledgerAccounts(activeAccount.id),
      ]);
      setStatement(ledger);
      setLedgerAccounts(accounts);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Unable to load the ledger.");
    } finally {
      setLoading(false);
    }
  }, [activeAccount?.id]);

  useEffect(() => {
    void load();
  }, [load]);

  if (!activeAccount) {
    return (
      <>
        <PageHeader title="Wallet" description="Virtual balances and the double-entry journal." />
        <EmptyState
          title="No trading account"
          description="Create a paper trading account to see virtual balances, deposits, withdrawals and ledger postings."
        />
      </>
    );
  }

  const account = activeAccount;
  const balance = account.balances[0];

  async function handleMovement(kind: "deposit" | "withdraw") {
    const value = Number(amount);
    if (!Number.isFinite(value) || value <= 0) {
      toast({ tone: "error", title: "Enter a positive amount", description: "Amounts must be greater than zero." });
      return;
    }
    setBusy(kind);
    try {
      if (kind === "deposit") await api.deposit(account.id, value);
      else await api.withdraw(account.id, value);
      toast({
        tone: "success",
        title: kind === "deposit" ? "Virtual deposit posted" : "Virtual withdrawal posted",
        description: `${formatMoney(value, balance.currency)} · balanced double-entry transaction`,
      });
      await Promise.all([load(), reload()]);
    } catch (err) {
      toast({
        tone: "error",
        title: "Transaction rejected",
        description: err instanceof Error ? err.message : "Please try again.",
      });
    } finally {
      setBusy(null);
    }
  }

  async function handleReconcile() {
    setBusy("recon");
    try {
      const record = await api.reconcile(account.id);
      setRecon(record);
      toast({
        tone: record.is_balanced ? "success" : "error",
        title: record.is_balanced ? "Ledger reconciled" : "Mismatch detected",
        description: record.notes,
      });
    } catch (err) {
      toast({ tone: "error", title: "Reconciliation failed", description: err instanceof Error ? err.message : "" });
    } finally {
      setBusy(null);
    }
  }

  return (
    <>
      <PageHeader
        eyebrow="Paper environment"
        title="Wallet"
        description="Virtual funding, withdrawals and the ledger that backs every balance. Real-money functionality is disabled in Phase 1."
        actions={
          <Button variant="secondary" onClick={() => void handleReconcile()} loading={busy === "recon"}>
            <IconRefresh /> Reconcile ledger
          </Button>
        }
      />

      <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
        <Stat label="Available cash" value={formatMoney(balance.cash, balance.currency)} hint="Derived from ledger entries" tone="accent" />
        <Stat label="Reserved" value={formatMoney(balance.reserved, balance.currency)} hint="Held against working orders" />
        <Stat label="Virtual equity" value={formatMoney(balance.equity, balance.currency)} hint="Cash less reserved" />
        <Stat
          label="Trial balance"
          value={statement ? formatMoney(statement.trial_balance, statement.currency) : "—"}
          hint="Must always be zero"
          tone={statement && statement.trial_balance === 0 ? "positive" : "negative"}
        />
      </div>

      <div className="mt-4 grid gap-4 lg:grid-cols-3">
        <Panel title="Virtual funding" subtitle="Simulated deposits and withdrawals only">
          <div className="space-y-4">
            <Field label="Amount" htmlFor="amount" hint={`Currency: ${balance.currency}. No real payments are processed.`}>
              <Input
                id="amount"
                type="number"
                min="1"
                step="100"
                value={amount}
                onChange={(event) => setAmount(event.target.value)}
                inputMode="decimal"
              />
            </Field>
            <div className="flex gap-2">
              <Button className="flex-1" onClick={() => void handleMovement("deposit")} loading={busy === "deposit"}>
                <IconArrowDown /> Virtual deposit
              </Button>
              <Button
                variant="secondary"
                className="flex-1"
                onClick={() => void handleMovement("withdraw")}
                loading={busy === "withdraw"}
              >
                <IconArrowUp /> Virtual withdraw
              </Button>
            </div>
            <div className="flex gap-2">
              {[1000, 5000, 25000].map((preset) => (
                <Button key={preset} size="sm" variant="ghost" onClick={() => setAmount(String(preset))}>
                  {formatMoney(preset, balance.currency, { decimals: 0 })}
                </Button>
              ))}
            </div>
            <p className="rounded-md border border-caution/25 bg-caution/[0.06] px-3 py-2 text-2xs leading-relaxed text-caution">
              Payment gateway integration is Phase 11 and requires a UK regulatory-perimeter
              assessment. Deposits here never touch a payment rail.
            </p>
          </div>
        </Panel>

        <Panel className="lg:col-span-2" title="Ledger accounts" subtitle="Chart of accounts for this trading account" bodyClassName="p-0">
          <table className="table-base">
            <thead>
              <tr>
                <th>Code</th>
                <th>Kind</th>
                <th>Type</th>
                <th className="text-right">Currency</th>
              </tr>
            </thead>
            <tbody>
              {ledgerAccounts.map((account) => (
                <tr key={account.id}>
                  <td className="font-mono text-xs text-ink-muted">{account.code}</td>
                  <td className="text-xs">{titleCase(account.kind)}</td>
                  <td>
                    <Badge tone={account.account_type === "asset" ? "positive" : account.account_type === "expense" ? "negative" : "neutral"}>
                      {account.account_type}
                    </Badge>
                  </td>
                  <td className="cell-num text-right">{account.currency}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </Panel>
      </div>

      <div className="mt-4 grid gap-4 lg:grid-cols-3">
        <Panel
          className="lg:col-span-2"
          title="Transaction statement"
          subtitle={statement ? `${statement.total} balanced transactions · immutable journal` : undefined}
          bodyClassName="p-0"
          actions={
            recon && (
              <Badge tone={recon.is_balanced ? "positive" : "negative"}>
                {recon.is_balanced ? "Reconciled" : "Mismatch"}
              </Badge>
            )
          }
        >
          {loading ? (
            <Spinner label="Loading statement" />
          ) : error ? (
            <div className="p-5">
              <ErrorNote message={error} onRetry={() => void load()} />
            </div>
          ) : statement && statement.transactions.length > 0 ? (
            <div className="overflow-x-auto">
              <table className="table-base">
                <thead>
                  <tr>
                    <th>Type</th>
                    <th>Description</th>
                    <th>Entries</th>
                    <th>Occurred</th>
                  </tr>
                </thead>
                <tbody>
                  {statement.transactions.map((txn) => (
                    <tr key={txn.id}>
                      <td>
                        <Badge tone={txn.transaction_type === "withdrawal" ? "negative" : "positive"}>
                          {titleCase(txn.transaction_type)}
                        </Badge>
                      </td>
                      <td className="text-xs text-ink-muted">{txn.description}</td>
                      <td>
                        <div className="flex flex-wrap gap-1">
                          {txn.entries.map((entry) => (
                            <span
                              key={entry.id}
                              className="rounded border border-line bg-surface-sunken px-1.5 py-0.5 font-mono text-[10px] text-ink-faint"
                              title="Signed amount: positive = debit, negative = credit"
                            >
                              {entry.amount > 0 ? "+" : ""}
                              {formatMoney(entry.amount, entry.currency)}
                            </span>
                          ))}
                        </div>
                      </td>
                      <td className="whitespace-nowrap text-xs text-ink-faint">
                        {formatDateTime(txn.occurred_at)}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ) : (
            <div className="p-5">
              <EmptyState
                title="No transactions"
                description="Post a virtual deposit to create the first balanced ledger transaction."
                icon={<IconWallet />}
              />
            </div>
          )}
        </Panel>

        <Panel title="Reconciliation" subtitle="Ledger vs cached balance">
          {recon ? (
            <ul className="space-y-2 text-xs">
              <li className="flex justify-between">
                <span className="text-ink-muted">Ledger cash</span>
                <span className="cell-num">{formatMoney(recon.ledger_cash, recon.currency)}</span>
              </li>
              <li className="flex justify-between">
                <span className="text-ink-muted">Cached balance</span>
                <span className="cell-num">{formatMoney(recon.cached_cash, recon.currency)}</span>
              </li>
              <li className="flex justify-between border-t border-line pt-2">
                <span className="text-ink-muted">Status</span>
                <Badge tone={recon.is_balanced ? "positive" : "negative"}>
                  {recon.is_balanced ? "Balanced" : "Mismatch"}
                </Badge>
              </li>
              <li className="pt-1 text-[11px] leading-relaxed text-ink-faint">{recon.notes}</li>
            </ul>
          ) : (
            <p className="text-xs leading-relaxed text-ink-muted">
              Run a reconciliation to compare the cached balance row against entries derived directly
              from the journal. The cached value is never authored by hand.
            </p>
          )}
          <p className="mt-4 rounded-md border border-line bg-surface-sunken px-3 py-2 text-[11px] leading-relaxed text-ink-faint">
            Corrections are posted as compensating transactions. Historical postings are never edited
            or deleted.
          </p>
        </Panel>
      </div>
    </>
  );
}
