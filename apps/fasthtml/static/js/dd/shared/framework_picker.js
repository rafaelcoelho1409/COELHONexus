// ============================================================
// framework_picker.js — Header-anchored framework picker dropdown.
// (Renamed from fw_picker.js on 2026-06-05 for naming clarity —
// catalog/picker.js is the tile-grid picker; this one is the header
// dropdown for switching between ingested frameworks.)
//
// Trigger: `.dd-fw-picker-trigger` button in the title row.
// Popover: `.dd-fw-picker-popover` (right-aligned, opens down).
// List:    `#fw-sidebar-list` — populated by library.js's
//          existing renderSidebar(); we own ONLY the open/close
//          behavior and the in-popover search filter.
//
// Behavior:
//   - Trigger click toggles `.open` on the picker root AND triggers
//     a fresh loadLibrary() so the list reflects current backend
//     state (stale-while-revalidate — existing items stay visible
//     and get overwritten when the fetch resolves). Mirrors the
//     GitHub repo switcher / Vercel project picker pattern: data
//     refreshes on every open, never goes stale.
//   - Click outside the picker closes.
//   - Escape closes and returns focus to the trigger.
//   - Search input filters .fw-lib-item rows by name (case-insensitive).
//   - Selecting a library row navigates (library.js click handler),
//     which destroys this page — no explicit close needed.
// ============================================================

import { loadLibrary } from './library.js';

(() => {
  const picker = document.querySelector('#dd-fw-picker');
  if (!picker) return;
  const trigger = picker.querySelector('#dd-fw-picker-trigger');
  const popover = picker.querySelector('#dd-fw-picker-popover');
  const search  = picker.querySelector('#dd-fw-picker-search');
  const list    = picker.querySelector('#fw-sidebar-list');
  if (!trigger || !popover || !search || !list) return;

  function setOpen(open) {
    picker.classList.toggle('open', open);
    trigger.setAttribute('aria-expanded', open ? 'true' : 'false');
    if (open) {
      // Defer focus until after the visibility transition so the
      // browser scrolls the input into view cleanly.
      requestAnimationFrame(() => search.focus());
      // Refresh library data on every open. Cheap insurance against
      // page-load races where the init-time loadLibrary() resolved
      // before the popover was reachable, or where the user has
      // ingested a framework in another tab since this page loaded.
      // Fire-and-forget; renderSidebar in-place-replaces the list so
      // existing items stay visible until the fresh data lands.
      loadLibrary().catch(() => {});
    }
  }

  trigger.addEventListener('click', (e) => {
    e.stopPropagation();
    setOpen(!picker.classList.contains('open'));
  });

  // Outside-click close. Two carve-outs:
  //   1. Clicks inside the picker itself stay open (list scrolling,
  //      whitespace, search input — all valid interactions).
  //   2. Clicks inside an open modal-backdrop stay open. The library
  //      delete button opens a confirm modal; the user's Confirm/
  //      Cancel click sits OUTSIDE the picker, but we want the
  //      picker to remain open so they can pick another framework
  //      right after the delete settles.
  document.addEventListener('click', (e) => {
    if (!picker.classList.contains('open')) return;
    if (picker.contains(e.target)) return;
    if (e.target.closest && e.target.closest('.fw-modal-backdrop')) return;
    setOpen(false);
  });

  document.addEventListener('keydown', (e) => {
    if (e.key !== 'Escape') return;
    if (!picker.classList.contains('open')) return;
    setOpen(false);
    trigger.focus();
  });

  // Close on window scroll. The picker lives in the row-3 toolbar which
  // is .topbar-collapsible (auto-hides on scroll-down via topbar.js).
  // Closing here returns the toolbar's overflow to hidden BEFORE any
  // collapse runs, so an open popover never gets clipped mid-animation.
  // Standard dropdown behavior — scrolling dismisses it. Scrolling the
  // popover's OWN list is overflow inside the popover (not window
  // scroll), so it won't trip this.
  window.addEventListener('scroll', () => {
    if (picker.classList.contains('open')) setOpen(false);
  }, { passive: true });

  // Client-side filter — hides .fw-lib-item rows whose framework
  // name doesn't contain the query. Substring match, lowercased,
  // ignores leading/trailing whitespace.
  search.addEventListener('input', () => {
    const q = search.value.trim().toLowerCase();
    list.querySelectorAll('.fw-lib-item').forEach(item => {
      const name = item.querySelector('.fw-lib-name')?.textContent
                   .toLowerCase() || '';
      item.style.display = (!q || name.includes(q)) ? '' : 'none';
    });
  });
})();
