// @vitest-environment jsdom
/**
 * Operations screen: it must show what the checks found, and it must not claim a green
 * light it did not earn. Both halves are tested — the healthy path and the refusal.
 */
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { Operations } from "./Operations";
import { ToastProvider } from "../components/ui";
import * as monitoring from "../lib/demo/monitoringEngine";
import * as risk from "../lib/demo/riskEngine";
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
        <Operations />
      </ToastProvider>,
    );
  });
  await act(async () => {
    await new Promise((resolve) => setTimeout(resolve, 150));
  });
  return host;
}

const text = () => document.body.textContent ?? "";

beforeEach(() => {
  localStorage.clear();
  monitoring.resetDemo();
  risk.resetDemo();
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

describe("Operations", () => {
  it("renders the page header", async () => {
    await mount();
    expect(text()).toContain("Operations");
  });

  it("lists every monitoring check by name", async () => {
    await mount();
    const body = text();
    for (const label of ["Data freshness", "Order activity", "Circuit breakers", "Audit chain", "Reconciliation"]) {
      expect(body).toContain(label);
    }
  });

  it("shows the overall health of a clean demo book", async () => {
    await mount();
    expect(text()).toContain("OK");
  });

  it("reports a tripped breaker instead of a green light", async () => {
    risk.tripBreaker("Manual stop during the test.");
    await mount();
    expect(text()).toContain("CRITICAL");
    expect(text()).toContain("Manual stop during the test.");
  });

  it("reports a broken chain as broken", async () => {
    monitoring.record("order.placed", "one");
    monitoring.record("order.filled", "two");
    monitoring.tamperForDemo();
    await mount();
    expect(text()).toContain("BROKEN");
  });

  it("shows an empty trail rather than a fake row", async () => {
    await mount();
    expect(text()).toContain("Nothing sealed in this session yet.");
  });

  it("lists sealed entries once they exist", async () => {
    monitoring.record("order.placed", "Buy 1 GBP/USD");
    await mount();
    expect(text()).toContain("order.placed");
    expect(text()).not.toContain("Nothing sealed");
  });

  it("states the chain's limitation next to its status", async () => {
    await mount();
    expect(text()).toContain("does not prevent them");
  });

  it("admits which checks the demo cannot run", async () => {
    await mount();
    const body = text();
    expect(body).toContain("Two of the eight checks are stubs in this demo");
    expect(body).toContain("Model degradation");
  });

  it("seals from the page and reflects the new entry", async () => {
    await mount();
    const button = Array.from(document.querySelectorAll("button")).find((node) =>
      node.textContent?.includes("Seal pending entries"),
    );
    expect(button).toBeTruthy();
    await act(async () => {
      button!.click();
      await new Promise((resolve) => setTimeout(resolve, 150));
    });
    expect(monitoring.chain().length).toBe(1);
    expect(text()).toContain("demo.seal");
  });
});
