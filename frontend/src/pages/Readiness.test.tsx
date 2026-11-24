// @vitest-environment jsdom
/**
 * Readiness — the page whose job is to say no.
 *
 * These tests defend the two ways this page could go wrong: it could show a green
 * "ready for live" (it must not, ever), or it could hide the items that need outside
 * help (it must list them).
 */
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { Readiness } from "./Readiness";
import { ToastProvider } from "../components/ui";
import * as monitoring from "../lib/demo/monitoringEngine";
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
        <Readiness />
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

describe("Readiness", () => {
  it("renders the page header", async () => {
    await mount();
    expect(text()).toContain("Readiness");
  });

  it("answers NO to live readiness", async () => {
    await mount();
    expect(text()).toContain("Live ready");
    expect(text()).toContain("NO");
  });

  it("never claims live trading is reachable", async () => {
    await mount();
    expect(text()).not.toContain("Live readyYES");
  });

  it("lists the items blocking live with what would unblock them", async () => {
    await mount();
    const body = text();
    expect(body).toContain("Written UK regulatory-perimeter assessment");
    expect(body).toContain("specialist counsel");
    expect(body).toContain("KYC / AML / sanctions screening");
  });

  it("shows the release gate and its scope", async () => {
    await mount();
    expect(text()).toContain("Release gate");
    expect(text()).toContain("current paper-trading release");
  });

  it("states that passing the gate is not a compliance sign-off", async () => {
    await mount();
    expect(text()).toContain("not a regulatory opinion");
  });

  it("states that no live adapter exists", async () => {
    await mount();
    expect(text()).toContain("not built");
    expect(text()).toContain("No adapter in this build implements live execution");
  });

  it("names all three regulatory stages", async () => {
    await mount();
    const body = text();
    expect(body).toContain("Private R&D");
    expect(body).toContain("Research SaaS");
    expect(body).toContain("Live trading services");
  });

  it("marks the live stage as not implemented rather than pending", async () => {
    await mount();
    expect(text()).toContain("not_implemented");
  });

  it("shows evidence for each assessment item", async () => {
    await mount();
    expect(text()).toContain("Every balance comes from a simulated funding entry");
  });

  it("reports an unbuilt item as not built, not as zero-risk", async () => {
    await mount();
    expect(text()).toContain("Not built");
  });

  it("says paper evidence is a sample and makes no profitability claim", async () => {
    await mount();
    expect(text()).toContain("sample, not a season");
  });
});
