// ESLint is kept for the two jobs Biome cannot do: Next.js/React-hooks rules (incl. the React Compiler
// checks in eslint-config-next) and architectural import boundaries. Formatting and every other lint
// rule belong to Biome (see biome.json).

import { defineConfig, globalIgnores } from "eslint/config";
import nextVitals from "eslint-config-next/core-web-vitals";
import nextTs from "eslint-config-next/typescript";
import boundaries from "eslint-plugin-boundaries";

// Layers: app -> features -> shared. A feature is imported only through its `index.ts`; a feature
// never reaches into another feature's internals; shared knows nothing about features or the app.
const boundariesConfig = {
  files: ["src/**/*.{ts,tsx}"],
  plugins: { boundaries },
  settings: {
    "boundaries/include": ["src/**/*"],
    "boundaries/elements": [
      { type: "app", pattern: "src/app" },
      { type: "feature", pattern: "src/features/*", capture: ["feature"] },
      { type: "shared", pattern: "src/shared/*", capture: ["module"] },
    ],
  },
  rules: {
    "boundaries/dependencies": [
      "error",
      {
        default: "disallow",
        policies: [
          { from: { element: { type: "app" } }, allow: { to: { element: { type: "shared" } } } },
          {
            from: { element: { type: "app" } },
            allow: { to: { element: { type: "feature", fileInternalPath: "index.ts" } } },
          },
          { from: { element: { type: "feature" } }, allow: { to: { element: { type: "shared" } } } },
          {
            from: { element: { type: "feature" } },
            allow: { to: { element: { type: "feature", fileInternalPath: "index.ts" } } },
          },
          { from: { element: { type: "shared" } }, allow: { to: { element: { type: "shared" } } } },
        ],
      },
    ],
  },
};

export default defineConfig([
  ...nextVitals,
  ...nextTs,
  boundariesConfig,
  globalIgnores([".next/**", "out/**", "build/**", "next-env.d.ts", "src/shared/api/generated/**"]),
]);
