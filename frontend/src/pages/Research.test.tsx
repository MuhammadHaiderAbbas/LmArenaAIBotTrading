// @vitest-environment jsdom
/**
 * Smoke tests for the Research Lab screen, rendered against the demo corpus.
 *
 * Playwright cannot run in this sandbox (missing libnspr4), so these tests use
 * a real DOM via jsdom. They render the actual page, click the buttons, and
 * assert on the text a reviewer would read — that is what catches a screen that
 * throws on mount, which is exactly how the previous version of this page broke.
 */
import { afterEach, describe, expect, it } from "vitest";
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { Research } from "./Research";
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
        <Research />
      </ToastProvider>,
    );
  });
  // Let the on-mount fetches settle.
  await act(async () => {
    await new Promise((resolve) => setTimeout(resolve, 30));
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

describe("Research Lab screen", () => {
  it("renders without throwing and labels the corpus as synthetic", async () => {
    const node = await mount();
    expect(node.textContent).toContain("AI Research Lab");
    expect(node.textContent?.toLowerCase()).toContain("synthetic demo corpus");
  });

  it("lists the source register with licence handling", async () => {
    const node = await mount();
    expect(node.textContent).toContain("Bank of England news");
    expect(node.textContent).toContain("SEC EDGAR filings");
    expect(node.textContent?.toLowerCase()).toContain("link and excerpt only");
  });

  it("shows the failing source honestly rather than hiding it", async () => {
    const node = await mount();
    expect(node.textContent).toContain("429");
  });

  it("produces claims with quotes when research is run", async () => {
    const node = await mount();
    const runButton = Array.from(node.querySelectorAll("button")).find((button) =>
      /run research/i.test(button.textContent ?? ""),
    );
    expect(runButton).toBeTruthy();

    await act(async () => {
      runButton!.dispatchEvent(new MouseEvent("click", { bubbles: true }));
      await new Promise((resolve) => setTimeout(resolve, 50));
    });

    expect(node.textContent).toContain("supported");
    expect(node.textContent).toContain("generator: extractive-v1");
    // A quoted passage must be rendered, in quote marks, with a source link.
    expect(node.querySelector("blockquote")).toBeTruthy();
    expect(node.querySelector("blockquote a")?.getAttribute("href")).toMatch(/^https:\/\//);
  });

  it("shows the injected document flagged, not silently accepted", async () => {
    const node = await mount();
    // The flagged document is visible in the list with its detected patterns.
    expect(node.textContent).toContain("Statement on operational resilience");
    expect(node.textContent).toContain("instruction_override, system_prompt_probe");
    // And the corpus health panel counts it rather than reporting a clean corpus.
    expect(node.textContent).toContain("Flagged1");
  });

  it("says plainly that ingestion cannot run offline", async () => {
    const node = await mount();
    const sync = Array.from(node.querySelectorAll("button")).find((button) =>
      /sync sources/i.test(button.textContent ?? ""),
    );
    await act(async () => {
      sync!.dispatchEvent(new MouseEvent("click", { bubbles: true }));
      await new Promise((resolve) => setTimeout(resolve, 50));
    });
    expect(node.textContent?.toLowerCase()).toContain("could not be reached");
  });
});
