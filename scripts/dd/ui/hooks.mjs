// ESM resolve hook: the browser import map (@nx/, @dd/) + a nanostores stub, so the real Synth page modules load under Node.
import { pathToFileURL, fileURLToPath } from 'node:url';
import path from 'node:path';
const HERE = path.dirname(fileURLToPath(import.meta.url));
const ROOT = path.resolve(HERE, '../../../apps/fasthtml/static/js') + '/';
export async function resolve(specifier, context, next) {
  if (specifier === 'nanostores') return { url: pathToFileURL(path.join(HERE, 'nanostores_stub.mjs')).href, shortCircuit: true };
  if (specifier.startsWith('@dd/')) return { url: pathToFileURL(ROOT + 'dd/' + specifier.slice(4)).href, shortCircuit: true };
  if (specifier.startsWith('@nx/')) return { url: pathToFileURL(ROOT + specifier.slice(4)).href, shortCircuit: true };
  return next(specifier, context);
}
