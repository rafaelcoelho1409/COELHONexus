// Proves the import-boundary rules still bite: plants illegal and legal imports under src/, lints them
// with the project's real ESLint config, and fails unless exactly the illegal ones are reported.
// Self-cleaning — the probe files never survive the run.
import { mkdirSync, rmSync, writeFileSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

import { ESLint } from "eslint";

const root = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const src = join(root, "src");

const probes = [
  // [path under src, content, must be reported?]
  ["features/zz_probe_a/internal.ts", "export const a = 1;\n", false],
  ["features/zz_probe_a/index.ts", 'export { a } from "./internal";\n', false],
  [
    "features/zz_probe_b/via-index.ts",
    'import { a } from "@/features/zz_probe_a";\nexport const ok = a;\n',
    false,
  ],
  [
    "features/zz_probe_b/deep.ts",
    'import { a } from "@/features/zz_probe_a/internal";\nexport const bad = a;\n',
    true,
  ],
  [
    "shared/lib/zz-shared-to-feature.ts",
    'import { a } from "@/features/zz_probe_a";\nexport const bad = a;\n',
    true,
  ],
  [
    "app/zz-deep-import.ts",
    'import { a } from "@/features/zz_probe_a/internal";\nexport const bad = a;\n',
    true,
  ],
  ["app/zz-ok-shared.ts", 'import { cn } from "@/shared/lib/utils";\nexport const ok = cn;\n', false],
];

let failed = false;
try {
  for (const [path, content] of probes) {
    const file = join(src, path);
    mkdirSync(dirname(file), { recursive: true });
    writeFileSync(file, content);
  }
  const eslint = new ESLint({ cwd: root });
  const results = await eslint.lintFiles(probes.map(([path]) => join(src, path)));
  const reported = new Set(
    results
      .filter((result) => result.messages.some((message) => message.ruleId === "boundaries/dependencies"))
      .map((result) => result.filePath.slice(src.length + 1)),
  );
  for (const [path, , mustReport] of probes) {
    const isReported = reported.has(path);
    if (isReported !== mustReport) {
      failed = true;
      console.error(`FAIL ${path}: expected ${mustReport ? "a boundary error" : "no boundary error"}`);
    }
  }
} finally {
  for (const dir of ["features/zz_probe_a", "features/zz_probe_b"])
    rmSync(join(src, dir), { recursive: true, force: true });
  for (const path of ["shared/lib/zz-shared-to-feature.ts", "app/zz-deep-import.ts", "app/zz-ok-shared.ts"])
    rmSync(join(src, path), { force: true });
}

if (failed) process.exit(1);
console.log("ok  import boundaries: 3 illegal imports rejected, 4 legal ones accepted");
