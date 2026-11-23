// The environment label must always be visible (blueprint section 8).
// A paper balance must never look like withdrawable real money.
import { Badge, Dot } from "./ui";
import { IS_DEMO } from "../services/api";

export function EnvironmentBanner({ compact = false }: { compact?: boolean }) {
  if (compact) {
    return (
      <span className="inline-flex items-center gap-1.5 rounded-full border border-caution/40 bg-caution/10 px-2.5 py-1 text-2xs font-semibold uppercase tracking-wider text-caution">
        <Dot tone="caution" pulse />
        Paper · virtual funds
      </span>
    );
  }

  return (
    <div className="flex flex-wrap items-center gap-2 rounded-lg border border-caution/25 bg-caution/[0.06] px-3 py-2 text-2xs text-caution">
      <Badge tone="caution">
        <Dot tone="caution" pulse />
        Paper environment
      </Badge>
      <span className="text-ink-muted">
        All balances are simulated virtual funds. Live trading and real-money transfers are disabled.
        Market data is real in the API build and synthetic in the static demo.
      </span>
      {IS_DEMO && (
        <span className="ml-auto rounded border border-line-strong px-1.5 py-0.5 font-semibold uppercase tracking-wider text-ink-faint">
          Static demo
        </span>
      )}
    </div>
  );
}
