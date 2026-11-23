import { describe, expect, it } from "vitest";
import { demoApi, DemoApiError } from "./engine";

// The demo engine reimplements the ledger contract used by the backend. These
// tests lock the accounting invariants so the static demo cannot drift from the
// service it demonstrates.
const memory = new Map<string, string>();
globalThis.localStorage = {
  getItem: (key: string) => memory.get(key) ?? null,
  setItem: (key: string, value: string) => void memory.set(key, value),
  removeItem: (key: string) => void memory.delete(key),
  clear: () => memory.clear(),
  key: () => null,
  length: 0,
} as Storage;

async function session(): Promise<string> {
  const tokens = await demoApi.login({ email: "demo@aurex.ai", password: "Demo!Aurex2026" });
  return tokens.access_token;
}

describe("demo API", () => {
  it("rejects invalid credentials", async () => {
    await expect(demoApi.login({ email: "demo@aurex.ai", password: "nope" })).rejects.toBeInstanceOf(
      DemoApiError,
    );
  });

  it("provisions a funded paper account on registration", async () => {
    const user = await demoApi.register({
      email: "new@aurex.ai",
      full_name: "New Trader",
      password: "Str0ngPass!",
    });
    expect(user.role).toBe("user");
    const tokens = await demoApi.login({ email: "new@aurex.ai", password: "Str0ngPass!" });
    const accounts = await demoApi.listAccounts(tokens.access_token);
    expect(accounts).toHaveLength(1);
    expect(accounts[0].balances[0].cash).toBe(100_000);
    expect(accounts[0].environment).toBe("paper");
  });

  it("keeps every ledger transaction balanced", async () => {
    const token = await session();
    const [account] = await demoApi.listAccounts(token);
    const statement = await demoApi.ledger(token, account.id);
    expect(statement.trial_balance).toBe(0);
    for (const txn of statement.transactions) {
      const sum = txn.entries.reduce((total, entry) => total + entry.amount, 0);
      expect(Math.round(sum * 1e8) / 1e8).toBe(0);
    }
  });

  it("applies virtual deposits and withdrawals to the balance", async () => {
    const token = await session();
    const [account] = await demoApi.listAccounts(token);
    const before = account.balances[0].cash;

    const deposited = await demoApi.deposit(token, account.id, 5000);
    expect(deposited.balances[0].cash).toBe(before + 5000);

    const withdrawn = await demoApi.withdraw(token, account.id, 2000);
    expect(withdrawn.balances[0].cash).toBe(before + 3000);
  });

  it("blocks withdrawals beyond available cash", async () => {
    const token = await session();
    const [account] = await demoApi.listAccounts(token);
    await expect(demoApi.withdraw(token, account.id, 999_999_999)).rejects.toBeInstanceOf(
      DemoApiError,
    );
  });

  it("prevents a standard user from reading administration data", async () => {
    const token = await session();
    await expect(demoApi.listUsers(token)).rejects.toBeInstanceOf(DemoApiError);
    await expect(demoApi.auditLogs(token)).rejects.toBeInstanceOf(DemoApiError);
  });

  it("reconciles the cached balance against the journal", async () => {
    const token = await session();
    const [account] = await demoApi.listAccounts(token);
    const record = await demoApi.reconcile(token, account.id);
    expect(record.is_balanced).toBe(true);
    expect(record.ledger_cash).toBe(record.cached_cash);
  });
});
