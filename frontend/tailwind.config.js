/** @type {import('tailwindcss').Config} */
export default {
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  darkMode: "class",
  theme: {
    extend: {
      colors: {
        // Institutional terminal palette: navy/charcoal surfaces, disciplined accents.
        canvas: "#070B14",
        surface: {
          DEFAULT: "#0C1220",
          raised: "#111A2C",
          sunken: "#080D18",
          hover: "#162034",
        },
        line: {
          DEFAULT: "#1C2740",
          strong: "#27354F",
        },
        ink: {
          DEFAULT: "#E8EDF7",
          muted: "#8F9DB5",
          faint: "#5C6A82",
        },
        accent: {
          DEFAULT: "#4F8CFF",
          soft: "#7FB0FF",
          dim: "#1E3A6B",
        },
        positive: "#25D19A",
        negative: "#FF5F7E",
        caution: "#FFB74D",
      },
      fontFamily: {
        sans: [
          "Inter",
          "ui-sans-serif",
          "system-ui",
          "-apple-system",
          "Segoe UI",
          "Roboto",
          "Helvetica Neue",
          "Arial",
          "sans-serif",
        ],
        mono: ["ui-monospace", "SFMono-Regular", "Menlo", "Consolas", "monospace"],
      },
      fontSize: {
        "2xs": ["0.6875rem", { lineHeight: "1rem", letterSpacing: "0.04em" }],
      },
      boxShadow: {
        panel: "0 1px 0 0 rgba(255,255,255,0.03) inset, 0 12px 32px -18px rgba(0,0,0,0.9)",
        glow: "0 0 0 1px rgba(79,140,255,0.35), 0 0 28px -8px rgba(79,140,255,0.45)",
      },
      keyframes: {
        "pulse-dot": {
          "0%, 100%": { opacity: "1", transform: "scale(1)" },
          "50%": { opacity: "0.45", transform: "scale(0.85)" },
        },
        "slide-up": {
          "0%": { opacity: "0", transform: "translateY(6px)" },
          "100%": { opacity: "1", transform: "translateY(0)" },
        },
      },
      animation: {
        "pulse-dot": "pulse-dot 2.4s ease-in-out infinite",
        "slide-up": "slide-up 220ms ease-out",
      },
    },
  },
  plugins: [],
};
