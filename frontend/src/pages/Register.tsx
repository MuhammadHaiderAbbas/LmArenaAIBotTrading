import { useState, type FormEvent } from "react";
import { Link, useNavigate } from "react-router-dom";
import { useAuth } from "../features/auth/AuthContext";
import { Button, ErrorNote, Field, Input } from "../components/ui";
import { AuthShell } from "./Login";

const rules = [
  { test: (v: string) => v.length >= 10, label: "At least 10 characters" },
  { test: (v: string) => /[a-z]/.test(v), label: "One lowercase letter" },
  { test: (v: string) => /[A-Z]/.test(v), label: "One uppercase letter" },
  { test: (v: string) => /[0-9]/.test(v), label: "One digit" },
];

export function Register() {
  const { register } = useAuth();
  const navigate = useNavigate();
  const [form, setForm] = useState({ full_name: "", email: "", password: "" });
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function handleSubmit(event: FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError(null);
    try {
      await register(form);
      navigate("/overview", { replace: true });
    } catch (err) {
      setError(err instanceof Error ? err.message : "Unable to create your account.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <AuthShell
      title="Create your account"
      subtitle="A funded paper trading account is provisioned automatically."
      footer={
        <>
          Already registered?{" "}
          <Link to="/login" className="font-medium text-accent-soft hover:underline">
            Sign in
          </Link>
        </>
      }
    >
      <form onSubmit={handleSubmit} className="space-y-4" noValidate>
        {error && <ErrorNote message={error} />}
        <Field label="Full name" htmlFor="full_name">
          <Input
            id="full_name"
            required
            autoComplete="name"
            value={form.full_name}
            onChange={(event) => setForm({ ...form, full_name: event.target.value })}
            placeholder="Nadia Rahman"
          />
        </Field>
        <Field label="Email" htmlFor="email">
          <Input
            id="email"
            type="email"
            required
            autoComplete="email"
            value={form.email}
            onChange={(event) => setForm({ ...form, email: event.target.value })}
            placeholder="you@example.com"
          />
        </Field>
        <Field label="Password" htmlFor="password">
          <Input
            id="password"
            type="password"
            required
            autoComplete="new-password"
            value={form.password}
            onChange={(event) => setForm({ ...form, password: event.target.value })}
            placeholder="••••••••••"
          />
        </Field>

        <ul className="space-y-1.5 rounded-md border border-line bg-surface-sunken px-3 py-2.5">
          {rules.map((rule) => {
            const met = rule.test(form.password);
            return (
              <li key={rule.label} className="flex items-center gap-2 text-2xs">
                <span
                  className={
                    met
                      ? "flex h-3.5 w-3.5 items-center justify-center rounded-full bg-positive/20 text-positive"
                      : "flex h-3.5 w-3.5 items-center justify-center rounded-full bg-line text-ink-faint"
                  }
                  aria-hidden
                >
                  <svg width="9" height="9" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="3.5">
                    <path d="m5 13 4 4L19 7" strokeLinecap="round" strokeLinejoin="round" />
                  </svg>
                </span>
                <span className={met ? "text-ink-muted" : "text-ink-faint"}>{rule.label}</span>
                <span className="sr-only">{met ? "requirement met" : "requirement not met"}</span>
              </li>
            );
          })}
        </ul>

        <Button type="submit" className="w-full" loading={busy}>
          Create account &amp; open paper account
        </Button>
      </form>
    </AuthShell>
  );
}
