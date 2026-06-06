// ingestion/ingestion.js — orchestrator + re-exports.
// Step 8 (2026-06-05): split into manifest.js + polling.js. This
// file now re-exports both surfaces so existing consumers using
// `import { ... } from './ingestion/ingestion.js'` resolve without
// churn. Module-init side effects (handler registration etc.) live
// in the polling module since that's where the UI is wired.
export {
  renderManifestTo,
  renderManifest,
  loadManifestForSlug,
} from './manifest.js';
export {
  renderProgress,
  pollRun,
  triggerIngest,
} from './polling.js';
// Importing polling.js for its side effects (event handlers) — the
// re-exports above pick up the symbols; this line ensures the
// module's top-level setup runs even if no consumer touches its
// exports (e.g. on a page where polling-side handlers fire from URL
// params instead of a function call).
import './polling.js';
