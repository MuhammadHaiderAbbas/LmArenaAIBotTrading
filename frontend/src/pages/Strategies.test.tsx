// @vitest-environment jsdom
/**
 * Strategies screen: the numbers a reviewer reads, and the gate they trust.
 */
import { afterEach, describe, expect, it } from "vitest";
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { Strategies } from "./Strategies";
import { ToastProvider } from "../components/ui";

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
        <Strategies />
      </ToastProvider>,
    );
  });
  await act(async () => {
    await new Promise((resolve) => setTimeout(resolve, 80));
  });
  return host;
}

afterEach(async () => {
  await act(async () => {
    root?.unmount();
  });
  host?.remove();
  root = null;
  host = null;
});

describe("Strategies screen", () => {
  it("renders the catalogue and states each hypothesis", async () => {
    const node = await mount();
    const text = node.textContent ?? "";
    expect(text).toContain("Strategies");
    expect(text).toContain("Trend following");
    expect(text).toContain("Hypothesis, stated before the result");
    expect(text).toContain("Buy and hold benchmark");
  });

  it("shows the cost model alongside the result", async () => {
    const node = await mount();
    const text = node.textContent ?? "";
    expect(text).toContain("Cost model — stored with every run");
    expect(text).toContain("Spread (bps)");
    expect(text).toContain("Slippage (bps)");
  });

  it("compares against buy and hold rather than zero", async () => {
    const node = await mount();
    const text = node.textContent ?? "";
    expect(text).toContain("Edge vs buy & hold");
    expect(text).toContain("Buy and hold");
    expect(text).toContain("Any active strategy has to beat that, not zero");
  });

  it("shows the gate verdict for the current run", async () => {
    const node = await mount();
    const text = node.textContent ?? "";
    expect(text).toMatch(/Gate (passed|not passed)\./);
  });

  it("states that no backtest can enable a strategy", async () => {
    const node = await mount();
    const text = node.textContent ?? "";
    expect(text).toContain("no backtest can enable it");
    expect(text.toLowerCase()).toContain("a run cannot validate itself");
  });

  it("switches strategy when the catalogue is clicked", async () => {
    const node = await mount();
    const button = Array.from(node.querySelectorAll("button")).find((element) =>
      /Mean reversion/.test(element.textContent ?? ""),
    );
    expect(button).toBeTruthy();
    await act(async () => {
      button!.dispatchEvent(new MouseEvent("click", { bubbles: true }));
    });
    expect(node.textContent).toContain("z-score");
  });
});
