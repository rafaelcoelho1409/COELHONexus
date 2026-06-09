// ============================================================
// ingestion/explorer.js — split-pane docs explorer (2026-06-08).
//
// SOTA pattern survey (June 2026): Mintlify, Docusaurus, Starlight,
// Cloudscape, RAGFlow all converge on list/detail split-pane with
// ⌘K-style search + tier/section grouping + inline live preview.
// This module ports that pattern onto the existing FastHTML + vanilla
// JS stack without adding a framework dep.
//
// Public surface:
//   buildExplorer(manifest)       — primary entry point, called by
//                                    manifest.js after a successful
//                                    /ingestion/{slug}/manifest fetch.
//   resetExplorer()               — wipe both panes (slug-switch reset).
//
// Reuses:
//   renderMarkdownInto            — full markdown pipeline (marked +
//                                    DOMPurify + hljs + KaTeX + mermaid
//                                    + ANSI), same as Study + drawer.
//   So.setCurrentManifestEntries  — keeps the shared drawer wired
//                                    (drawer.js click delegation reads
//                                    `.fw-page-card`; explorer rows use
//                                    `.fw-explorer-row` so the drawer
//                                    no longer fires from this page).
//   Si.activeSlug                 — page-content fetch URL.
// ============================================================

import * as Sa from '@dd/shared/state/api.js';
import * as Si from '@dd/shared/state/ingestion.js';
import * as So from '@dd/shared/state/overlays.js';
import { fmtBytes, fmtAge } from '../shared/utils.js';
import { openDrawer } from '../shared/ui.js';

// ============================================================
// Module-local state (single-explorer page; no need for closures)
// ============================================================
const S = {
  entries: [],                  // raw manifest entries (array-position
                                //   indexed; matches data-idx contract)
  groups: [],                   // [{prefix, entries: [{e, i}, ...]}]
  filtered: [],                 // current visible index list (after
                                //   search + tier filter)
  activeIdx: -1,                // currently-previewed array index, or -1
  searchQ: '',                  // current search text (lowercased)
  activeTiers: new Set(),       // empty == all tiers; else allowlist
  collapsed: new Set(),         // collapsed group prefixes
  framework: '',                // display name for breadcrumb root
};

// ============================================================
// Grouping — derive a navigable tree from entry.url path segments.
//   `https://docs.browser-use.com/open-source/llms-full.txt`
//      → host = docs.browser-use.com
//      → path = /open-source/llms-full.txt
//      → group prefix = "open-source"   (first path segment)
//   Pages whose URL parse fails or has no path → group "(root)".
// ============================================================
function _prefixForEntry(e) {
  try {
    const u = new URL(e.url || '');
    const parts = u.pathname.split('/').filter(Boolean);
    if (parts.length <= 1) return '(root)';
    // First segment is usually the section ("docs", "guides", "api"…)
    // Two segments give more useful grouping for sites that namespace
    // everything under /docs/<section>/<page>.
    if (parts[0] === 'docs' && parts.length >= 2) return parts[1];
    return parts[0];
  } catch (_) {
    return '(root)';
  }
}

function _groupEntries(entries) {
  const map = new Map();
  entries.forEach((e, i) => {
    const k = _prefixForEntry(e);
    if (!map.has(k)) map.set(k, []);
    map.get(k).push({ e, i });
  });
  // Sort group entries by title; sort groups by name with (root) first.
  const out = [];
  for (const [prefix, list] of map.entries()) {
    list.sort((a, b) =>
      String(a.e.title || a.e.slug || '').localeCompare(
        String(b.e.title || b.e.slug || ''),
      ),
    );
    out.push({ prefix, entries: list });
  }
  out.sort((a, b) => {
    if (a.prefix === '(root)') return -1;
    if (b.prefix === '(root)') return 1;
    return a.prefix.localeCompare(b.prefix);
  });
  return out;
}

// ============================================================
// Filtering — applies search + tier filter against group entries.
// Returns the flat list of array-positions that should be visible.
// ============================================================
function _runFilter() {
  const q = S.searchQ.trim().toLowerCase();
  const tierActive = S.activeTiers.size > 0;
  const visible = [];
  for (const { e, i } of S.entries.map((e, i) => ({ e, i }))) {
    if (tierActive && !S.activeTiers.has(e.tier || '')) continue;
    if (q) {
      const hay = (
        (e.title || '') + ' ' + (e.slug || '') + ' ' + (e.url || '')
      ).toLowerCase();
      if (!hay.includes(q)) continue;
    }
    visible.push(i);
  }
  S.filtered = visible;
  return visible;
}

// ============================================================
// Tree render — group headers + rows. Collapsed groups show count only.
// ============================================================
function _renderTree() {
  const treeEl = document.getElementById('fw-explorer-tree');
  const countEl = document.getElementById('fw-explorer-tree-count');
  if (!treeEl) return;
  const visibleSet = new Set(S.filtered);
  if (!S.entries.length) {
    treeEl.innerHTML =
      '<div class="fw-empty">No pages in this manifest.</div>';
    if (countEl) countEl.textContent = '';
    return;
  }
  if (!S.filtered.length) {
    treeEl.innerHTML =
      '<div class="fw-empty">No matches. Clear search to see all pages.</div>';
    if (countEl) {
      countEl.textContent =
        '0 of ' + S.entries.length + ' pages';
    }
    return;
  }
  const html = [];
  for (const g of S.groups) {
    const visibleEntries = g.entries.filter(x => visibleSet.has(x.i));
    if (!visibleEntries.length) continue;
    const collapsed = S.collapsed.has(g.prefix);
    const caret = collapsed ? '▸' : '▼';
    html.push(
      '<div class="fw-explorer-group' + (collapsed ? ' collapsed' : '') +
      '" data-prefix="' + _escapeAttr(g.prefix) + '">' +
      '<div class="fw-explorer-group-head">' +
      '<span class="fw-explorer-group-caret">' + caret + '</span>' +
      '<span class="fw-explorer-group-name">' +
      _escapeHtml(g.prefix) + '</span>' +
      '<span class="fw-explorer-group-count">' +
      visibleEntries.length + '</span>' +
      '</div>',
    );
    if (!collapsed) {
      html.push('<div class="fw-explorer-group-body">');
      for (const { e, i } of visibleEntries) {
        const active = (i === S.activeIdx);
        html.push(
          '<div class="fw-explorer-row' + (active ? ' active' : '') +
          '" data-idx="' + i + '" title="' +
          _escapeAttr(e.url || '') + '">' +
          '<span class="fw-explorer-row-title">' +
          _escapeHtml(e.title || e.slug || '(untitled)') +
          '</span>' +
          '<span class="fw-explorer-row-meta">' +
          _escapeHtml(e.tier || '') + ' · ' +
          fmtBytes(e.bytes || 0) +
          '</span>' +
          '</div>',
        );
      }
      html.push('</div>');
    }
    html.push('</div>');
  }
  treeEl.innerHTML = html.join('');
  if (countEl) {
    countEl.textContent =
      S.filtered.length + ' of ' + S.entries.length + ' pages';
  }
}

// ============================================================
// Tier chips — one chip per distinct tier, toggle to filter.
// ============================================================
function _renderTierChips() {
  const el = document.getElementById('fw-explorer-tier-chips');
  if (!el) return;
  const tiers = Array.from(new Set(
    S.entries.map(e => e.tier || '').filter(Boolean),
  )).sort();
  if (tiers.length <= 1) { el.innerHTML = ''; return; }
  const html = [];
  for (const t of tiers) {
    const active = S.activeTiers.has(t);
    html.push(
      '<button class="fw-explorer-chip' + (active ? ' active' : '') +
      '" data-tier="' + _escapeAttr(t) + '" type="button">' +
      _escapeHtml(t) + '</button>',
    );
  }
  el.innerHTML = html.join('');
}

// ============================================================
// Preview pane — fetch + render markdown for the selected entry.
// Uses the SAME pipeline as the Study chapter view and the drawer.
// ============================================================
async function _loadPreview(idx) {
  if (idx === S.activeIdx) return;
  const e = S.entries[idx];
  if (!e || !Si.activeSlug) return;
  S.activeIdx = idx;
  _updateBreadcrumb(e);
  _updateMeta(e);
  // Highlight active row across both DOMs (visible + collapsed groups
  // still get the class so re-expanding doesn't lose state).
  document.querySelectorAll('.fw-explorer-row.active')
    .forEach(r => r.classList.remove('active'));
  document.querySelectorAll(
    '.fw-explorer-row[data-idx="' + idx + '"]',
  ).forEach(r => r.classList.add('active'));
  _scrollRowIntoView(idx);

  const bodyEl = document.getElementById('fw-explorer-body');
  if (!bodyEl) return;
  bodyEl.innerHTML = '<div class="fw-empty">Loading…</div>';
  try {
    const r = await fetch(
      Sa.API + '/ingestion/' + Si.activeSlug + '/pages/' + e.idx,
    );
    if (!r.ok) {
      bodyEl.innerHTML =
        '<div class="fw-empty">Failed to load page (HTTP ' + r.status +
        ').</div>';
      return;
    }
    const data = await r.json();
    const raw = data.body || '';
    bodyEl.innerHTML = '<article class="fw-markdown"></article>';
    const article = bodyEl.querySelector('article');
    const { renderMarkdownInto } = await import(
      '../shared/content_renderer.js',
    );
    await renderMarkdownInto(article, raw, {});
    bodyEl.scrollTop = 0;
  } catch (err) {
    bodyEl.innerHTML =
      '<div class="fw-empty">' + _escapeHtml(String(err)) + '</div>';
  }
}

function _updateBreadcrumb(e) {
  const el = document.getElementById('fw-explorer-breadcrumb');
  if (!el) return;
  const prefix = _prefixForEntry(e);
  const title = e.title || e.slug || '(untitled)';
  el.innerHTML =
    '<span class="fw-explorer-bc-root">' +
    _escapeHtml(S.framework || 'Library') + '</span>' +
    '<span class="fw-explorer-bc-sep">›</span>' +
    '<span class="fw-explorer-bc-section">' +
    _escapeHtml(prefix) + '</span>' +
    '<span class="fw-explorer-bc-sep">›</span>' +
    '<span class="fw-explorer-bc-title">' +
    _escapeHtml(title) + '</span>';
}

function _updateMeta(e) {
  const el = document.getElementById('fw-explorer-meta');
  if (!el) return;
  el.textContent =
    (e.tier || '') + ' · ' + fmtBytes(e.bytes || 0);
}

function _scrollRowIntoView(idx) {
  const row = document.querySelector(
    '.fw-explorer-row[data-idx="' + idx + '"]',
  );
  if (row && row.scrollIntoView) {
    row.scrollIntoView({ block: 'nearest', behavior: 'smooth' });
  }
}

// ============================================================
// Wiring — search, chips, tree clicks, keyboard nav, popout button.
// Idempotent: wireExplorer can be called multiple times; the
// document-level listener guards prevent double-binding.
// ============================================================
let _wired = false;
function _wireExplorer() {
  if (_wired) return;
  _wired = true;

  // Search input (debounced) — `/` focuses it from anywhere.
  const search = document.getElementById('fw-explorer-search');
  if (search) {
    let t = null;
    search.addEventListener('input', () => {
      clearTimeout(t);
      t = setTimeout(() => {
        S.searchQ = search.value || '';
        _runFilter();
        _renderTree();
      }, 120);
    });
    search.addEventListener('keydown', e => {
      if (e.key === 'Escape') {
        search.value = '';
        S.searchQ = '';
        _runFilter();
        _renderTree();
        search.blur();
      }
    });
  }

  // Global `/` shortcut to focus the search box (Study uses ⌘K; this
  // page uses both — ⌘K is wired by the drawer's keydown handler and
  // is harmless here, `/` is the documentation-explorer convention used
  // by GitHub, Mintlify, MDN, etc.).
  document.addEventListener('keydown', e => {
    if (e.key === '/' && !e.metaKey && !e.ctrlKey && !e.altKey) {
      const tag = (document.activeElement?.tagName || '').toLowerCase();
      if (tag === 'input' || tag === 'textarea') return;
      const s = document.getElementById('fw-explorer-search');
      if (s) { e.preventDefault(); s.focus(); s.select(); }
    }
  });

  // Tree click delegation — group headers toggle collapsed; rows load.
  const tree = document.getElementById('fw-explorer-tree');
  if (tree) {
    tree.addEventListener('click', e => {
      const head = e.target.closest('.fw-explorer-group-head');
      if (head) {
        const group = head.closest('.fw-explorer-group');
        const prefix = group?.dataset?.prefix;
        if (!prefix) return;
        if (S.collapsed.has(prefix)) S.collapsed.delete(prefix);
        else S.collapsed.add(prefix);
        _renderTree();
        return;
      }
      const row = e.target.closest('.fw-explorer-row');
      if (row) {
        const idx = parseInt(row.dataset.idx, 10);
        if (Number.isFinite(idx)) _loadPreview(idx);
      }
    });
    // Arrow-key navigation when the tree (or any of its descendants)
    // holds focus, OR when no input has focus.
    document.addEventListener('keydown', e => {
      if (!S.entries.length) return;
      const tag = (document.activeElement?.tagName || '').toLowerCase();
      if (tag === 'input' || tag === 'textarea') return;
      if (e.key !== 'ArrowDown' && e.key !== 'ArrowUp') return;
      // Find current position in the filtered visible list.
      const pos = S.filtered.indexOf(S.activeIdx);
      let next;
      if (pos === -1) {
        next = S.filtered[0];
      } else if (e.key === 'ArrowDown') {
        next = S.filtered[Math.min(pos + 1, S.filtered.length - 1)];
      } else {
        next = S.filtered[Math.max(pos - 1, 0)];
      }
      if (Number.isFinite(next) && next !== S.activeIdx) {
        e.preventDefault();
        _loadPreview(next);
      }
    });
  }

  // Tier-chip toggles.
  const chips = document.getElementById('fw-explorer-tier-chips');
  if (chips) {
    chips.addEventListener('click', e => {
      const btn = e.target.closest('.fw-explorer-chip');
      if (!btn) return;
      const tier = btn.dataset.tier;
      if (S.activeTiers.has(tier)) S.activeTiers.delete(tier);
      else S.activeTiers.add(tier);
      _renderTierChips();
      _runFilter();
      _renderTree();
    });
  }

  // "Open in drawer" popout — power-user fullscreen view via the shared
  // drawer system (Esc to close, arrow keys for prev/next).
  const popout = document.getElementById('fw-explorer-popout');
  if (popout) {
    popout.addEventListener('click', () => {
      if (S.activeIdx >= 0) openDrawer(S.activeIdx);
    });
  }
}

// ============================================================
// Public API
// ============================================================
export function buildExplorer(manifest) {
  if (!manifest || !manifest.entries) return;
  S.entries = manifest.entries;
  S.framework = manifest.framework_name || manifest.framework_slug || '';
  S.searchQ = '';
  S.activeTiers = new Set();
  S.collapsed = new Set();
  S.activeIdx = -1;
  S.groups = _groupEntries(S.entries);
  // Sync shared overlay state so the drawer (and Study's source-index
  // pop-ups) can still walk the same list when the user pops out.
  So.setCurrentManifestEntries(S.entries);
  _runFilter();
  _renderTierChips();
  _renderTree();
  _wireExplorer();
  const search = document.getElementById('fw-explorer-search');
  if (search) search.value = '';
  // Auto-open the first entry so the preview pane is never empty on a
  // fresh slug load — matches Mintlify/Docusaurus first-page behavior.
  if (S.filtered.length > 0) _loadPreview(S.filtered[0]);
}

export function resetExplorer() {
  S.entries = [];
  S.groups = [];
  S.filtered = [];
  S.activeIdx = -1;
  S.searchQ = '';
  S.activeTiers.clear();
  S.collapsed.clear();
  S.framework = '';
  const tree = document.getElementById('fw-explorer-tree');
  if (tree) tree.innerHTML =
    '<div class="fw-empty">Pick a framework to see its files.</div>';
  const chips = document.getElementById('fw-explorer-tier-chips');
  if (chips) chips.innerHTML = '';
  const count = document.getElementById('fw-explorer-tree-count');
  if (count) count.textContent = '';
  const bc = document.getElementById('fw-explorer-breadcrumb');
  if (bc) bc.innerHTML = '';
  const meta = document.getElementById('fw-explorer-meta');
  if (meta) meta.textContent = '';
  const body = document.getElementById('fw-explorer-body');
  if (body) body.innerHTML =
    '<div class="fw-empty">Pick a page from the left to preview it here.</div>';
  const search = document.getElementById('fw-explorer-search');
  if (search) search.value = '';
}

// ============================================================
// Tiny HTML escapers — avoid pulling in dompurify just for attribute
// text. The preview body still goes through the full sanitize pipeline
// in renderMarkdownInto.
// ============================================================
function _escapeHtml(s) {
  return String(s == null ? '' : s)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;');
}
function _escapeAttr(s) { return _escapeHtml(s); }
