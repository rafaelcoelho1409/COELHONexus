// Fails when the committed generated client is not exactly what the committed spec generates —
// i.e. someone edited generated code by hand, or changed openapi.json without regenerating.
// (The other half — openapi.json vs the live backend — is `scripts/server/export_openapi.py --check`.)
import { execFileSync } from "node:child_process";
import { cpSync, existsSync, readdirSync, readFileSync, rmSync, statSync } from "node:fs";
import { dirname, join, relative, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const root = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const generated = join(root, "src/shared/api/generated");
const backup = join(root, "node_modules/.cache/api-drift-backup");

function snapshot(dir) {
  const files = new Map();
  const walk = (current) => {
    for (const entry of readdirSync(current).sort()) {
      const path = join(current, entry);
      if (statSync(path).isDirectory()) walk(path);
      else files.set(relative(dir, path), readFileSync(path, "utf8"));
    }
  };
  if (existsSync(dir)) walk(dir);
  return files;
}

const before = snapshot(generated);
rmSync(backup, { recursive: true, force: true });
if (existsSync(generated)) cpSync(generated, backup, { recursive: true });

const drift = [];
try {
  execFileSync("npx", ["openapi-ts"], { cwd: root, stdio: "ignore" });
  const after = snapshot(generated);
  for (const name of new Set([...before.keys(), ...after.keys()])) {
    if (before.get(name) !== after.get(name)) drift.push(name);
  }
} finally {
  // Always leave the tree exactly as it was found.
  rmSync(generated, { recursive: true, force: true });
  if (existsSync(backup)) cpSync(backup, generated, { recursive: true });
  rmSync(backup, { recursive: true, force: true });
}

if (drift.length > 0) {
  console.error(
    `FAIL generated client is out of date (${drift.length} file(s)): ${drift.slice(0, 8).join(", ")}`,
  );
  console.error("Run `npm run api:generate` and commit the result.");
  process.exit(1);
}
console.log(`ok  generated client matches openapi/openapi.json (${before.size} files)`);
