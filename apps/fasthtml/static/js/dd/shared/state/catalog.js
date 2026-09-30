// state/catalog.js — catalog (Step 1) picker DOM + sticky generate bar +
// catalog-local filter state. Self-contained: every "write" path lives
// here so the `let` bindings can be re-assigned.

// -------- picker controls --------
export const search       = document.querySelector('#fw-search');
export const chips        = document.querySelectorAll('.fw-chip');
export const tiles        = document.querySelectorAll('.fw-tile');
export const grid         = document.querySelector('#fw-grid');
export const countEl      = document.querySelector('#fw-count');
export const total        = tiles.length;
// -------- sticky bar --------
export const generate     = document.querySelector('#fw-generate');
export const selectedName = document.querySelector('#fw-selected-name');
export const stickyBar    = document.querySelector('#fw-sticky-bar');

// State
export let activeChip     = 'All';
export let query          = '';
// Multi-toggle facet sets (OR within, AND across + with search/category).
// Empty set == "All" (no constraint).
export let activeTiers     = new Set();
export let activeStatuses  = new Set();
// Set of slugs with a finalized ingestion (populated by loadLibrary from
// GET /ingestion). Used on Catalog to green-badge already-downloaded tiles.
export let ingestedSlugs  = new Set();
export let selected       = null;       // slug picked in Step 1

// Setters
export function setActiveChip(v)    { activeChip = v; }
export function setQuery(v)         { query = v; }
export function setIngestedSlugs(v) { ingestedSlugs = v; }
export function setSelected(v)      { selected = v; }
export function toggleTier(v) {
  if (activeTiers.has(v)) activeTiers.delete(v);
  else activeTiers.add(v);
}
export function toggleStatus(v) {
  if (activeStatuses.has(v)) activeStatuses.delete(v);
  else activeStatuses.add(v);
}
export function resetFacets() {
  activeChip = 'All';
  activeTiers.clear();
  activeStatuses.clear();
}
