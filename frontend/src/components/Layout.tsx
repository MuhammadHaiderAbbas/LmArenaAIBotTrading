import { useEffect, useState, type ReactNode } from "react";
import { NavLink, Outlet, useLocation, useNavigate } from "react-router-dom";
import clsx from "clsx";
import { useAuth } from "../features/auth/AuthContext";
import { useAccounts } from "../features/account/AccountContext";
import { api } from "../services/api";
import { formatMoney, initials } from "../lib/format";
import type { Notification } from "../lib/types";
import { Badge, Button, Dot } from "./ui";
import { EnvironmentBanner } from "./EnvironmentBanner";
import {
  IconBell,
  IconBot,
  IconCandles,
  IconCheck,
  IconChart,
  IconCpu,
  IconFlask,
  IconGrid,
  IconHistory,
  IconLayers,
  IconLogout,
  IconSettings,
  IconShield,
  IconWallet,
} from "../lib/icons";

export interface NavItem {
  to: string;
  label: string;
  icon: ReactNode;
  phase?: number;
  requiresAdmin?: boolean;
}

export const NAV_ITEMS: NavItem[] = [
  { to: "/overview", label: "Overview", icon: <IconGrid /> },
  { to: "/markets", label: "Markets", icon: <IconCandles />, phase: 3 },
  { to: "/trading", label: "Execution", icon: <IconBot />, phase: 8 },
  { to: "/research", label: "AI Research Lab", icon: <IconFlask />, phase: 4 },
  { to: "/ai", label: "AI Desk", icon: <IconCpu />, phase: 5 },
  { to: "/models", label: "Models", icon: <IconLayers />, phase: 6 },
  { to: "/portfolio", label: "Portfolio", icon: <IconChart /> },
  { to: "/risk", label: "Risk Centre", icon: <IconShield />, phase: 9 },
  { to: "/wallet", label: "Wallet", icon: <IconWallet /> },
  { to: "/history", label: "Trade History", icon: <IconHistory />, phase: 8 },
  { to: "/analytics", label: "Analytics", icon: <IconChart />, phase: 10 },
  { to: "/operations", label: "Operations", icon: <IconShield />, phase: 11 },
  { to: "/readiness", label: "Readiness", icon: <IconCheck />, phase: 12 },
  { to: "/settings", label: "Settings & Admin", icon: <IconSettings />, requiresAdmin: false },
];

function Logo() {
  return (
    <div className="flex items-center gap-2.5">
      <span className="flex h-8 w-8 items-center justify-center rounded-lg border border-accent/40 bg-accent/10">
        <svg width="18" height="18" viewBox="0 0 32 32" aria-hidden>
          <path
            d="M6 24l6-14 4 9 3-5 7 10"
            fill="none"
            stroke="#7FB0FF"
            strokeWidth="2.4"
            strokeLinecap="round"
            strokeLinejoin="round"
          />
        </svg>
      </span>
      <span className="leading-tight">
        <span className="block text-sm font-semibold tracking-[0.16em] text-white">AUREX</span>
        <span className="block text-[10px] font-medium uppercase tracking-[0.18em] text-ink-faint">
          Financial Intelligence
        </span>
      </span>
    </div>
  );
}

function AccountSwitcher() {
  const { accounts, activeAccount, setActiveId } = useAccounts();
  if (accounts.length === 0) return null;
  return (
    <label className="flex flex-col gap-1">
      <span className="label">Trading account</span>
      <select
        value={activeAccount?.id ?? ""}
        onChange={(event) => setActiveId(event.target.value)}
        className="h-9 w-full rounded-md border border-line-strong bg-surface-sunken px-2 text-xs text-ink"
        aria-label="Select trading account"
      >
        {accounts.map((account) => (
          <option key={account.id} value={account.id}>
            {account.name} · {account.base_currency}
          </option>
        ))}
      </select>
    </label>
  );
}

export function Layout() {
  const { user, logout, meta } = useAuth();
  const { activeAccount } = useAccounts();
  const navigate = useNavigate();
  const location = useLocation();
  const [notifications, setNotifications] = useState<Notification[]>([]);
  const [navOpen, setNavOpen] = useState(false);

  useEffect(() => {
    api
      .notifications()
      .then(setNotifications)
      .catch(() => setNotifications([]));
  }, []);

  useEffect(() => {
    setNavOpen(false);
  }, [location.pathname]);

  const unread = notifications.filter((n) => !n.is_read).length;
  const balance = activeAccount?.balances[0];

  return (
    <div className="flex min-h-full">
      <a href="#main" className="skip-link">
        Skip to main content
      </a>

      {/* Sidebar */}
      <aside
        className={clsx(
          "fixed inset-y-0 left-0 z-40 flex w-64 flex-col border-r border-line bg-surface-sunken transition-transform lg:static lg:translate-x-0",
          navOpen ? "translate-x-0" : "-translate-x-full",
        )}
        aria-label="Primary"
      >
        <div className="flex h-16 items-center border-b border-line px-5">
          <Logo />
        </div>

        <nav className="flex-1 overflow-y-auto px-3 py-4">
          <p className="label px-2 pb-2">Terminal</p>
          <ul className="space-y-0.5">
            {NAV_ITEMS.map((item) => (
              <li key={item.to}>
                <NavLink
                  to={item.to}
                  className={({ isActive }) =>
                    clsx(
                      "flex items-center gap-2.5 rounded-md px-2.5 py-2 text-sm transition-colors",
                      isActive
                        ? "bg-accent/12 text-white ring-1 ring-inset ring-accent/25"
                        : "text-ink-muted hover:bg-surface-raised hover:text-ink",
                    )
                  }
                >
                  <span className="text-ink-faint">{item.icon}</span>
                  <span className="flex-1">{item.label}</span>
                  {item.phase && (
                    <span
                      className="rounded border border-line-strong px-1 py-0.5 font-mono text-[9px] text-ink-faint"
                      title={`Activated in Phase ${item.phase}`}
                    >
                      P{item.phase}
                    </span>
                  )}
                </NavLink>
              </li>
            ))}
            {user?.role === "admin" && (
              <li>
                <NavLink
                  to="/admin"
                  className={({ isActive }) =>
                    clsx(
                      "flex items-center gap-2.5 rounded-md px-2.5 py-2 text-sm transition-colors",
                      isActive
                        ? "bg-accent/12 text-white ring-1 ring-inset ring-accent/25"
                        : "text-ink-muted hover:bg-surface-raised hover:text-ink",
                    )
                  }
                >
                  <span className="text-ink-faint">
                    <IconShield />
                  </span>
                  <span className="flex-1">Administration</span>
                  <Badge tone="accent">Admin</Badge>
                </NavLink>
              </li>
            )}
          </ul>
        </nav>

        <div className="space-y-3 border-t border-line p-4">
          <AccountSwitcher />
          {balance && (
            <div className="rounded-lg border border-line bg-surface p-3">
              <p className="label">Virtual balance</p>
              <p className="mt-1 font-mono text-lg font-semibold tabular-nums text-white">
                {formatMoney(balance.cash, balance.currency)}
              </p>
              <p className="mt-0.5 text-[10px] uppercase tracking-wider text-caution">
                Paper · not withdrawable
              </p>
            </div>
          )}
          <div className="flex items-center gap-2.5 rounded-lg border border-line bg-surface px-3 py-2.5">
            <span className="flex h-8 w-8 shrink-0 items-center justify-center rounded-full bg-accent/15 text-xs font-semibold text-accent-soft">
              {initials(user?.full_name ?? "A U")}
            </span>
            <div className="min-w-0 flex-1">
              <p className="truncate text-xs font-medium text-ink">{user?.full_name}</p>
              <p className="truncate text-[10px] uppercase tracking-wider text-ink-faint">
                {user?.role}
              </p>
            </div>
            <button
              type="button"
              onClick={() => void logout().then(() => navigate("/login"))}
              className="rounded p-1.5 text-ink-faint transition-colors hover:bg-surface-hover hover:text-negative"
              aria-label="Sign out"
              title="Sign out"
            >
              <IconLogout />
            </button>
          </div>
        </div>
      </aside>

      {navOpen && (
        <button
          type="button"
          aria-label="Close navigation"
          onClick={() => setNavOpen(false)}
          className="fixed inset-0 z-30 bg-black/60 lg:hidden"
        />
      )}

      {/* Main column */}
      <div className="flex min-w-0 flex-1 flex-col">
        <header className="sticky top-0 z-20 border-b border-line bg-canvas/85 backdrop-blur">
          <div className="flex h-16 items-center gap-3 px-4 sm:px-6">
            <button
              type="button"
              onClick={() => setNavOpen(true)}
              className="rounded-md border border-line-strong p-2 text-ink-muted lg:hidden"
              aria-label="Open navigation"
            >
              <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" aria-hidden>
                <path d="M4 7h16M4 12h16M4 17h16" strokeLinecap="round" />
              </svg>
            </button>

            <EnvironmentBanner compact />

            <div className="ml-auto flex items-center gap-2">
              <span className="hidden items-center gap-1.5 text-2xs text-ink-faint sm:flex">
                <Dot tone="positive" pulse />
                Systems operational
              </span>
              <div className="relative">
                <button
                  type="button"
                  className="relative rounded-md border border-line-strong p-2 text-ink-muted transition-colors hover:text-ink"
                  aria-label={`Notifications (${unread} unread)`}
                  title="Notifications"
                >
                  <IconBell />
                  {unread > 0 && (
                    <span className="absolute -right-1 -top-1 flex h-4 min-w-4 items-center justify-center rounded-full bg-accent px-1 text-[9px] font-bold text-white">
                      {unread}
                    </span>
                  )}
                </button>
              </div>
              <Badge tone="muted" className="hidden md:inline-flex">
                {meta?.version ?? "v0.1.0"}
              </Badge>
            </div>
          </div>
        </header>

        <main id="main" className="grid-backdrop flex-1 px-4 py-6 sm:px-6 lg:px-8">
          <div className="mx-auto w-full max-w-[1400px]">
            <div className="mb-5">
              <EnvironmentBanner />
            </div>
            <Outlet />
          </div>
        </main>

        <footer className="border-t border-line px-4 py-4 text-2xs text-ink-faint sm:px-6">
          <div className="mx-auto flex max-w-[1400px] flex-wrap items-center justify-between gap-2">
            <p>
              AUREX AI · Phases 1–10 · Paper trading only. No real money, no broker connection, no
              performance claims.
            </p>
            <p>
              Historical and simulated results do not guarantee live outcomes. Not investment advice.
            </p>
          </div>
        </footer>
      </div>
    </div>
  );
}

export function RequireAuth({ children }: { children: ReactNode }) {
  const { user, loading } = useAuth();
  const location = useLocation();
  if (loading) {
    return (
      <div className="flex min-h-screen items-center justify-center">
        <div className="flex items-center gap-3 text-sm text-ink-muted">
          <span className="h-4 w-4 animate-spin rounded-full border-2 border-accent border-t-transparent" />
          Verifying session…
        </div>
        <span className="sr-only">Loading {location.pathname}</span>
      </div>
    );
  }
  if (!user) {
    return <NavigateToLogin />;
  }
  return <>{children}</>;
}

function NavigateToLogin() {
  const navigate = useNavigate();
  useEffect(() => {
    navigate("/login", { replace: true });
  }, [navigate]);
  return null;
}

export function SignOutButton() {
  const { logout } = useAuth();
  const navigate = useNavigate();
  return (
    <Button variant="secondary" onClick={() => void logout().then(() => navigate("/login"))}>
      Sign out
    </Button>
  );
}
