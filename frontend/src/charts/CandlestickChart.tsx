// Candlestick chart drawn as SVG.
//
// A charting library would add ~150 kB to render one candle style, and the
// GitHub Pages build renders in a sandboxed iframe with no network. Drawing it
// directly also gives exact control over two things that matter here:
//  - a distinct treatment for the forming (non-final) bar, so nobody mistakes a
//    partial bar for a closed one;
//  - the last-price line, which is what a trader checks first.

import { useId, useMemo } from "react";
import type { Candle } from "../lib/marketTypes";

interface Props {
  candles: Candle[];
  height?: number;
  showVolume?: boolean;
  overlays?: Array<{ values: (number | null)[]; colour: string; label: string; dashed?: boolean }>;
  symbol: string;
}

export function CandlestickChart({
  candles,
  height = 320,
  showVolume = true,
  overlays = [],
  symbol,
}: Props) {
  const clipId = useId().replace(/[:]/g, "");
  const volumeHeight = showVolume ? 56 : 0;
  const padding = { top: 12, right: 56, bottom: 20, left: 8 };
  const plotHeight = height - volumeHeight - padding.top - padding.bottom;

  const geometry = useMemo(() => {
    if (!candles.length) return null;
    const lows = candles.map((candle) => candle.low);
    const highs = candles.map((candle) => candle.high);
    for (const overlay of overlays) {
      for (const value of overlay.values) {
        if (value === null) continue;
        lows.push(value);
        highs.push(value);
      }
    }
    let min = Math.min(...lows);
    let max = Math.max(...highs);
    if (!Number.isFinite(min) || !Number.isFinite(max) || min === max) {
      min = min - 0.5;
      max = max + 0.5;
    }
    const pad = (max - min) * 0.08;
    min -= pad;
    max += pad;
    return { min, max, step: (max - min) / Math.max(plotHeight, 1) };
  }, [candles, overlays, plotHeight]);

  if (!candles.length || !geometry) {
    return (
      <div
        className="flex items-center justify-center rounded-md border border-line bg-surface-sunken text-xs text-ink-faint"
        style={{ height }}
      >
        No candles available for {symbol}
      </div>
    );
  }

  const width = 1000; // viewBox units; the SVG scales to its container
  const plotWidth = width - padding.left - padding.right;
  const slot = plotWidth / candles.length;
  const bodyWidth = Math.max(Math.min(slot * 0.62, 14), 1.2);
  const y = (value: number) => padding.top + ((geometry.max - value) / (geometry.max - geometry.min)) * plotHeight;

  const lastCandle = candles[candles.length - 1];
  const previousCandle = candles[candles.length - 2] ?? lastCandle;
  const up = lastCandle.close >= previousCandle.close;
  const lineColour = up ? "#25D19A" : "#FF5F7E";

  const volumeMax = Math.max(...candles.map((candle) => candle.volume), 1);
  const volumeTop = padding.top + plotHeight + 10;

  // Price gridlines: five levels is enough to read the chart without clutter.
  const ticks = Array.from({ length: 5 }, (_, index) => {
    const value = geometry.min + ((geometry.max - geometry.min) / 4) * index;
    return { value, y: y(value) };
  });

  return (
    <svg
      viewBox={`0 0 ${width} ${height}`}
      className="h-auto w-full"
      role="img"
      aria-label={`${symbol} candlestick chart, ${candles.length} bars, last price ${lastCandle.close.toFixed(4)}`}
      style={{ maxHeight: height }}
    >
      <defs>
        <clipPath id={clipId}>
          <rect x={padding.left} y={padding.top} width={plotWidth} height={plotHeight} />
        </clipPath>
      </defs>

      {ticks.map((tick) => (
        <g key={tick.value}>
          <line
            x1={padding.left}
            x2={width - padding.right}
            y1={tick.y}
            y2={tick.y}
            stroke="#1E2A38"
            strokeWidth={1}
            strokeDasharray="2 4"
          />
          <text
            x={width - padding.right + 6}
            y={tick.y + 3}
            fontSize={9}
            fill="#6C7B8C"
            fontFamily="ui-monospace, monospace"
          >
            {tick.value.toLocaleString("en-GB", { maximumFractionDigits: tick.value > 100 ? 0 : 4 })}
          </text>
        </g>
      ))}

      <g clipPath={`url(#${clipId})`}>
        {candles.map((candle, index) => {
          const x = padding.left + slot * index + slot / 2;
          const rising = candle.close >= candle.open;
          const colour = rising ? "#25D19A" : "#FF5F7E";
          const bodyTop = y(Math.max(candle.open, candle.close));
          const bodyBottom = y(Math.min(candle.open, candle.close));
          // The most recent bar is still forming: draw it hollow and dimmed so it
          // cannot be mistaken for a settled print.
          const forming = !candle.is_final || index === candles.length - 1;
          return (
            <g key={candle.open_time} opacity={forming ? 0.55 : 1}>
              <line x1={x} x2={x} y1={y(candle.high)} y2={y(candle.low)} stroke={colour} strokeWidth={1} />
              <rect
                x={x - bodyWidth / 2}
                y={bodyTop}
                width={bodyWidth}
                height={Math.max(bodyBottom - bodyTop, 1)}
                fill={forming ? "transparent" : colour}
                stroke={colour}
                strokeWidth={1}
              />
            </g>
          );
        })}

        {overlays.map((overlay) => {
          const points = overlay.values
            .map((value, index) => {
              if (value === null) return null;
              const x = padding.left + slot * index + slot / 2;
              return `${x},${y(value)}`;
            })
            .filter((point): point is string => point !== null)
            .join(" ");
          if (!points) return null;
          return (
            <polyline
              key={overlay.label}
              points={points}
              fill="none"
              stroke={overlay.colour}
              strokeWidth={1.3}
              strokeDasharray={overlay.dashed ? "4 3" : undefined}
              opacity={0.9}
            />
          );
        })}
      </g>

      {/* Last price line */}
      <line
        x1={padding.left}
        x2={width - padding.right}
        y1={y(lastCandle.close)}
        y2={y(lastCandle.close)}
        stroke={lineColour}
        strokeWidth={1}
        strokeDasharray="5 4"
        opacity={0.8}
      />
      <rect
        x={width - padding.right + 2}
        y={y(lastCandle.close) - 7}
        width={padding.right - 6}
        height={14}
        rx={2}
        fill={lineColour}
      />
      <text
        x={width - padding.right + 5}
        y={y(lastCandle.close) + 3.5}
        fontSize={9}
        fontWeight={600}
        fill="#0A1017"
        fontFamily="ui-monospace, monospace"
      >
        {lastCandle.close.toLocaleString("en-GB", { maximumFractionDigits: lastCandle.close > 100 ? 0 : 4 })}
      </text>

      {showVolume &&
        candles.map((candle, index) => {
          const x = padding.left + slot * index + slot / 2;
          const barHeight = (candle.volume / volumeMax) * (volumeHeight - 8);
          return (
            <rect
              key={`v-${candle.open_time}`}
              x={x - bodyWidth / 2}
              y={volumeTop + (volumeHeight - 8) - barHeight}
              width={bodyWidth}
              height={Math.max(barHeight, 0.6)}
              fill={candle.close >= candle.open ? "#25D19A" : "#FF5F7E"}
              opacity={0.28}
            />
          );
        })}
    </svg>
  );
}
