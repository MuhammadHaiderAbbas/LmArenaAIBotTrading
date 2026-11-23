import { useState, type FormEvent } from "react";
import { Link, useLocation, useNavigate } from "react-router-dom";
import { useAuth } from "../features/auth/AuthContext";
import { IS_DEMO } from "../services/api";
import { Button, Field, Input, ErrorNote } from "../components/ui";
import { DEMO_CREDENTIALS } from "../lib/demo/seed";

export function Login() {
  const { login } = useAuth();
  const navigate = useNavigate();
  const location = useLocation();
  const [email, setEmail] = useState(IS_DEMO ? DEMO_CREDENTIALS.email : "");
  const [password, setPassword] = useState(IS_DEMO ? DEMO_CREDENTIALS.password : "");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const redirectTo = (location.state as { from?: string } | null)?.from ?? "/overview";

  async function handleSubmit(event: FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError(null);
    try {
      await login(email, password);
      navigate(redirectTo, { replace: true });
    } catch (err) {
      setError(err instanceof Error ? err.message : "Unable to sign in.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <AuthShell
      title="Sign in"
      subtitle="Access the AUREX AI paper trading terminal."
      footer={
        <>
          No account?{" "}
          <Link to="/register" className="font-medium text-accent-soft hover:underline">
            Create one
          </Link>
        </>
      }
    >
      <form onSubmit={handleSubmit} className="space-y-4" noValidate>
        {error && <ErrorNote message={error} />}
        <Field label="Email" htmlFor="email">
          <Input
            id="email"
            type="email"
            autoComplete="email"
            required
            value={email}
            onChange={(event) => setEmail(event.target.value)}
            placeholder="you@example.com"
          />
        </Field>
        <Field label="Password" htmlFor="password">
          <Input
            id="password"
            type="password"
            autoComplete="current-password"
            required
            value={password}
            onChange={(event) => setPassword(event.target.value)}
            placeholder="••••••••••"
          />
        </Field>
        <Button type="submit" className="w-full" loading={busy}>
          Sign in
        </Button>
        {IS_DEMO && (
          <p className="rounded-md border border-line bg-surface-sunken px-3 py-2 text-2xs text-ink-muted">
            <strong className="text-ink">Static demo.</strong> Credentials are pre-filled. Register
            your own account to create a fresh funded paper account — data is stored only in this
            browser.
          </p>
        )}
      </form>
    </AuthShell>
  );
}

export function AuthShell({
  title,
  subtitle,
  children,
  footer,
}: {
  title: string;
  subtitle: string;
  children: React.ReactNode;
  footer?: React.ReactNode;
}) {
  return (
    <div className="grid min-h-screen lg:grid-cols-[1.05fr_1fr]">
      {/* Brand / value panel */}
      <div className="relative hidden overflow-hidden border-r border-line bg-surface-sunken lg:flex lg:flex-col lg:justify-between lg:p-12">
        <div className="grid-backdrop absolute inset-0" aria-hidden />
        <div className="relative">
          <div className="flex items-center gap-2.5">
            <span className="flex h-9 w-9 items-center justify-center rounded-lg border border-accent/40 bg-accent/10">
              <svg width="20" height="20" viewBox="0 0 32 32" aria-hidden>
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
            <div>
              <p className="text-sm font-semibold tracking-[0.18em] text-white">AUREX AI</p>
              <p className="text-[10px] uppercase tracking-[0.2em] text-ink-faint">
                Autonomous trading · financial intelligence
              </p>
            </div>
          </div>

          <h1 className="mt-14 max-w-md text-3xl font-semibold leading-tight tracking-tight text-white">
            A multi-market research desk and deterministic risk engine — in one terminal.
          </h1>
          <p className="mt-4 max-w-md text-sm leading-relaxed text-ink-muted">
            Specialist AI agents propose. An independent risk controller decides. Every movement of
            value is recorded in a reconciled double-entry ledger — so any result can be reproduced
            from data, model versions and decisions.
          </p>

          <ul className="mt-8 space-y-3">
            {[
              "Paper trading only — real money and live execution are disabled by design",
              "Double-entry ledger with per-account reconciliation",
              "Source-linked research with uncertainty and contradictions surfaced",
              "Role-based access, audited actions and an independent emergency stop",
            ].map((item) => (
              <li key={item} className="flex items-start gap-2.5 text-sm text-ink-muted">
                <svg
                  width="16"
                  height="16"
                  viewBox="0 0 24 24"
                  fill="none"
                  stroke="#25D19A"
                  strokeWidth="2.2"
                  strokeLinecap="round"
                  strokeLinejoin="round"
                  className="mt-0.5 shrink-0"
                  aria-hidden
                >
                  <path d="m5 13 4 4L19 7" />
                </svg>
                {item}
              </li>
            ))}
          </ul>
        </div>

        <p className="relative text-2xs leading-relaxed text-ink-faint">
          Phase 1 foundation. Not investment advice. Historical and simulated results do not
          guarantee live outcomes. AUREX AI is a development environment using virtual capital.
        </p>
      </div>

      {/* Form panel */}
      <div className="flex items-center justify-center px-5 py-12 sm:px-8">
        <div className="w-full max-w-sm">
          <div className="mb-8 lg:hidden">
            <p className="text-sm font-semibold tracking-[0.18em] text-white">AUREX AI</p>
            <p className="text-[10px] uppercase tracking-[0.2em] text-ink-faint">
              Autonomous trading · financial intelligence
            </p>
          </div>
          <h2 className="text-xl font-semibold tracking-tight text-white">{title}</h2>
          <p className="mt-1.5 text-sm text-ink-muted">{subtitle}</p>
          <div className="mt-7">{children}</div>
          {footer && <div className="mt-6 text-sm text-ink-muted">{footer}</div>}
          <p className="mt-8 rounded-md border border-caution/25 bg-caution/[0.06] px-3 py-2 text-2xs leading-relaxed text-caution">
            Paper environment · virtual funds only. Live trading and real-money transfers are
            disabled in Phase 1.
          </p>
        </div>
      </div>
    </div>
  );
}
