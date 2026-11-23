import { defineConfig, loadEnv } from "vite";
import react from "@vitejs/plugin-react";

// GitHub Pages serves the app from a sub-path, so `base` must match the repo
// name in demo builds. Local dev and Docker builds serve from the root.
export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), "");
  const isDemo = mode === "demo";
  const repo = env.GITHUB_REPOSITORY?.split("/")[1] ?? "LmArenaAIBotTrading";

  return {
    base: isDemo ? `/${repo}/` : "/",
    plugins: [react()],
    server: {
      host: "0.0.0.0",
      port: 5173,
      strictPort: true,
    },
    preview: { host: "0.0.0.0", port: 4173 },
    build: {
      outDir: "dist",
      sourcemap: false,
      chunkSizeWarningLimit: 900,
    },
    define: {
      __DEMO_MODE__: JSON.stringify(isDemo),
    },
  };
});
