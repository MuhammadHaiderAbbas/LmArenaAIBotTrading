import { useState, type FormEvent } from "react";
import { useAuth } from "../features/auth/AuthContext";
import { api, IS_DEMO } from "../services/api";
import { Badge, Button, ErrorNote, Field, Input, Panel, PageHeader, Select, useToast } from "../components/ui";
import { phases } from "../lib/demoStatic";
import { formatDateTime } from "../lib/format";
import type { Integration } from "../lib/types";

export function Settings() {
  const { user, refreshUser, meta } = useAuth();
  const toast = useToast();
  const [profile, setProfile] = useState({
    full_name: user?.full_name ?? "",
    base_currency: user?.preferences?.base_currency ?? "GBP",
    timezone: user?.preferences?.timezone ?? "Europe/London",
  });
  const [passwords, setPasswords] = useState({ current: "", next: "" });
  const [busy, setBusy] = useState<"profile" | "password" | null>(null);
  const [error, setError] = useState<string | null>(null);

  async function saveProfile(event: FormEvent) {
    event.preventDefault();
    setBusy("profile");
    setError(null);
    try {
      await api.updateProfile(profile);
      await refreshUser();
      toast({ tone: "success", title: "Profile updated", description: "Preferences saved to your account." });
    } catch (err) {
      setError(err instanceof Error ? err.message : "Unable to update profile.");
    } finally {
      setBusy(null);
    }
  }

  async function savePassword(event: FormEvent) {
    event.preventDefault();
    setBusy("password");
    setError(null);
    try {
      await api.changePassword(passwords.current, passwords.next);
      setPasswords({ current: "", next: "" });
      toast({
        tone: "success",
        title: "Password changed",
        description: "All other sessions have been revoked.",
      });
    } catch (err) {
      setError(err instanceof Error ? err.message : "Unable to change password.");
    } finally {
      setBusy(null);
    }
  }

  return (
    <>
      <PageHeader
        eyebrow="Account & platform"
        title="Settings & Administration"
        description="Profile, security, integrations, environment guardrails and the delivery roadmap."
      />

      {error && (
        <div className="mb-4">
          <ErrorNote message={error} />
        </div>
      )}

      <div className="grid gap-4 lg:grid-cols-3">
        <Panel title="Profile" subtitle="Displayed across the terminal">
          <form onSubmit={saveProfile} className="space-y-3">
            <Field label="Full name" htmlFor="full_name">
              <Input
                id="full_name"
                value={profile.full_name}
                onChange={(event) => setProfile({ ...profile, full_name: event.target.value })}
              />
            </Field>
            <Field label="Email" htmlFor="email-readonly" hint="Email changes require re-verification (Phase 11).">
              <Input id="email-readonly" value={user?.email ?? ""} readOnly className="opacity-60" />
            </Field>
            <Field label="Base currency" htmlFor="currency">
              <Select
                id="currency"
                value={profile.base_currency}
                onChange={(event) => setProfile({ ...profile, base_currency: event.target.value })}
              >
                {["GBP", "USD", "EUR", "JPY"].map((code) => (
                  <option key={code} value={code}>
                    {code}
                  </option>
                ))}
              </Select>
            </Field>
            <Field label="Timezone" htmlFor="tz">
              <Select
                id="tz"
                value={profile.timezone}
                onChange={(event) => setProfile({ ...profile, timezone: event.target.value })}
              >
                {["Europe/London", "UTC", "America/New_York", "Asia/Tokyo"].map((zone) => (
                  <option key={zone} value={zone}>
                    {zone}
                  </option>
                ))}
              </Select>
            </Field>
            <Button type="submit" loading={busy === "profile"}>
              Save profile
            </Button>
          </form>
        </Panel>

        <Panel title="Security" subtitle="Credentials and session control">
          <form onSubmit={savePassword} className="space-y-3">
            <Field label="Current password" htmlFor="current">
              <Input
                id="current"
                type="password"
                autoComplete="current-password"
                value={passwords.current}
                onChange={(event) => setPasswords({ ...passwords, current: event.target.value })}
                required
              />
            </Field>
            <Field
              label="New password"
              htmlFor="next"
              hint="Minimum 10 characters with upper, lower and a digit."
            >
              <Input
                id="next"
                type="password"
                autoComplete="new-password"
                value={passwords.next}
                onChange={(event) => setPasswords({ ...passwords, next: event.target.value })}
                required
              />
            </Field>
            <Button type="submit" variant="secondary" loading={busy === "password"}>
              Change password
            </Button>
          </form>

          <dl className="mt-4 space-y-2 border-t border-line pt-3 text-xs">
            <div className="flex justify-between">
              <dt className="text-ink-muted">Role</dt>
              <dd>
                <Badge tone={user?.role === "admin" ? "accent" : "neutral"}>{user?.role}</Badge>
              </dd>
            </div>
            <div className="flex justify-between">
              <dt className="text-ink-muted">MFA</dt>
              <dd className="text-ink-faint">Planned (Phase 11)</dd>
            </div>
            <div className="flex justify-between">
              <dt className="text-ink-muted">Last sign-in</dt>
              <dd className="text-ink-faint">
                {user?.last_login_at ? formatDateTime(user.last_login_at) : "—"}
              </dd>
            </div>
          </dl>
        </Panel>

        <Panel title="Environment" subtitle="Hard guardrails for this deployment">
          <dl className="space-y-2 text-xs">
            {[
              ["Environment", meta?.environment ?? "paper"],
              ["Execution mode", meta?.execution_mode ?? "paper"],
              ["Live trading", meta?.live_trading_enabled ? "ENABLED" : "Disabled"],
              ["Real money", meta?.real_money_enabled ? "ENABLED" : "Disabled"],
              ["Build", meta?.phase ?? "Phase 1"],
            ].map(([label, value]) => (
              <div key={label} className="flex items-center justify-between">
                <dt className="text-ink-muted">{label}</dt>
                <dd className="font-medium text-ink">{value}</dd>
              </div>
            ))}
          </dl>
          <p className="mt-3 rounded-md border border-caution/25 bg-caution/[0.06] px-3 py-2 text-[11px] leading-relaxed text-caution">
            Live trading and real-money transfers are compiled out of Phase 1 and require separate
            security, legal and regulatory gates.
          </p>
          {IS_DEMO && (
            <p className="mt-2 text-[11px] leading-relaxed text-ink-faint">
              This is a static demo build for GitHub Pages. All data lives in your browser only.
            </p>
          )}
        </Panel>
      </div>

      <div className="mt-4 grid gap-4 lg:grid-cols-2">
        <IntegrationsPanel />
        <Panel title="Delivery roadmap" subtitle="Twelve phases, each independently reviewable" bodyClassName="p-0">
          <ul className="max-h-[26rem] divide-y divide-line/70 overflow-y-auto">
            {phases.map((phase) => (
              <li key={phase.id} className="flex items-start gap-3 px-4 py-2.5">
                <span className="mt-0.5 font-mono text-[10px] text-ink-faint">
                  {String(phase.id).padStart(2, "0")}
                </span>
                <div className="min-w-0 flex-1">
                  <p className="text-xs font-medium text-ink">{phase.name}</p>
                  <p className="text-[10px] leading-relaxed text-ink-faint">{phase.scope}</p>
                </div>
                <Badge tone={phase.status === "complete" ? "positive" : "muted"}>
                  {phase.status === "complete" ? "Done" : "Planned"}
                </Badge>
              </li>
            ))}
          </ul>
        </Panel>
      </div>
    </>
  );
}

function IntegrationsPanel() {
  const { user } = useAuth();
  const [items, setItems] = useState<Integration[] | null>(null);
  const [loading, setLoading] = useState(false);

  async function load() {
    setLoading(true);
    try {
      setItems(await api.integrations());
    } catch {
      setItems([]);
    } finally {
      setLoading(false);
    }
  }

  if (user?.role !== "admin") {
    return (
      <Panel title="Integrations" subtitle="Administrator visibility only">
        <p className="text-xs leading-relaxed text-ink-muted">
          Provider status is visible to administrators. Standard users see integration health through
          operational notices.
        </p>
      </Panel>
    );
  }

  return (
    <Panel
      title="Integrations"
      subtitle="Provider status — none configured in Phase 1"
      actions={
        <Button size="sm" variant="secondary" onClick={() => void load()} loading={loading}>
          Check status
        </Button>
      }
      bodyClassName="p-0"
    >
      {items === null ? (
        <p className="px-5 py-4 text-xs text-ink-muted">
          Run a status check to read configured provider health.
        </p>
      ) : items.length === 0 ? (
        <p className="px-5 py-4 text-xs text-ink-muted">No integration records available.</p>
      ) : (
        <ul className="divide-y divide-line/70">
          {items.map((item) => (
            <li key={item.provider} className="flex items-start justify-between gap-3 px-5 py-3">
              <div>
                <p className="font-mono text-xs text-ink">{item.provider}</p>
                <p className="text-[10px] text-ink-faint">{item.message}</p>
              </div>
              <Badge tone={item.status === "ok" ? "positive" : item.status === "error" ? "negative" : "muted"}>
                {item.status.replace(/_/g, " ")}
              </Badge>
            </li>
          ))}
        </ul>
      )}
    </Panel>
  );
}
