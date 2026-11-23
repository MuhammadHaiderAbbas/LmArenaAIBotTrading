import { useMemo, useState } from "react";
import { Area, AreaChart, ResponsiveContainer } from "recharts";
import { markets } from "../lib/demoStatic";
import { formatPercent } from "../lib/format";
import { Badge, EmptyState, Input, PageHeader, Panel } from "../components/ui";
import { IconSearch } from "../lib/icons";

const CLASSES = ["All", "Crypto", "Forex", "Equity", "ETF"] as const;

export function Markets() {
  const [assetClass, setAssetClass] = useState<(typeof CLASSES)[number]>("All");
  const [query, setQuery] = useState("");

  const rows = useMemo(
    () =>
      markets.filter((market) => {
        const matchesClass = assetClass === "All" || market.assetClass === assetClass;
        const matchesQuery =
          !query ||
          market.symbol.toLowerCase().includes(query.toLowerCase()) ||
          market.name.toLowerCase().includes(query.toLowerCase());
        return matchesClass && matchesQuery;
      }),
    [assetClass, query],
  );

  return (
    <>
      <PageHeader
        eyebrow="Market intelligence"
        title="Markets"
        description="Instrument registry across four asset classes. Reference prices are shown until licensed provider adapters connect in Phase 3."
        actions={<Badge tone="muted">Phase 3 activates live data</Badge>}
      />

      <Panel bodyClassName="p-0">
        <div className="flex flex-wrap items-center gap-2 border-b border-line px-4 py-3">
          <div className="flex flex-wrap gap-1">
            {CLASSES.map((item) => (
              <button
                key={item}
                type="button"
                onClick={() => setAssetClass(item)}
                className={
                  assetClass === item
                    ? "rounded-md border border-accent/40 bg-accent/15 px-3 py-1.5 text-xs font-semibold text-accent-soft"
                    : "rounded-md border border-transparent px-3 py-1.5 text-xs font-medium text-ink-muted hover:bg-surface-raised hover:text-ink"
                }
                aria-pressed={assetClass === item}
              >
                {item}
              </button>
            ))}
          </div>
          <div className="relative ml-auto w-full sm:w-64">
            <span className="pointer-events-none absolute left-2.5 top-2.5 text-ink-faint">
              <IconSearch width={16} height={16} />
            </span>
            <Input
              className="h-9 pl-8"
              placeholder="Search instruments…"
              value={query}
              onChange={(event) => setQuery(event.target.value)}
              aria-label="Search instruments"
            />
          </div>
        </div>

        {rows.length === 0 ? (
          <div className="p-5">
            <EmptyState title="No instruments match" description="Adjust the asset-class filter or clear the search." />
          </div>
        ) : (
          <div className="overflow-x-auto">
            <table className="table-base">
              <thead>
                <tr>
                  <th>Instrument</th>
                  <th>Class</th>
                  <th>Venue</th>
                  <th className="text-right">Last</th>
                  <th className="text-right">Change</th>
                  <th className="text-right">30d</th>
                  <th>Session</th>
                </tr>
              </thead>
              <tbody>
                {rows.map((market) => (
                  <tr key={market.symbol}>
                    <td>
                      <p className="font-mono text-xs font-semibold text-ink">{market.symbol}</p>
                      <p className="text-[10px] text-ink-faint">{market.name}</p>
                    </td>
                    <td>
                      <Badge tone={market.assetClass === "Crypto" ? "accent" : "neutral"}>
                        {market.assetClass}
                      </Badge>
                    </td>
                    <td className="text-xs text-ink-muted">{market.venue}</td>
                    <td className="cell-num text-right">
                      {market.price.toLocaleString("en-GB", { maximumFractionDigits: 4 })}
                    </td>
                    <td
                      className={
                        market.changePct >= 0
                          ? "cell-num text-right text-positive"
                          : "cell-num text-right text-negative"
                      }
                    >
                      {formatPercent(market.changePct)}
                    </td>
                    <td className="w-28">
                      <div className="h-8">
                        <ResponsiveContainer width="100%" height="100%">
                          <AreaChart data={market.spark.map((value, index) => ({ index, value }))}>
                            <Area
                              type="monotone"
                              dataKey="value"
                              stroke={market.changePct >= 0 ? "#25D19A" : "#FF5F7E"}
                              fill={market.changePct >= 0 ? "#25D19A22" : "#FF5F7E22"}
                              strokeWidth={1.4}
                              isAnimationActive={false}
                            />
                          </AreaChart>
                        </ResponsiveContainer>
                      </div>
                    </td>
                    <td>
                      <Badge tone={market.session === "24/7" ? "accent" : market.session === "Open" ? "positive" : "muted"}>
                        {market.session}
                      </Badge>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Panel>

      <div className="mt-4 grid gap-4 lg:grid-cols-3">
        <Panel title="What Phase 3 delivers" subtitle="Provider adapters and streaming" className="lg:col-span-2">
          <ul className="grid gap-2 text-xs text-ink-muted sm:grid-cols-2">
            {[
              "Instrument registry with normalised identifiers",
              "Historical OHLCV ingestion with backfill validation",
              "Streaming quotes and candlestick updates",
              "Data-quality events for gaps, staleness and outliers",
              "Trading calendars and session awareness",
              "Candlestick charts with indicator overlays",
            ].map((item) => (
              <li key={item} className="flex items-start gap-2 rounded-md border border-line bg-surface-sunken px-3 py-2">
                <span className="mt-1.5 h-1 w-1 shrink-0 rounded-full bg-accent" aria-hidden />
                {item}
              </li>
            ))}
          </ul>
        </Panel>
        <Panel title="Data discipline" subtitle="Non-negotiable">
          <ul className="space-y-2 text-xs">
            {[
              ["Freshness", "Reject trading on stale quotes"],
              ["Quality", "Flag gaps and outliers before use"],
              ["Timezone", "Explicit UTC storage, London display"],
              ["Licensing", "Respect provider terms and entitlements"],
            ].map(([label, value]) => (
              <li key={label} className="flex items-start justify-between gap-3">
                <span className="text-ink-muted">{label}</span>
                <span className="text-right font-medium text-ink">{value}</span>
              </li>
            ))}
          </ul>
        </Panel>
      </div>
    </>
  );
}
