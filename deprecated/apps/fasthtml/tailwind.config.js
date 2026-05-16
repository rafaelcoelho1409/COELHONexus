/** @type {import('tailwindcss').Config} */
// Memos-inspired design tokens, replicated via Tailwind + DaisyUI.
// Compiled with the standalone tailwindcss binary (no Node.js required).
//   tailwindcss-linux-amd64 -i static/css/input.css -o static/css/main.css --watch
module.exports = {
  content: [
    "./templates/**/*.templ",
    "./main.go",
  ],
  safelist: [
    // HTMX-returned fragment classes that purge might otherwise drop
    { pattern: /^(badge|alert)-(success|warning|error|info|primary)$/ },
    { pattern: /^htmx-(swapping|settling|request|added)$/ },
  ],
  theme: {
    extend: {
      fontFamily: {
        sans: [
          "Inter",
          "ui-sans-serif",
          "system-ui",
          "-apple-system",
          "Segoe UI",
          "sans-serif",
        ],
        mono: [
          "JetBrains Mono",
          "ui-monospace",
          "SFMono-Regular",
          "Menlo",
          "monospace",
        ],
      },
      typography: ({ theme }) => ({
        DEFAULT: {
          css: {
            // Memos-ish prose: smaller headings, tighter leading, visible code
            h1: { fontSize: "1.5rem", fontWeight: "700" },
            h2: { fontSize: "1.25rem", fontWeight: "600" },
            h3: { fontSize: "1.1rem", fontWeight: "600" },
            "code::before": { content: "none" },
            "code::after": { content: "none" },
            code: {
              fontWeight: "500",
              padding: "0.125rem 0.375rem",
              borderRadius: "0.25rem",
              backgroundColor: theme("colors.zinc.100"),
              color: theme("colors.zinc.800"),
            },
            "pre code": {
              backgroundColor: "transparent",
              padding: 0,
            },
          },
        },
      }),
    },
  },
  plugins: [
    require("@tailwindcss/typography"),
    require("daisyui"),
  ],
  daisyui: {
    // Memos-ish palette — green primary, airy zinc neutrals, crisp radii.
    themes: [
      {
        memos: {
          "primary": "#16a34a",          // memos-green
          "primary-content": "#ffffff",
          "secondary": "#64748b",         // slate-500
          "accent": "#0ea5e9",            // sky-500 for links/info
          "neutral": "#18181b",           // zinc-900
          "base-100": "#ffffff",
          "base-200": "#f4f4f5",          // zinc-100
          "base-300": "#e4e4e7",          // zinc-200
          "base-content": "#18181b",
          "info": "#0ea5e9",
          "success": "#16a34a",
          "warning": "#f59e0b",
          "error": "#ef4444",
          "--rounded-box": "0.5rem",
          "--rounded-btn": "0.375rem",
          "--rounded-badge": "0.375rem",
          "--animation-btn": "0.2s",
          "--border-btn": "1px",
        },
      },
      {
        "memos-dark": {
          "primary": "#22c55e",
          "primary-content": "#082f49",
          "secondary": "#94a3b8",
          "accent": "#38bdf8",
          "neutral": "#f4f4f5",
          "base-100": "#18181b",          // zinc-900
          "base-200": "#27272a",          // zinc-800
          "base-300": "#3f3f46",          // zinc-700
          "base-content": "#f4f4f5",
          "info": "#38bdf8",
          "success": "#22c55e",
          "warning": "#fbbf24",
          "error": "#f87171",
          "--rounded-box": "0.5rem",
          "--rounded-btn": "0.375rem",
          "--rounded-badge": "0.375rem",
        },
      },
    ],
    darkTheme: "memos-dark",
    logs: false,
  },
};
