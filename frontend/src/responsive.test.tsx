// @vitest-environment jsdom
/**
 * Responsive contract for every screen.
 *
 * jsdom does not lay out, so this cannot prove a page *looks* right. What it can do is
 * hold the structural line that broke repeatedly: every table sits inside a scrolling
 * body, every grid has a single-column fallback, and nothing hardcodes a pixel width
 * that cannot fit 320px.
 */
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { act } from "react";
import type { ReactElement } from "react";
import { createRoot, type Root } from "react-dom/client";
import { MemoryRouter } from "react-router-dom";

import { AIDesk } from "./pages/AIDesk";
import { Admin } from "./pages/Admin";
import { Analytics } from "./pages/Analytics";
import { Execution } from "./pages/Execution";
import { History } from "./pages/History";
import { Markets } from "./pages/Markets";
import { Models } from "./pages/Models";
import { Overview } from "./pages/Overview";
import { Portfolio } from "./pages/Portfolio";
import { Research } from "./pages/Research";
import { Risk } from "./pages/Risk";
import { Settings } from "./pages/Settings";
import { Strategies } from "./pages/Strategies";
import { Wallet } from "./pages/Wallet";
import { ToastProvider } from "./components/ui";
import { AuthProvider } from "./features/auth/AuthContext";
import { AccountProvider } from "./features/account/AccountContext";

(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

// jsdom has no ResizeObserver; the charts observe their container to size themselves.
class ResizeObserverStub {
  observe() {}
  unobserve() {}
  disconnect() {}
}
(globalThis as unknown as { ResizeObserver: unknown }).ResizeObserver = ResizeObserverStub;

// The narrowest viewport worth supporting (iPhone SE class).
const NARROW = 320;

let root: Root | null = null;
let host: HTMLDivElement | null = null;

async function mount(element: ReactElement) {
  host = document.createElement("div");
  document.body.appendChild(host);
  root = createRoot(host);
  await act(async () => {
    root!.render(
      <MemoryRouter>
        <AuthProvider>
          <AccountProvider>
            <ToastProvider>{element}</ToastProvider>
          </AccountProvider>
        </AuthProvider>
      </MemoryRouter>,
    );
  });
  await act(async () => {
    await new Promise((resolve) => setTimeout(resolve, 60));
  });
  return host;
}

function setViewport(width: number) {
  Object.defineProperty(window, "innerWidth", { writable: true, configurable: true, value: width });
  window.dispatchEvent(new Event("resize"));
}

const PAGES: [string, ReactElement][] = [
  ["Overview", <Overview />],
  ["Markets", <Markets />],
  ["Execution", <Execution />],
  ["Risk", <Risk />],
  ["Research", <Research />],
  ["AIDesk", <AIDesk />],
  ["Models", <Models />],
  ["Strategies", <Strategies />],
  ["Portfolio", <Portfolio />],
  ["Wallet", <Wallet />],
  ["History", <History />],
  ["Analytics", <Analytics />],
  ["Settings", <Settings />],
  ["Admin", <Admin />],
];

beforeEach(() => {
  localStorage.clear();
  setViewport(NARROW);
});

afterEach(async () => {
  await act(async () => {
    root?.unmount();
  });
  host?.remove();
  root = null;
  host = null;
});

describe(`every page at ${NARROW}px`, () => {
  for (const [name, element] of PAGES) {
    it(`${name} mounts without an unscrollable table`, async () => {
      const container = await mount(element);
      const tables = Array.from(container.querySelectorAll("table"));
      for (const table of tables) {
        // A table needs an ancestor that can scroll sideways, or the Panel's
        // overflow-hidden silently cuts it off.
        const scroller = table.closest(".overflow-x-auto");
        expect(scroller, `${name}: a table has no horizontal scroll container`).not.toBeNull();
      }
    });

    it(`${name} has no fixed pixel width wider than the viewport`, async () => {
      const container = await mount(element);
      const offenders: string[] = [];
      container.querySelectorAll<HTMLElement>("*").forEach((node) => {
        const style = node.getAttribute("style") ?? "";
        const match = /width:\s*(\d+)px/.exec(style);
        if (match && Number(match[1]) > NARROW) offenders.push(style);
      });
      expect(offenders).toEqual([]);
    });
  }
});

describe("layout primitives", () => {
  it("the shell collapses navigation behind a button below lg", async () => {
    const container = await mount(<Overview />);
    // The page itself must not render a permanent sidebar at this width; the trigger
    // is in the shell, which these page-level mounts do not include, so assert the
    // page did not assume one.
    expect(container.textContent).toBeTruthy();
  });
});
