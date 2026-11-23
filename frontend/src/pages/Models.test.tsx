// @vitest-environment jsdom
/**
 * Models screen: the promotion gate has to be visible and unclickable-through.
 *
 * The test that matters is the last one. If a failed model could be promoted with
 * a click, the whole point of Phase 6 would be decoration.
 */
import { afterEach, describe, expect, it } from "vitest";
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { Models } from "./Models";
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
        <Models />
      </ToastProvider>,
    );
  });
  await act(async () => {
    await new Promise((resolve) => setTimeout(resolve, 60));
  });
  return host;
}

function buttonByText(node: HTMLElement, pattern: RegExp) {
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

describe("Models screen", () => {
  it("renders versions and the evaluation table", async () => {
    const node = await mount();
    const text = node.textContent ?? "";
    expect(text).toContain("Models");
    expect(text).toContain("Version history");
    expect(text).toContain("Walk-forward folds");
    expect(text).toContain("Out Of Sample");
    // The baseline must be on screen, not buried in a tooltip.
    expect(text).toContain("Baseline");
    expect(text).toContain("majority-class");
  });

  it("shows no champion and says so", async () => {
    const node = await mount();
    const text = node.textContent ?? "";
    expect(text).toContain("Nothing has passed the gate");
    expect(text).toContain("Never promoted — candidates are inert");
  });

  it("blocks promotion and explains why", async () => {
    const node = await mount();
    const blocked = buttonByText(node, /Promotion blocked by gate/i);
    expect(blocked.getAttribute("aria-disabled")).toBe("true");
    expect(node.textContent).toContain("Gate not passed");
  });

  it("surfaces drift as advisory text, not as an action", async () => {
    const node = await mount();
    const text = node.textContent ?? "";
    // Drift is only present when a feature has actually moved; either way the
    // screen must never present drift as something the system acts on.
    expect(text).toContain("advisory only");
    expect(text).toContain("Advisory only — never auto-acted");
    expect(text).toContain("No automatic promotion");
  });

  it("names the safety boundaries the phase relies on", async () => {
    const node = await mount();
    const text = node.textContent ?? "";
    expect(text).toContain("No production rule changes");
    expect(text).toContain("can never promote itself");
  });

  it("cannot promote a failed model even when the button is clicked", async () => {
    const node = await mount();
    const blocked = buttonByText(node, /Promotion blocked by gate/i);
    await act(async () => {
      blocked.dispatchEvent(new MouseEvent("click", { bubbles: true }));
    });
    // The click produces a refusal notice, never a status change.
    expect(node.textContent).toContain("Promotion refused");
    expect(node.textContent).toContain("Gate not passed");
    expect(node.textContent).not.toContain("Promoted to champion successfully");
  });
});
