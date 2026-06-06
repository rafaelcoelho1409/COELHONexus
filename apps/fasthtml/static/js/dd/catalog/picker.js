// ============================================================
// picker.js — Framework picker (Step 1): filtering, chip/tile
//             click handlers, search input handler
// ============================================================

import * as Sa from '@dd/shared/state/api.js';
import * as Sc from '@dd/shared/state/catalog.js';
import * as Si from '@dd/shared/state/ingestion.js';
import { refreshGenerateState } from '../shared/ui.js';
import { navigateToStage } from '../shared/nav.js';

export function indexTilesForFramework() {
  Sc.tiles.forEach(t => {
    const slug = t.dataset.slug;
    const name = t.dataset.name;
    // Multi-logo tile carries a strip of `.fw-tile-logo-multi`;
    // single-logo tile carries `.fw-tile-logo`. Collect whichever.
    const multi = Array.from(t.querySelectorAll('.fw-tile-logo-multi'));
    const single = t.querySelector('.fw-tile-logo');
    const logos = multi.length
      ? multi.map(i => i.src)
      : (single ? [single.src] : []);
    Si.frameworkInfo[slug] = {name, logos};
  });
}

// Hydrate `Si.frameworkInfo[slug]` from the resolver API when not
// already cached from a catalog tile or library-list scan. Catalog
// tiles ONLY exist on the Catalog stage — every other stage (Ingestion,
// Planner, Synth, Study) loads without them, so a deep-link or full
// page reload at /docs-distiller/ingestion?slug=X has no catalog
// metadata for the active slug → the progress box would fall back to
// printing the raw slug ("apache-airflow"). This helper closes that
// gap by fetching the canonical catalog entry on demand.
export async function ensureFrameworkInfo(slug) {
  if (!slug) return null;
  const cached = Si.frameworkInfo[slug];
  // Treat "name == slug" as a fallback-stub (not yet hydrated from the
  // catalog) so we re-fetch and pick up the real display name + logo.
  if (cached && cached.name && cached.name !== slug) return cached;
  try {
    const r = await fetch(Sa.API + '/resolver/' + encodeURIComponent(slug));
    if (!r.ok) return cached || null;
    const entry = await r.json();
    const logos = (entry.logos && entry.logos.length)
      ? entry.logos
      : (entry.logo ? [entry.logo] : []);
    const info = { name: entry.name || slug, logos };
    Si.frameworkInfo[slug] = info;
    return info;
  } catch (_) { return cached || null; }
}

// Update the header `#dd-fw-picker-trigger` button (label text +
// optional logo image) to reflect the active framework. The trigger
// is server-rendered at page load from the `?slug=` query param, so
// any code path that sets the active slug AFTER initial render needs
// to also call this — otherwise the button keeps showing "Library"
// even though the rest of the UI knows which framework is loading.
// Concrete trigger case: user clicks the top-nav "Ingestion" tab
// (URL: `/docs-distiller/ingestion` — no slug param) while a run is
// in flight. `recoverActiveRuns` discovers it via /runs/active and
// sets `Si.activeSlug` — and now this helper paints the button so the
// user sees the framework name + logo above the progress box.
export async function updatePickerTrigger(slug) {
  const trigger = document.querySelector('#dd-fw-picker-trigger');
  if (!trigger) return;
  const label = trigger.querySelector('.dd-fw-picker-label');
  if (!slug) {
    if (label) label.textContent = 'Library';
    const oldLogo = trigger.querySelector('.dd-fw-picker-logo');
    if (oldLogo) oldLogo.remove();
    return;
  }
  const info = (await ensureFrameworkInfo(slug)) || {name: slug, logos: []};
  if (label) label.textContent = info.name || slug;
  const logoUrl = (info.logos && info.logos.length) ? info.logos[0] : null;
  let logo = trigger.querySelector('.dd-fw-picker-logo');
  if (logoUrl) {
    if (!logo) {
      logo = document.createElement('img');
      logo.className = 'dd-fw-picker-logo';
      logo.alt = '';
      trigger.insertBefore(logo, trigger.firstChild);
    }
    if (logo.getAttribute('src') !== logoUrl) logo.setAttribute('src', logoUrl);
  } else if (logo) {
    logo.remove();
  }
}

export async function setProgressFramework(slug) {
  // Progress UI elements only exist on /docs-distiller/ingestion. On
  // every other stage this function is a no-op.
  if (!Si.progressFramework) return;
  // Hydrate from /resolver/{slug} if the catalog-tile cache doesn't
  // have this slug yet (the Ingestion page has no catalog tiles so
  // indexTilesForFramework never ran for it).
  const info = (await ensureFrameworkInfo(slug)) || {name: slug, logos: []};
  Si.progressFramework.textContent = info.name || slug;
  if (info.logos && info.logos.length) {
    Si.progressLogos.innerHTML = info.logos.map(u =>
      '<img class="fw-progress-logo" src="' + u + '" alt="">'
    ).join('');
    Si.progressLogos.style.display = '';
  } else {
    Si.progressLogos.innerHTML = '';
    Si.progressLogos.style.display = 'none';
  }
}

// ============================================================
// Step 1: picker filtering + selection
// ============================================================
export function applyFilter() {
  let visible = 0;
  Sc.tiles.forEach(t => {
    const name = t.dataset.name.toLowerCase();
    const cat = t.dataset.category;
    const matchQ = !Sc.query || name.includes(Sc.query);
    const matchC = Sc.activeChip === 'All' || cat === Sc.activeChip;
    const show = matchQ && matchC;
    t.style.display = show ? '' : 'none';
    if (show) visible++;
  });
  if (Sc.grid) Sc.grid.classList.toggle('fw-grid-empty', visible === 0);
  if (Sc.countEl) Sc.countEl.textContent = visible + ' of ' + Sc.total;
}

// Green-badge the catalog tiles whose slug is already in the /ingestion
// library (Sc.ingestedSlugs, populated by loadLibrary). Called from
// main.js initCatalog after the library fetch resolves. Replaces the
// Catalog tab's Library dropdown — ingested status shows inline.
export function markIngestedTiles() {
  Sc.tiles.forEach(t => {
    t.classList.toggle('fw-tile-ingested', Sc.ingestedSlugs.has(t.dataset.slug));
  });
}

Sc.search?.addEventListener('input', e => {
  Sc.setQuery(e.target.value.toLowerCase().trim());
  applyFilter();
});

// Category filter dropdown (catalog row-3 toolbar) — replaces the old
// .fw-chip row. Open/close mirrors the framework picker; choosing an
// option sets Sc.activeChip + re-filters. Guarded: only wires up when
// the dropdown is on the page (catalog only).
const catFilter = document.querySelector('#dd-catfilter');
if (catFilter) {
  const catTrigger = catFilter.querySelector('#dd-catfilter-trigger');
  const catLabel = catFilter.querySelector('#dd-catfilter-label');
  const catPopover = catFilter.querySelector('#dd-catfilter-popover');
  const setCatOpen = (open) => {
    catFilter.classList.toggle('open', open);
    catTrigger?.setAttribute('aria-expanded', open ? 'true' : 'false');
  };
  catTrigger?.addEventListener('click', (e) => {
    e.stopPropagation();
    setCatOpen(!catFilter.classList.contains('open'));
  });
  document.addEventListener('click', (e) => {
    if (catFilter.classList.contains('open') && !catFilter.contains(e.target)) {
      setCatOpen(false);
    }
  });
  document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape' && catFilter.classList.contains('open')) {
      setCatOpen(false);
      catTrigger?.focus();
    }
  });
  // Close on scroll — the toolbar is .topbar-collapsible (overflow
  // hidden); closing returns overflow to hidden before any auto-hide
  // collapse so the popover never clips mid-animation.
  window.addEventListener('scroll', () => {
    if (catFilter.classList.contains('open')) setCatOpen(false);
  }, { passive: true });
  catPopover?.querySelectorAll('.dd-catfilter-option').forEach(opt => {
    opt.addEventListener('click', () => {
      catPopover.querySelectorAll('.dd-catfilter-option')
        .forEach(o => o.classList.remove('active'));
      opt.classList.add('active');
      const chip = opt.dataset.chip;
      Sc.setActiveChip(chip);
      if (catLabel) catLabel.textContent = chip;
      applyFilter();
      setCatOpen(false);
    });
  });
}

Sc.tiles.forEach(t => t.addEventListener('click', () => {
  // Already-ingested tiles jump straight to their Planner — the corpus
  // is downloaded, so the next step is planning (user's choice). Non-
  // ingested tiles select for ingestion (sticky bar → Start Ingestion).
  if (t.classList.contains('fw-tile-ingested')) {
    navigateToStage('planner', t.dataset.slug);
    return;
  }
  Sc.tiles.forEach(x => x.classList.remove('selected'));
  t.classList.add('selected');
  Sc.setSelected(t.dataset.slug);
  if (Sc.selectedName) Sc.selectedName.textContent = t.dataset.name;
  if (Sc.stickyBar) Sc.stickyBar.classList.add('visible');
  refreshGenerateState();
}));
