// ============================================================
// picker.js — Framework picker (Step 1): filtering, chip/tile
//             click handlers, search input handler
// ============================================================

import * as S from './state.js';
import { refreshGenerateState } from './ui.js';
import { navigateToStage } from './nav.js';

export function indexTilesForFramework() {
  S.tiles.forEach(t => {
    const slug = t.dataset.slug;
    const name = t.dataset.name;
    // Multi-logo tile carries a strip of `.fw-tile-logo-multi`;
    // single-logo tile carries `.fw-tile-logo`. Collect whichever.
    const multi = Array.from(t.querySelectorAll('.fw-tile-logo-multi'));
    const single = t.querySelector('.fw-tile-logo');
    const logos = multi.length
      ? multi.map(i => i.src)
      : (single ? [single.src] : []);
    S.frameworkInfo[slug] = {name, logos};
  });
}

export function setProgressFramework(slug) {
  // Progress UI elements only exist on /docs-distiller/ingestion. On
  // every other stage this function is a no-op.
  if (!S.progressFramework) return;
  const info = S.frameworkInfo[slug] || {name: slug, logos: []};
  S.progressFramework.textContent = info.name || slug;
  if (info.logos && info.logos.length) {
    S.progressLogos.innerHTML = info.logos.map(u =>
      '<img class="fw-progress-logo" src="' + u + '" alt="">'
    ).join('');
    S.progressLogos.style.display = '';
  } else {
    S.progressLogos.innerHTML = '';
    S.progressLogos.style.display = 'none';
  }
}

// ============================================================
// Step 1: picker filtering + selection
// ============================================================
export function applyFilter() {
  let visible = 0;
  S.tiles.forEach(t => {
    const name = t.dataset.name.toLowerCase();
    const cat = t.dataset.category;
    const matchQ = !S.query || name.includes(S.query);
    const matchC = S.activeChip === 'All' || cat === S.activeChip;
    const show = matchQ && matchC;
    t.style.display = show ? '' : 'none';
    if (show) visible++;
  });
  if (S.grid) S.grid.classList.toggle('fw-grid-empty', visible === 0);
  if (S.countEl) S.countEl.textContent = visible + ' of ' + S.total;
}

// Green-badge the catalog tiles whose slug is already in the /ingestion
// library (S.ingestedSlugs, populated by loadLibrary). Called from
// main.js initCatalog after the library fetch resolves. Replaces the
// Catalog tab's Library dropdown — ingested status shows inline.
export function markIngestedTiles() {
  S.tiles.forEach(t => {
    t.classList.toggle('fw-tile-ingested', S.ingestedSlugs.has(t.dataset.slug));
  });
}

S.search?.addEventListener('input', e => {
  S.setQuery(e.target.value.toLowerCase().trim());
  applyFilter();
});

// Category filter dropdown (catalog row-3 toolbar) — replaces the old
// .fw-chip row. Open/close mirrors the framework picker; choosing an
// option sets S.activeChip + re-filters. Guarded: only wires up when
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
      S.setActiveChip(chip);
      if (catLabel) catLabel.textContent = chip;
      applyFilter();
      setCatOpen(false);
    });
  });
}

S.tiles.forEach(t => t.addEventListener('click', () => {
  // Already-ingested tiles jump straight to their Planner — the corpus
  // is downloaded, so the next step is planning (user's choice). Non-
  // ingested tiles select for ingestion (sticky bar → Start Ingestion).
  if (t.classList.contains('fw-tile-ingested')) {
    navigateToStage('planner', t.dataset.slug);
    return;
  }
  S.tiles.forEach(x => x.classList.remove('selected'));
  t.classList.add('selected');
  S.setSelected(t.dataset.slug);
  if (S.selectedName) S.selectedName.textContent = t.dataset.name;
  if (S.stickyBar) S.stickyBar.classList.add('visible');
  refreshGenerateState();
}));
