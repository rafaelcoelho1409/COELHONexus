// ============================================================
// picker.js — Framework picker (Step 1): filtering, chip/tile
//             click handlers, search input handler
// ============================================================

import * as S from './state.js';
import { refreshGenerateState } from './ui.js';

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
  S.grid.classList.toggle('fw-grid-empty', visible === 0);
  S.countEl.textContent = visible + ' of ' + S.total;
}

S.search.addEventListener('input', e => {
  S.setQuery(e.target.value.toLowerCase().trim());
  applyFilter();
});
S.chips.forEach(c => c.addEventListener('click', () => {
  S.chips.forEach(x => x.classList.remove('active'));
  c.classList.add('active');
  S.setActiveChip(c.dataset.chip);
  applyFilter();
}));
S.tiles.forEach(t => t.addEventListener('click', () => {
  // Tile selection always works (catalog stays interactive). Whether
  // the Generate button is clickable is governed by activeRunId.
  if (S.currentStep !== 1) return;
  S.tiles.forEach(x => x.classList.remove('selected'));
  t.classList.add('selected');
  S.setSelected(t.dataset.slug);
  S.selectedName.textContent = t.dataset.name;
  S.stickyBar.classList.add('visible');
  refreshGenerateState();
}));
