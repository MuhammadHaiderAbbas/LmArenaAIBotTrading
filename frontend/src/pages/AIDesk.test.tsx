// @vitest-environment jsdom
/**
 * Smoke tests for the AI Desk screen against the demo engine.
 *
 * Playwright cannot run in this sandbox (missing libnspr4), so these render the
 * real page in jsdom, click the button and assert on the text a reviewer would
 * read. That catches a screen which throws on mount — the failure mode this
 * project has hit before.
 */
import { afterEach, describe, expect, it } from "vitest";
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { AIDesk } from "./AIDesk";
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
        <AIDesk />
      </ToastProvider>,
    );
  });
  await act(async () => {
    await new Promise((resolve) => setTimeout(resolve, 40));
  });
  return host;
}

function clickByText(node: HTMLElement, pattern: RegExp) {
  const button = Array.from(node.querySelectorAll("button")).find((element) =>
    pattern.test(element.textContent ?? ""),
  );
  expect(button, `no button matching ${pattern}`).toBeTruthy();
  return button!;
}

afterEach(async () => {
  await act(async () => {
    root?.unmount();
  });
  host?.remove();
  root = null;
  host = null;
});

describe("AI Desk screen", () => {
  it("renders and states that proposals are not orders", async () => {
    const node = await mount();
    expect(node.textContent).toContain("AI Desk");
    expect(node.textContent).toContain("Proposals are not orders");
    expect(node.textContent).toContain("no execution path");
  });

  it("shows built and planned specialists separately", async () => {
    const node = await mount();
    expect(node.textContent).toContain("Technical Analyst");
    expect(node.textContent).toContain("Fundamental Analyst");
    expect(node.textContent).toContain("planned");
    expect(node.textContent).toContain("5 of 8 built");
  });

  it("runs the specialists and renders findings with evidence", async () => {
    const node = await mount();
    const runButton = clickByText(node, /run specialists/i);

    await act(async () => {
      runButton.dispatchEvent(new MouseEvent("click", { bubbles: true }));
      await new Promise((resolve) => setTimeout(resolve, 60));
    });

    // A finding per implemented agent.
    expect(node.textContent).toContain("Technical Analyst");
    expect(node.textContent).toContain("Data Quality Monitor");
    expect(node.textContent).toContain("Not tradeable");
    expect(node.textContent).toContain("rule score");
  });

  it("surfaces the blocked reason rather than implying execution", async () => {
    const node = await mount();
    await act(async () => {
      clickByText(node, /run specialists/i).dispatchEvent(
        new MouseEvent("click", { bubbles: true }),
      );
      await new Promise((resolve) => setTimeout(resolve, 60));
    });
    expect(node.textContent).toContain("Not tradeable");
    expect(node.textContent?.toLowerCase()).toContain("risk controller");
  });

  it("opens a finding to reveal measured facts and evidence rows", async () => {
    const node = await mount();
    await act(async () => {
      clickByText(node, /run specialists/i).dispatchEvent(
        new MouseEvent("click", { bubbles: true }),
      );
      await new Promise((resolve) => setTimeout(resolve, 60));
    });

    const findingToggle = Array.from(node.querySelectorAll("button")).find((element) =>
      /Trend indicators|No price history/i.test(element.textContent ?? ""),
    );
    expect(findingToggle).toBeTruthy();

    await act(async () => {
      findingToggle!.dispatchEvent(new MouseEvent("click", { bubbles: true }));
      await new Promise((resolve) => setTimeout(resolve, 30));
    });

    expect(node.textContent).toContain("Measured");
    expect(node.textContent).toContain("Evidence");
    expect(node.textContent).toContain("Limitations");
  });

  it("never displays a tradeable proposal", async () => {
    const node = await mount();
    await act(async () => {
      clickByText(node, /run specialists/i).dispatchEvent(
        new MouseEvent("click", { bubbles: true }),
      );
      await new Promise((resolve) => setTimeout(resolve, 60));
    });
    expect(node.textContent).not.toContain("tradeable: true");
  });

  it("does not offer instruments the demo engine cannot price", async () => {
    const node = await mount();
    const options = Array.from(node.querySelectorAll("select option")).map(
      (option) => option.getAttribute("value") ?? "",
    );
    expect(options.length).toBeGreaterThan(3);
    expect(options).not.toContain("SPX500");
    expect(options).not.toContain("XAUUSD");
  });
});
