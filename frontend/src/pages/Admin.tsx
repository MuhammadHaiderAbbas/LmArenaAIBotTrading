import { useCallback, useEffect, useState } from "react";
import { Navigate } from "react-router-dom";
import { useAuth } from "../features/auth/AuthContext";
import { api } from "../services/api";
import { formatDateTime, relativeTime } from "../lib/format";
import {
  Badge,
  Button,
  EmptyState,
  ErrorNote,
  PageHeader,
  Panel,
  Spinner,
  Stat,
  useToast,
} from "../components/ui";
import type { AuditLog, SystemStatus, User, UserRole } from "../lib/types";

export function Admin() {
  const { user } = useAuth();
  const toast = useToast();
  const [tab, setTab] = useState<"users" | "audit" | "system">("users");
  const [users, setUsers] = useState<User[]>([]);
  const [logs, setLogs] = useState<AuditLog[]>([]);
  const [status, setStatus] = useState<SystemStatus | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const [userList, audit, systemStatus] = await Promise.all([
        api.listUsers(),
        api.auditLogs(100),
        api.systemStatus(),
      ]);
      setUsers(userList);
      setLogs(audit);
      setStatus(systemStatus);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Unable to load administration data.");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  if (user?.role !== "admin") return <Navigate to="/overview" replace />;

  async function changeRole(target: User, role: UserRole) {
    try {
      await api.updateUserRole(target.id, role);
      toast({ tone: "success", title: "Role updated", description: `${target.email} is now ${role}.` });
      await load();
    } catch (err) {
      toast({
        tone: "error",
        title: "Update rejected",
        description: err instanceof Error ? err.message : "Please try again.",
      });
    }
  }

  const tabs = [
    { id: "users" as const, label: "Users & roles" },
    { id: "audit" as const, label: "Audit trail" },
    { id: "system" as const, label: "System status" },
  ];

  return (
    <>
      <PageHeader
        eyebrow={`Signed in as ${user.email}`}
        title="Administration"
        description="Role-based access control, the append-only audit trail and platform health."
        actions={
          <Button variant="secondary" onClick={() => void load()} loading={loading}>
            Refresh
          </Button>
        }
      />

      {error && (
        <div className="mb-4">
          <ErrorNote message={error} onRetry={() => void load()} />
        </div>
      )}

      <div className="mb-4 grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
        <Stat label="Users" value={status?.counts.users ?? "—"} hint="All roles" />
        <Stat label="Trading accounts" value={status?.counts.trading_accounts ?? "—"} hint="Paper environment" />
        <Stat label="Ledger transactions" value={status?.counts.ledger_transactions ?? "—"} hint="Balanced postings" />
        <Stat label="Audit records" value={status?.counts.audit_logs ?? "—"} hint="Append-only" />
      </div>

      <div className="mb-4 flex gap-1 border-b border-line">
        {tabs.map((item) => (
          <button
            key={item.id}
            type="button"
            onClick={() => setTab(item.id)}
            className={
              tab === item.id
                ? "border-b-2 border-accent px-4 py-2.5 text-sm font-semibold text-white"
                : "border-b-2 border-transparent px-4 py-2.5 text-sm text-ink-muted hover:text-ink"
            }
            aria-pressed={tab === item.id}
          >
            {item.label}
          </button>
        ))}
      </div>

      {loading && users.length === 0 ? (
        <Spinner label="Loading administration data" />
      ) : tab === "users" ? (
        <Panel title="Users" subtitle="Role changes are audited immediately" bodyClassName="p-0" scrollBody>
          <div className="overflow-x-auto">
            <table className="table-base">
              <thead>
                <tr>
                  <th>Name</th>
                  <th>Email</th>
                  <th>Role</th>
                  <th>Status</th>
                  <th>Last sign-in</th>
                  <th className="text-right">Change role</th>
                </tr>
              </thead>
              <tbody>
                {users.map((row) => (
                  <tr key={row.id}>
                    <td className="text-xs font-medium">{row.full_name}</td>
                    <td className="font-mono text-xs text-ink-muted">{row.email}</td>
                    <td>
                      <Badge tone={row.role === "admin" ? "accent" : "neutral"}>{row.role}</Badge>
                    </td>
                    <td>
                      <Badge tone={row.is_active ? "positive" : "negative"}>
                        {row.is_active ? "active" : "suspended"}
                      </Badge>
                    </td>
                    <td className="text-xs text-ink-faint sm:whitespace-nowrap">
                      {row.last_login_at ? relativeTime(row.last_login_at) : "never"}
                    </td>
                    <td className="text-right">
                      <select
                        value={row.role}
                        onChange={(event) => void changeRole(row, event.target.value as UserRole)}
                        className="h-8 rounded-md border border-line-strong bg-surface-sunken px-2 text-xs text-ink"
                        aria-label={`Role for ${row.email}`}
                      >
                        <option value="user">user</option>
                        <option value="analyst">analyst</option>
                        <option value="admin">admin</option>
                      </select>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </Panel>
      ) : tab === "audit" ? (
        <Panel title="Audit trail" subtitle="Append-only record of sensitive actions" bodyClassName="p-0" scrollBody>
          {logs.length === 0 ? (
            <div className="p-5">
              <EmptyState title="No audit records" description="Sensitive actions are recorded automatically." />
            </div>
          ) : (
            <div className="max-h-[32rem] overflow-auto">
              <table className="table-base">
                <thead>
                  <tr>
                    <th>When</th>
                    <th>Actor</th>
                    <th>Action</th>
                    <th>Entity</th>
                    <th>Outcome</th>
                    <th>Detail</th>
                  </tr>
                </thead>
                <tbody>
                  {logs.map((log) => (
                    <tr key={log.id}>
                      <td className="text-xs text-ink-faint sm:whitespace-nowrap">{formatDateTime(log.created_at)}</td>
                      <td className="font-mono text-xs text-ink-muted">{log.actor_email ?? "system"}</td>
                      <td className="font-mono text-xs">{log.action}</td>
                      <td className="text-xs text-ink-faint">{log.entity_type ?? "—"}</td>
                      <td>
                        <Badge tone={log.outcome === "success" ? "positive" : log.outcome === "failure" ? "negative" : "caution"}>
                          {log.outcome}
                        </Badge>
                      </td>
                      <td className="max-w-[24rem] truncate text-xs text-ink-muted">{log.detail}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </Panel>
      ) : (
        <div className="grid gap-4 lg:grid-cols-2">
          <Panel title="System status" subtitle="Environment and counters">
            <dl className="space-y-2 text-xs">
              <div className="flex justify-between">
                <dt className="text-ink-muted">Environment</dt>
                <dd className="font-medium text-ink">{status?.environment}</dd>
              </div>
              <div className="flex justify-between">
                <dt className="text-ink-muted">Build</dt>
                <dd className="font-medium text-ink">{status?.version}</dd>
              </div>
              <div className="flex justify-between">
                <dt className="text-ink-muted">Session uptime</dt>
                <dd className="cell-num">{status?.uptime_seconds ?? 0}s</dd>
              </div>
              {Object.entries(status?.counts ?? {}).map(([key, value]) => (
                <div key={key} className="flex justify-between">
                  <dt className="text-ink-muted">{key.replace(/_/g, " ")}</dt>
                  <dd className="cell-num">{value}</dd>
                </div>
              ))}
            </dl>
          </Panel>

          <Panel title="Guardrails" subtitle="Compiled-in safety switches">
            <dl className="space-y-2 text-xs">
              {Object.entries(status?.guardrails ?? {}).map(([key, value]) => (
                <div key={key} className="flex justify-between gap-4">
                  <dt className="text-ink-muted">{key.replace(/_/g, " ")}</dt>
                  <dd className="text-right font-medium text-ink">{String(value)}</dd>
                </div>
              ))}
            </dl>
            <p className="mt-3 rounded-md border border-line bg-surface-sunken px-3 py-2 text-[11px] leading-relaxed text-ink-faint">
              Guardrails are environment configuration, not user preferences. They cannot be changed
              from the UI.
            </p>
          </Panel>
        </div>
      )}
    </>
  );
}
