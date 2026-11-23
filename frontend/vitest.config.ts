import react from "@vitejs/plugin-react";
import { defineConfig } from "vitest/config";

// Tests run against the demo build: the demo API is deterministic, needs no
// backend and is what ships to GitHub Pages, so it is the thing worth guarding.
export default defineConfig({
  plugins: [react()],
  define: { __DEMO_MODE__: JSON.stringify(true) },
  test: {
    environment: "node",
    include: ["src/**/*.test.{ts,tsx}"],
  },
});
