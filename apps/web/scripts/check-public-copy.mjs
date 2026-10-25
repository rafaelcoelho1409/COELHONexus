// Content policy for everything a visitor can read: the interface describes what the products do and how
// the platform is operated. It never names the private model-routing layer, the model providers behind it,
// or products that no longer exist. Run in `verify`; fails with file:line for each hit.
import { readdirSync, readFileSync, statSync } from "node:fs";
import { dirname, join, relative, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const root = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const SCAN = ["src/app", "src/features", "src/shared/ui", "public"];
const SKIP = /\.(test|spec)\.[cm]?[jt]sx?$|\.(png|jpe?g|ico|woff2?)$/;

const FORBIDDEN = [
  /\brotator\b/i,
  /\b(model|llm) router\b/i,
  /\bbandit\b/i,
  /\bfgts\b/i,
  /\blitellm\b/i,
  /\bbyok\b/i,
  /\bopenrouter\b/i,
  /\bfree[- ]tier\b/i,
  /\b(nvidia nim|groq|cerebras|mistral|deepseek)\b/i,
  /\bresearch radar\b/i,
];

function* files(dir) {
  let entries = [];
  try {
    entries = readdirSync(dir);
  } catch {
    return; // an optional folder (e.g. public/) may not exist
  }
  for (const entry of entries) {
    const path = join(dir, entry);
    if (statSync(path).isDirectory()) yield* files(path);
    else if (!SKIP.test(path)) yield path;
  }
}

const hits = [];
for (const base of SCAN) {
  for (const path of files(join(root, base))) {
    readFileSync(path, "utf8")
      .split("\n")
      .forEach((line, index) => {
        for (const pattern of FORBIDDEN) {
          if (pattern.test(line))
            hits.push(`${relative(root, path)}:${index + 1}  ${pattern}  ${line.trim()}`);
        }
      });
  }
}

if (hits.length > 0) {
  console.error(`FAIL ${hits.length} forbidden mention(s) in public copy:\n  ${hits.join("\n  ")}`);
  process.exit(1);
}
console.log("ok  public copy is clean (no router, provider or retired-product mentions)");
