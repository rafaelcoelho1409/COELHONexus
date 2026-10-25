// Performance budgets for the first load of `/`, measured from the production build: the gzipped size of
// every script and stylesheet the prerendered HTML references. Run after `npm run build`.
// (Brotli at the ingress will be smaller; gzip is the conservative, tool-independent number.)
import { readFileSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { gzipSync } from "node:zlib";

const root = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const KB = 1024;
const BUDGETS = { js: 170 * KB, css: 30 * KB, html: 40 * KB };

let html;
try {
  html = readFileSync(join(root, ".next/server/app/index.html"), "utf8");
} catch {
  console.error("No prerendered home page found — run `npm run build` first.");
  process.exit(1);
}

// `<script noModule>` is Next's legacy-browser polyfill bundle (~38 KB gz): modern browsers skip it, so it is
// not part of anyone's first load and must not count against the budget.
const tags = [...html.matchAll(/<(?:script|link)\b[^>]*>/g)]
  .map((m) => m[0])
  .filter((tag) => !/\bnoModule\b/i.test(tag));
const assets = (extension) =>
  [
    ...new Set(
      tags.flatMap((tag) => [...tag.matchAll(/\/_next\/(static\/[^"'\s]+?\.(?:js|css))/g)].map((m) => m[1])),
    ),
  ].filter((path) => path.endsWith(extension));
const gzipped = (relativePath) => gzipSync(readFileSync(join(root, ".next", relativePath))).length;
const total = (paths) => paths.reduce((sum, path) => sum + gzipped(path), 0);

const measured = {
  js: total(assets(".js")),
  css: total(assets(".css")),
  html: gzipSync(html).length,
};

const rows = Object.entries(BUDGETS).map(([name, limit]) => {
  const size = measured[name];
  return { name, size, limit, ok: size <= limit };
});
for (const { name, size, limit, ok } of rows) {
  console.log(
    `${ok ? "ok  " : "FAIL"} ${name.padEnd(4)} ${(size / KB).toFixed(1).padStart(6)} KB gz  (budget ${limit / KB} KB)`,
  );
}
console.log(`     ${assets(".js").length} scripts, ${assets(".css").length} stylesheets on first load of /`);
if (rows.some((row) => !row.ok)) process.exit(1);
