// @vitest-environment jsdom
/**
 * Risk Centre: the page must show the limits it enforces, and be honest about the
 * shutdown's scope — a reviewer should never read "trading halted" and assume the
 * positions were closed.
 */
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { Risk } from "./Risk";
import { ToastProvider } from "../components/ui";
import * as riskDemo from "../lib/demo/riskEngine";
import * as executionDemo from "../lib/demo/executionEngine";

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
        <Risk />
      </ToastProvider>,
    );
  });
  await act(async () => {
    await new Promise((resolve) => setTimeout(resolve, 100));
  });
  return host;
}

beforeEach(() => {
  localStorage.clear();
  executionDemo.resetDemo();
  riskDemo.resetDemo();
});

afterEach(async () => {
  await act(async () => {
    root?.unmount();
  });
  host?.remove();
  root = null;
  host = null;
});

describe("Risk Centre", () => {
  it("shows the blueprint limits", async () => {
    await mount();
    const text = document.body.textContent ?? "";
    expect(text).toContain("Max risk per trade");
    expect(text).toContain("0.50%");
    expect(text).toContain("Max daily loss");
    expect(text).toContain("2.00%");
    expect(text).toContain("Max drawdown");
    expect(text).toContain("10.00%");
    expect(text).toContain("Max positions");
    expect(text).toContain("Leverage is disabled");
  });

  it("says trading is permitted when the breaker is closed", async () => {
    await mount();
    expect(document.body.textContent ?? "").toContain("Trading permitted");
  });

  it("states that the stop does not liquidate positions", async () => {
    await mount();
    const text = document.body.textContent ?? "";
    expect(text).toContain("never liquidates");
    expect(text).toContain("not");
    expect(text).toContain("close open positions");
  });

  it("shows a halted state with the reason once the breaker trips", async () => {
    riskDemo.tripBreaker("Manual halt for a test.");
    await mount();
    const text = document.body.textContent ?? "";
    expect(text).toContain("New risk halted");
    expect(text).toContain("Manual halt for a test.");
    expect(text).toContain("not being liquidated");
  });

  it("lists the rules it applies, in order", async () => {
    await mount();
    const text = document.body.textContent ?? "";
    expect(text).toContain("Rules applied, in order");
    expect(text).toContain("Kill switch");
    expect(text).toContain("Per-trade risk");
    expect(text).toContain("Correlation");
  });

  it("is explicit about what it is not", async () => {
    await mount();
    const text = document.body.textContent ?? "";
    expect(text).toContain("What this controller is not");
    expect(text).toContain("Not an optimiser");
    expect(text).toContain("Not learning");
    expect(text).toContain("Not a liquidation engine");
  });

  it("shows recorded checks with their reasons", async () => {
    riskDemo.evaluateAndRecord({ symbol: "AAPL", side: "buy", quantity: 1e6 });
    await mount();
    const text = document.body.textContent ?? "";
    expect(text).toContain("AAPL");
    expect(text).toContain("Rejected");
    expect(text).toContain("leverage is disabled");
  });

  it("shows an empty state before any check exists", async () => {
    await mount();
    expect(document.body.textContent ?? "").toContain("No checks yet");
  });
});
