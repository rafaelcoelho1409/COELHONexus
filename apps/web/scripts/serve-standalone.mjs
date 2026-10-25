// Serves the production build exactly as the container does (see Dockerfile.web): the standalone server
// plus the static assets and public/ folder copied beside it. `next start` is not used — it ignores
// `output: "standalone"`, so it would test something that never ships.
//
//   PORT=3100 node scripts/serve-standalone.mjs      (after `npm run build`)
import { cpSync, existsSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

const root = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const standalone = join(root, ".next/standalone");
const server = join(standalone, "server.js");

if (!existsSync(server)) {
  console.error("No standalone build found — run `npm run build` first.");
  process.exit(1);
}

cpSync(join(root, ".next/static"), join(standalone, ".next/static"), { recursive: true });
if (existsSync(join(root, "public")))
  cpSync(join(root, "public"), join(standalone, "public"), { recursive: true });

process.env.PORT ??= "3000";
process.env.HOSTNAME ??= "127.0.0.1";
process.env.NODE_ENV = "production";
await import(pathToFileURL(server).href);
