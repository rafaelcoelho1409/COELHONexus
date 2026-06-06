// shared/cytoscape_loader.js — on-demand loader for Cytoscape + Dagre.
//
// Phase 2 (2026-06-05): the graph stack (Cytoscape ~320 KB + Dagre ~120 KB
// + cytoscape-dagre ~20 KB ≈ 460 KB) was previously loaded eagerly in HEAD
// on EVERY page including home/settings/youtube where the canvas never
// mounts. This module injects the 3 scripts on the first canvas-init call,
// caches the resulting promise, and resolves it for subsequent callers.
// Both planner and synth canvas init paths now `await ensureCytoscape()`
// before touching `window.cytoscape`.

let _loadPromise = null;


function _loadScript(src) {
  return new Promise((resolve, reject) => {
    const s = document.createElement('script');
    s.src = src;
    s.onload = () => resolve();
    s.onerror = () => reject(new Error(`failed to load ${src}`));
    document.head.appendChild(s);
  });
}


/**
 * Ensures Cytoscape + Dagre + cytoscape-dagre are loaded into the page.
 * Idempotent — returns the cached promise on every subsequent call.
 * Loads serially because cytoscape-dagre and dagre depend on cytoscape
 * being defined when their script body runs.
 */
export function ensureCytoscape() {
  if (_loadPromise) return _loadPromise;
  _loadPromise = (async () => {
    // Already loaded (e.g. someone reverted to eager-load): short-circuit.
    if (typeof cytoscape !== 'undefined') return;
    await _loadScript('https://cdnjs.cloudflare.com/ajax/libs/cytoscape/3.30.4/cytoscape.min.js');
    await _loadScript('https://unpkg.com/dagre@0.8.5/dist/dagre.min.js');
    await _loadScript('https://unpkg.com/cytoscape-dagre@2.5.0/cytoscape-dagre.js');
    // cytoscape-dagre 2.x auto-registers via `cytoscape.use(...)` on its
    // own script load. Mark the registration so callers can branch the
    // layout type without reaching into Cytoscape's internals.
    try {
      if (typeof cytoscape !== 'undefined' && typeof cytoscapeDagre !== 'undefined') {
        cytoscape.use(cytoscapeDagre);
      }
      // _dagreRegistered is set by the script as a side effect of use().
      // Stamp it ourselves if the script's auto-registration didn't.
      if (typeof cytoscape !== 'undefined' && cytoscape._dagreRegistered === undefined) {
        cytoscape._dagreRegistered = true;
      }
    } catch (e) {
      console.warn('[cytoscape_loader] dagre registration failed:', e);
    }
  })();
  return _loadPromise;
}
