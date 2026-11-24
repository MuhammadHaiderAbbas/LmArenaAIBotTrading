// @vitest-environment jsdom
/**
 * Analytics screen: the negatives matter more than the numbers. A metric the sample
 * cannot support must read as a dash with a reason, never as 0.00.
 */
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { Analytics } from "./Analytics";
import { ToastProvider } from "../components/ui";
import * as execution from "../lib/demo/executionEngine";

(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

let root: Root | null = null;
let host: HTMLDivElement | null = null;

async function mount() {
  host = document.createElement("div");
  document.body.appendChild(host);
  root = createRoot(host);
  await act(async () => {
    root!.render(
      <ToastProvider>
        <Analytics />
      </ToastProvider>,
    );
  });
  await act(async () => {
    await new Promise((resolve) => setTimeout(resolve, 120));
  });
  return host;
}

const text = () => document.body.textContent ?? "";

beforeEach(() => {
  localStorage.clear();
  execution.resetDemo();
});

afterEach(async () => {
  await act(async () => {
    root?.unmount();
  });
  host?.remove();
  root = null;
  host = null;
});

describe("Analytics screen", () => {
  it("labels itself as simulated", async () => {
    await mount();
    expect(text()).toContain("Simulated results");
  });

  it("states that unsupported metrics are withheld, not zeroed", async () => {
    await mount();
    expect(text()).toContain("withheld with a reason");
    expect(text()).toContain("No zero for missing data");
  });

  it("shows the metric table with definitions", async () => {
    await mount();
    const body = text();
    expect(body).toContain("Net return");
    expect(body).toContain("Sharpe ratio");
    expect(body).toContain("Max drawdown");
    expect(body).toContain("Profit factor");
    expect(body).toContain("Every metric, with its definition");
  });

  it("explains why a number is missing", async () => {
    await mount();
    expect(text()).toContain("Why a number is missing");
    expect(text()).toContain("observation");
  });

  it("shows the cost breakdown", async () => {
    execution.placeOrder({ symbol: "AAPL", side: "buy", quantity: 1 });
    await mount();
    const body = text();
    expect(body).toContain("Commission");
    expect(body).toContain("Slippage");
  });

  it("lists a closed trade after a round trip", async () => {
    execution.placeOrder({ symbol: "AAPL", side: "buy", quantity: 1 });
    execution.placeOrder({ symbol: "AAPL", side: "sell", quantity: 1 });
    await mount();
    expect(text()).toContain("Closed trades");
    expect(text()).toContain("AAPL");
  });

  it("shows an empty state before any trade closes", async () => {
    await mount();
    expect(text()).toContain("No closed trades");
  });

  it("says it is not a forecast", async () => {
    await mount();
    expect(text()).toContain("No forecast");
    expect(text()).toContain("forward_looking");
  });
});
