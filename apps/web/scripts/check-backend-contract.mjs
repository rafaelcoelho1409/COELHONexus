// The Docs Distiller screens read the planner's and synth's graph state by key name, and recognise a run by its
// thread-id prefix. Those names live in the backend, not in the OpenAPI spec (state values are open objects),
// so a typo here fails silently: a node that never reads "done", a run that is never found again after a reload.
// This guard reads the backend's own definitions and fails when the web's node tables disagree with them.
// It needs the monorepo (`apps/server` next to `apps/web`); in a build context without it, it says so and passes.
import { existsSync, readFileSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const web = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const server = resolve(web, "../server/domains/dd");

if (!existsSync(server)) {
  console.log("skip backend contract check: apps/server is not next to apps/web in this checkout");
  process.exit(0);
}

const read = (path) => readFileSync(path, "utf8");
const problems = [];

/** Keys of a TypedDict class body: the `name:` lines indented four spaces. */
function typedDictKeys(source, className) {
  const body = source.split(new RegExp(`^class ${className}\\b.*$`, "m"))[1];
  if (body === undefined) throw new Error(`class ${className} not found`);
  const end = body.search(/^\S/m); // the class body ends at the next unindented line
  return new Set(
    [...(end < 0 ? body : body.slice(0, end)).matchAll(/^ {4}([a-z_][a-z0-9_]*):/gm)].map((m) => m[1]),
  );
}

/** The strings of `NODE_ORDER = ( … )` in a graph module, in order. */
function nodeOrder(source) {
  const match = source.match(/^NODE_ORDER = \(([\s\S]*?)\n\)/m);
  if (!match) throw new Error("NODE_ORDER not found");
  return [...match[1].matchAll(/"([a-z_]+)"/g)].map((m) => m[1]);
}

/** `{ id, field, stats }` entries of one node table in stages.ts. */
function webNodes(source, table) {
  const match = source.match(new RegExp(`export const ${table}: StageNode\\[\\] = \\[([\\s\\S]*?)\\n\\];`));
  if (!match) throw new Error(`${table} not found in stages.ts`);
  // `\s*` between properties: the formatter wraps long entries over several lines.
  const entry = /id: "([a-z_]+)",\s*label: "[^"]*",\s*field: "([a-z_]+)",\s*stats: "([a-z_]+)"/g;
  return [...match[1].matchAll(entry)].map((m) => ({
    id: m[1],
    field: m[2],
    stats: m[3],
  }));
}

const stages = read(join(web, "src/features/dd/domain/stages.ts"));

for (const [name, table, stateClass, statePath, graphPath] of [
  ["planner", "PLANNER_NODES", "PlannerState", "planner/state.py", "planner/graph.py"],
  ["synth", "SYNTH_NODES", "SynthState", "synth/state.py", "synth/graph.py"],
]) {
  const keys = typedDictKeys(read(join(server, statePath)), stateClass);
  const nodes = webNodes(stages, table);
  const order = nodeOrder(read(join(server, graphPath)));

  if (nodes.length === 0) problems.push(`${name}: found no nodes in ${table}`);
  if (nodes.map((n) => n.id).join() !== order.join()) {
    problems.push(
      `${name}: node ids [${nodes.map((n) => n.id)}] differ from the backend's NODE_ORDER [${order}]`,
    );
  }
  for (const node of nodes) {
    for (const key of [node.field, node.stats]) {
      if (!keys.has(key)) problems.push(`${name}.${node.id}: "${key}" is not a key of ${stateClass}`);
    }
  }
}

const backendPrefix = read(join(server, "planner/runtime/dispatch/keys.py")).match(
  /^THREAD_PREFIX = "([^"]+)"/m,
)?.[1];
const webPrefix = read(join(web, "src/features/dd/params.ts")).match(/THREAD_PREFIX = "([^"]+)"/)?.[1];
if (!backendPrefix || backendPrefix !== webPrefix) {
  problems.push(`thread-id prefix: web "${webPrefix}" vs backend "${backendPrefix}"`);
}

if (problems.length > 0) {
  console.error(`backend contract drift:\n  - ${problems.join("\n  - ")}`);
  process.exit(1);
}
console.log("ok   planner and synth node tables and the thread-id prefix match the backend");
