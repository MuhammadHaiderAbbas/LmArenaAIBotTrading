// @vitest-environment jsdom
/**
 * Execution screen: the honest framing matters as much as the numbers. A reviewer
 * must be able to tell, without scrolling far, that this is a paper venue and that
 * refusals carry their reason.
 */
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { Execution } from "./Execution";
import { ToastProvider } from "../components/ui";
import { resetDemo } from "../lib/demo/executionEngine";

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
        <Execution />
      </ToastProvider>,
    );
  });
  await act(async () => {
    await new Promise((resolve) => setTimeout(resolve, 80));
  });
  return host;
}

function setValue(element: HTMLInputElement | HTMLSelectElement, value: string) {
  const prototype =
    element instanceof HTMLSelectElement ? HTMLSelectElement.prototype : HTMLInputElement.prototype;
  const setter = Object.getOwnPropertyDescriptor(prototype, "value")!.set!;
  setter.call(element, value);
  element.dispatchEvent(new Event("input", { bubbles: true }));
  element.dispatchEvent(new Event("change", { bubbles: true }));
}

function findByLabel(label: string): HTMLInputElement | HTMLSelectElement {
  const element = document.querySelector(`[aria-label="${label}"]`);
  if (!element) throw new Error(`No control labelled "${label}"`);
  return element as HTMLInputElement | HTMLSelectElement;
}

function buttonWithText(text: string): HTMLButtonElement {
  const match = Array.from(document.querySelectorAll("button")).find((button) =>
    button.textContent?.includes(text),
  );
  if (!match) throw new Error(`No button containing "${text}"`);
  return match as HTMLButtonElement;
}

async function submitOrder(quantity: string, side: "Buy" | "Sell" = "Buy") {
  // The instrument select is left at its default: React's value tracker blocks
  // synthetic select changes, and the behaviour under test does not depend on it.
  await act(async () => {
    setValue(findByLabel("Quantity"), quantity);
  });
  await act(async () => {
    buttonWithText(side).click();
  });
  await act(async () => {
    buttonWithText("Submit").click();
  });
  await act(async () => {
    await new Promise((resolve) => setTimeout(resolve, 60));
  });
}

beforeEach(() => {
  localStorage.clear();
  resetDemo();
});

afterEach(async () => {
  await act(async () => {
    root?.unmount();
  });
  host?.remove();
  root = null;
  host = null;
});

describe("Execution screen", () => {
  it("states in the header that this is a paper venue", async () => {
    await mount();
    const text = document.body.textContent ?? "";
    expect(text).toContain("Paper venue");
    expect(text).toContain("no broker connected");
  });

  it("says what the phase does not do", async () => {
    await mount();
    const text = document.body.textContent ?? "";
    expect(text).toContain("What Phase 8 does not do");
    expect(text).toContain("No broker");
    expect(text).toContain("No autonomous loop");
    expect(text).toContain("No short selling");
  });

  it("shows equity, cash and cost figures for an empty book", async () => {
    await mount();
    const text = document.body.textContent ?? "";
    expect(text).toContain("Equity");
    expect(text).toContain("Unrealised");
    expect(text).toContain("Realised");
    expect(text).toContain("No open positions");
  });

  it("fills a buy and shows the position", async () => {
    await mount();
    await submitOrder("2");
    const text = document.body.textContent ?? "";
    expect(text).toContain("GBP/USD");
    expect(text).toContain("Filled");
    // A fill has a price and costs; the table must show both.
    expect(text).toMatch(/Avg entry|Avg price/);
  });

  it("shows the rejection reason instead of hiding a refusal", async () => {
    await mount();
    await submitOrder("1000000");
    const text = document.body.textContent ?? "";
    expect(text).toContain("Rejected");
    expect(text).toContain("Insufficient cash");
  });

  it("refuses a short sale and says why", async () => {
    await mount();
    await submitOrder("1", "Sell");
    const text = document.body.textContent ?? "";
    expect(text).toContain("Rejected");
    expect(text.toLowerCase()).toContain("holds");
  });

  it("explains that a signal is validated before it becomes an order", async () => {
    await mount();
    const text = document.body.textContent ?? "";
    expect(text).toContain("Signals are not commands");
    expect(text).toContain("The ledger is the record");
  });
});
