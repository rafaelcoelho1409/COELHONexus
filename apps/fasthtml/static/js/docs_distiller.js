(() => {
  const API = '/api/v1/docs-distiller';

  // -------- picker controls (Step 1) --------
  const search = document.querySelector('#fw-search');
  const chips = document.querySelectorAll('.fw-chip');
  const tiles = document.querySelectorAll('.fw-tile');
  const grid = document.querySelector('#fw-grid');
  const countEl = document.querySelector('#fw-count');
  const total = tiles.length;
  // -------- sticky bar --------
  const generate = document.querySelector('#fw-generate');
  const selectedName = document.querySelector('#fw-selected-name');
  const stickyBar = document.querySelector('#fw-sticky-bar');
  // -------- stepper --------
  const steps = document.querySelectorAll('.fw-step');
  const connectors = document.querySelectorAll('.fw-step-connector');
  const panels = document.querySelectorAll('.fw-step-panel');
  // -------- step 2 progress + file list --------
  const progressBox = document.querySelector('#fw-progress-box');
  const progressTier = document.querySelector('#fw-progress-tier');
  const progressStatus = document.querySelector('#fw-progress-status');
  const progressBar = document.querySelector('#fw-progress-bar');
  const progressFill = document.querySelector('#fw-progress-fill');
  const progressCounter = document.querySelector('#fw-progress-counter');
  const progressUrl = document.querySelector('#fw-progress-url');
  const cancelBtn = document.querySelector('#fw-cancel');
  const step2Summary = document.querySelector('#fw-step2-summary');
  const step2Grid = document.querySelector('#fw-step2-grid');
  // -------- step 3 manifest (mirror — also rendered for the future synth view) --------
  const pagesSummary = document.querySelector('#fw-pages-summary');
  const pageGrid = document.querySelector('#fw-page-grid');
  // -------- sidebar (library) --------
  const sidebar = document.querySelector('#fw-sidebar');
  const sidebarList = document.querySelector('#fw-sidebar-list');
  // -------- notice + toast --------
  const noticeEl = document.querySelector('#fw-cache-notice');
  const noticeText = document.querySelector('#fw-cache-notice-text');
  const toastEl = document.querySelector('#fw-denied-toast');
  const toastText = document.querySelector('#fw-denied-toast-text');
  const toastClose = document.querySelector('#fw-denied-toast-close');
  // -------- confirm modal --------
  const modalEl = document.querySelector('#fw-modal');
  const modalTitleEl = document.querySelector('#fw-modal-title');
  const modalMessageEl = document.querySelector('#fw-modal-message');
  const modalConfirmBtn = document.querySelector('#fw-modal-confirm');
  const modalCancelBtn = document.querySelector('#fw-modal-cancel');
  // -------- file-content drawer --------
  const drawerEl = document.querySelector('#fw-drawer');
  const drawerName = document.querySelector('#fw-drawer-name');
  const drawerMeta = document.querySelector('#fw-drawer-meta');
  const drawerBody = document.querySelector('#fw-drawer-body');
  const drawerPrev = document.querySelector('#fw-drawer-prev');
  const drawerNext = document.querySelector('#fw-drawer-next');
  const drawerClose = document.querySelector('#fw-drawer-close');

  // State
  let activeChip = 'All';
  let query = '';
  let selected = null;            // slug picked in Step 1
  let activeSlug = null;          // slug currently shown in Step 3
  let activeRunId = null;         // run currently being polled
  let pollAbort = false;
  let currentStep = 1;
  let farthestStep = 1;

  // ============================================================
  // Utility
  // ============================================================
  function sleep(ms) { return new Promise(r => setTimeout(r, ms)); }
  function fmtBytes(n) {
    if (!n) return '0 B';
    if (n < 1024) return n + ' B';
    if (n < 1024 * 1024) return (n / 1024).toFixed(1) + ' KB';
    return (n / (1024 * 1024)).toFixed(1) + ' MB';
  }
  function fmtAge(ts) {
    if (!ts) return '';
    const s = Math.max(1, Math.floor(Date.now() / 1000 - ts));
    if (s < 60) return s + 's ago';
    if (s < 3600) return Math.floor(s / 60) + 'm ago';
    if (s < 86400) return Math.floor(s / 3600) + 'h ago';
    return Math.floor(s / 86400) + 'd ago';
  }

  function showNotice(text) {
    noticeText.textContent = text;
    noticeEl.style.display = '';
    setTimeout(() => { noticeEl.style.display = 'none'; }, 8000);
  }
  function hideNotice() { noticeEl.style.display = 'none'; }
  function showToast(text) {
    toastText.textContent = text;
    toastEl.style.display = '';
  }
  function hideToast() { toastEl.style.display = 'none'; }
  toastClose.addEventListener('click', hideToast);

  // ---- in-page confirm modal (replacement for browser confirm()) ----
  let _modalResolver = null;
  function showConfirm(title, message, confirmLabel) {
    modalTitleEl.textContent = title;
    modalMessageEl.textContent = message;
    modalConfirmBtn.textContent = confirmLabel || 'Confirm';
    modalEl.classList.add('visible');
    return new Promise(resolve => { _modalResolver = resolve; });
  }
  function closeModal(result) {
    modalEl.classList.remove('visible');
    const r = _modalResolver;
    _modalResolver = null;
    if (r) r(result);
  }
  modalConfirmBtn.addEventListener('click', () => closeModal(true));
  modalCancelBtn.addEventListener('click', () => closeModal(false));
  modalEl.addEventListener('click', (e) => {
    // Click on the backdrop (outside the box) cancels.
    if (e.target === modalEl) closeModal(false);
  });
  document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape' && modalEl.classList.contains('visible')) {
      closeModal(false);
    }
  });

  // ---- file-content drawer (slide-out, right-anchored) ----
  let currentManifestEntries = [];
  let drawerIdx = -1;

  function openDrawer(idx) {
    if (!currentManifestEntries || currentManifestEntries.length === 0) return;
    if (idx < 0 || idx >= currentManifestEntries.length) return;
    drawerIdx = idx;
    drawerEl.classList.add('visible');
    renderDrawerContent();
  }
  function closeDrawer() {
    drawerEl.classList.remove('visible');
    document.querySelectorAll('.fw-page-card.viewing').forEach(
      c => c.classList.remove('viewing')
    );
  }
  function drawerStep(delta) {
    const next = drawerIdx + delta;
    if (next < 0 || next >= currentManifestEntries.length) return;
    drawerIdx = next;
    renderDrawerContent();
  }
  async function renderDrawerContent() {
    const e = currentManifestEntries[drawerIdx];
    if (!e || !activeSlug) { closeDrawer(); return; }
    drawerName.textContent = e.title || e.slug;
    drawerMeta.textContent =
      (e.tier || '') + ' · ' + fmtBytes(e.bytes) + ' · ' +
      (drawerIdx + 1) + ' of ' + currentManifestEntries.length;
    if (drawerIdx === 0) drawerPrev.setAttribute('disabled', 'disabled');
    else drawerPrev.removeAttribute('disabled');
    if (drawerIdx >= currentManifestEntries.length - 1) drawerNext.setAttribute('disabled', 'disabled');
    else drawerNext.removeAttribute('disabled');
    // Highlight the currently-viewing card across both step grids
    document.querySelectorAll('.fw-page-card.viewing').forEach(
      c => c.classList.remove('viewing')
    );
    document.querySelectorAll(
      '.fw-page-card[data-idx="' + e.idx + '"]'
    ).forEach(c => c.classList.add('viewing'));
    drawerBody.innerHTML = '<div class="fw-empty">Loading…</div>';
    try {
      const r = await fetch(API + '/ingestion/' + activeSlug +
                             '/pages/' + e.idx);
      if (!r.ok) {
        drawerBody.innerHTML =
          '<div class="fw-empty">Failed to load (HTTP ' + r.status + ')</div>';
        return;
      }
      const data = await r.json();
      const raw = data.body || '';
      const md = (typeof marked !== 'undefined')
        ? marked.parse(raw)
        : '<pre>' + raw.replace(/&/g, '&amp;').replace(/</g, '&lt;') + '</pre>';
      drawerBody.innerHTML = '<article class="fw-markdown">' + md + '</article>';
      drawerBody.scrollTop = 0;
    } catch (err) {
      drawerBody.innerHTML = '<div class="fw-empty">' + String(err) + '</div>';
    }
  }
  drawerPrev.addEventListener('click', () => drawerStep(-1));
  drawerNext.addEventListener('click', () => drawerStep(1));
  drawerClose.addEventListener('click', closeDrawer);
  document.addEventListener('keydown', (e) => {
    if (!drawerEl.classList.contains('visible')) return;
    // Don't hijack arrows when the user is typing in an input/textarea
    const tag = (document.activeElement?.tagName || '').toLowerCase();
    if (tag === 'input' || tag === 'textarea') return;
    if (e.key === 'Escape') closeDrawer();
    else if (e.key === 'ArrowLeft') drawerStep(-1);
    else if (e.key === 'ArrowRight') drawerStep(1);
  });
  // Click delegation — opens the drawer from any .fw-page-card in any grid
  document.addEventListener('click', (e) => {
    const card = e.target.closest('.fw-page-card');
    if (!card) return;
    const idx = parseInt(card.dataset.idx, 10);
    if (Number.isFinite(idx)) openDrawer(idx);
  });

  // ============================================================
  // Step 1: picker filtering + selection
  // ============================================================
  function applyFilter() {
    let visible = 0;
    tiles.forEach(t => {
      const name = t.dataset.name.toLowerCase();
      const cat = t.dataset.category;
      const matchQ = !query || name.includes(query);
      const matchC = activeChip === 'All' || cat === activeChip;
      const show = matchQ && matchC;
      t.style.display = show ? '' : 'none';
      if (show) visible++;
    });
    grid.classList.toggle('fw-grid-empty', visible === 0);
    countEl.textContent = visible + ' of ' + total;
  }
  search.addEventListener('input', e => {
    query = e.target.value.toLowerCase().trim();
    applyFilter();
  });
  chips.forEach(c => c.addEventListener('click', () => {
    chips.forEach(x => x.classList.remove('active'));
    c.classList.add('active');
    activeChip = c.dataset.chip;
    applyFilter();
  }));
  tiles.forEach(t => t.addEventListener('click', () => {
    // Tile selection always works (catalog stays interactive). Whether
    // the Generate button is clickable is governed by activeRunId.
    if (currentStep !== 1) return;
    tiles.forEach(x => x.classList.remove('selected'));
    t.classList.add('selected');
    selected = t.dataset.slug;
    selectedName.textContent = t.dataset.name;
    stickyBar.classList.add('visible');
    refreshGenerateState();
  }));

  // ============================================================
  // Stepper navigation
  // ============================================================
  function renderStepper() {
    steps.forEach((s, i) => {
      const n = i + 1;
      s.classList.remove('active', 'completed');
      if (n === currentStep) s.classList.add('active');
      else if (n <= farthestStep) s.classList.add('completed');
    });
    connectors.forEach((c, i) => {
      c.classList.toggle('complete', i + 1 < farthestStep);
    });
  }
  function showStep(n) {
    if (n > farthestStep) return;
    currentStep = n;
    panels.forEach((p, i) => p.classList.toggle('active', i + 1 === n));
    // Sticky bar appears on Step 1 whenever a tile is selected; Generate
    // enablement is controlled by `refreshGenerateState()`.
    stickyBar.classList.toggle('visible', n === 1 && selected !== null);
    // Step 2 — only show the live progress box during an active run;
    // pull the canonical manifest into the file list otherwise. While a
    // run is in flight the manifest doesn't exist yet (finalize happens
    // at the very end), so skip the fetch and show an "in progress"
    // placeholder — pollRun will paint the real file list on done.
    if (n === 2) {
      if (activeRunId !== null) {
        progressBox.style.display = '';
        step2Summary.innerHTML = '';
        step2Grid.innerHTML =
          '<div class="fw-empty">Ingestion in progress — materials will ' +
          'appear here when it completes.</div>';
      } else {
        progressBox.style.display = 'none';
        if (activeSlug) loadManifestForSlug(activeSlug);
      }
    }
    renderStepper();
  }

  function syncStepLocks() {
    // Step 2/3 unlock when EITHER an ingestion is running OR the library
    // has at least one finalized framework. Otherwise lock back to Step 1.
    const hasLibrary =
      sidebarList.querySelectorAll('.fw-lib-item').length > 0;
    const ingestActive = activeRunId !== null;
    if (hasLibrary || ingestActive) {
      farthestStep = Math.max(farthestStep, 3);
    } else {
      farthestStep = 1;
      if (currentStep !== 1) {
        currentStep = 1;
        panels.forEach((p, i) => p.classList.toggle('active', i + 1 === 1));
        stickyBar.classList.toggle('visible', selected !== null);
      }
    }
    renderStepper();
  }

  function refreshGenerateState() {
    // Disable Start Ingestion + every sidebar Refresh button while an
    // ingestion is in flight — prevents parallel POST /runs that would
    // queue + immediately be denied by the single-flight lock anyway.
    const ingestActive = activeRunId !== null;
    if (!selected || ingestActive) {
      generate.setAttribute('disabled', 'disabled');
    } else {
      generate.removeAttribute('disabled');
    }
    document.querySelectorAll('.fw-lib-refresh, .fw-lib-delete').forEach(b => {
      if (ingestActive) {
        b.setAttribute('disabled', 'disabled');
      } else {
        b.removeAttribute('disabled');
      }
    });
  }
  function advance() {
    if (currentStep >= 3) return;
    farthestStep = Math.max(farthestStep, currentStep + 1);
    showStep(currentStep + 1);
  }
  function jumpTo(step) {
    farthestStep = Math.max(farthestStep, step);
    showStep(step);
  }
  steps.forEach((s, i) => s.addEventListener('click', () => {
    const target = i + 1;
    if (target <= farthestStep) showStep(target);
  }));

  // ============================================================
  // Step 3: render manifest entries into the page grid
  // ============================================================
  function renderManifestTo(summaryEl, gridEl, m) {
    if (!m || !m.entries) {
      gridEl.innerHTML = '<div class="fw-empty">Manifest unavailable.</div>';
      if (summaryEl) summaryEl.innerHTML = '';
      return;
    }
    // Track the current entry list so the drawer's prev/next + click
    // delegation walk the same list the user is looking at.
    currentManifestEntries = m.entries;
    if (summaryEl) {
      summaryEl.innerHTML =
        '<span><strong>' + (m.framework_name || activeSlug) + '</strong> · ' +
        (m.entries.length) + ' pages · ' + fmtBytes(m.total_bytes || 0) + '</span>' +
        '<span>' + (m.tier_kind || '') + ' · ' + fmtAge(m.ingested_at) + '</span>';
    }
    gridEl.innerHTML = m.entries.map(e =>
      '<div class="fw-page-card" data-idx="' + e.idx + '">' +
      '<div class="fw-page-title">' + (e.title || e.slug) + '</div>' +
      '<div class="fw-page-meta">' + (e.tier || '') + ' · ' + fmtBytes(e.bytes) + '</div>' +
      '</div>'
    ).join('');
  }

  // Backward-compat wrapper — historical callers target Step 3.
  function renderManifest(m) {
    renderManifestTo(pagesSummary, pageGrid, m);
    renderManifestTo(step2Summary, step2Grid, m);
  }

  async function loadManifestForSlug(slug) {
    activeSlug = slug;
    try {
      const r = await fetch(API + '/ingestion/' + slug + '/manifest');
      if (!r.ok) {
        const msg = '<div class="fw-empty">Manifest fetch failed (HTTP ' +
          r.status + ').</div>';
        pageGrid.innerHTML = msg;
        step2Grid.innerHTML = msg;
        return;
      }
      renderManifest(await r.json());
    } catch (e) {
      const msg = '<div class="fw-empty">' + String(e) + '</div>';
      pageGrid.innerHTML = msg;
      step2Grid.innerHTML = msg;
    }
  }

  // ============================================================
  // Step 2: progress display + polling
  // ============================================================
  function renderProgress(p) {
    if (!p) return;
    progressTier.textContent = p.tier || '—';
    progressStatus.textContent = p.status || '—';
    progressUrl.textContent = p.last_url || '';
    if (p.total && p.total > 0) {
      progressBar.classList.remove('indeterminate');
      const pct = Math.min(100, Math.round((p.current / p.total) * 100));
      progressFill.style.width = pct + '%';
      progressCounter.textContent =
        (p.current || 0) + ' / ' + p.total + ' (' + pct + '%)';
    } else {
      progressBar.classList.add('indeterminate');
      progressFill.style.width = '35%';
      progressCounter.textContent = (p.current || 0) + ' so far…';
    }
  }

  async function pollRun(runId) {
    pollAbort = false;
    activeRunId = runId;
    refreshGenerateState();   // disable Generate while this run is in flight
    progressBox.style.display = '';   // reveal the live progress display
    while (!pollAbort && activeRunId === runId) {
      try {
        const r = await fetch(API + '/runs/' + runId);
        if (r.status === 404) { await sleep(800); continue; }
        const data = await r.json();
        renderProgress(data.progress);
        const st = data.progress?.status;
        if (st === 'done') {
          activeRunId = null;
          refreshGenerateState();
          await loadManifestForSlug(activeSlug);
          await loadLibrary();
          jumpTo(3);
          return;
        }
        if (st === 'failed' || st === 'cancelled') {
          activeRunId = null;
          refreshGenerateState();
          await loadLibrary();
          showToast('Ingestion ' + st + '. ' +
            (st === 'cancelled' ? 'Partial pages cleared from storage.' : ''));
          return;
        }
      } catch (e) {
        // transient — retry
      }
      await sleep(1500);
    }
  }

  cancelBtn.addEventListener('click', async () => {
    if (!activeRunId) return;
    cancelBtn.disabled = true;
    try {
      await fetch(API + '/runs/' + activeRunId + '/cancel', {method: 'POST'});
    } finally {
      // Poll loop will pick up the cancelled status and surface a toast.
      cancelBtn.disabled = false;
    }
  });

  // ============================================================
  // POST /runs — Generate / Refresh
  // ============================================================
  async function triggerIngest(slug, refresh) {
    hideToast(); hideNotice();
    activeSlug = slug;
    try {
      const r = await fetch(API + '/runs', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({slug: slug, refresh: !!refresh}),
      });
      const data = await r.json();
      if (data.status === 'cached') {
        renderManifest(data.manifest);
        showNotice('Loaded from cache · ingested ' +
          fmtAge(data.manifest?.ingested_at) +
          '. Click ↻ in the sidebar to refresh.');
        farthestStep = 3;
        showStep(3);
        return;
      }
      if (data.status === 'queued') {
        // Claim activeRunId synchronously so showStep(2) doesn't race
        // pollRun and try to fetch the (not-yet-finalized) manifest.
        activeRunId = data.run_id;
        refreshGenerateState();
        jumpTo(2);
        pollRun(data.run_id);
        return;
      }
      if (data.status === 'locked') {
        showToast(data.message || 'Another ingestion is already running for this framework.');
        return;
      }
      showToast('Unexpected response: ' + JSON.stringify(data));
    } catch (e) {
      showToast('Request failed: ' + String(e));
    }
  }

  generate.addEventListener('click', () => {
    if (!selected) return;
    triggerIngest(selected, false);
  });

  // ============================================================
  // Sidebar — library list
  // ============================================================
  function renderSidebar(items) {
    if (!items || items.length === 0) {
      sidebarList.innerHTML =
        '<div class="fw-sidebar-empty">' +
        'No ingested frameworks yet. Pick one in the catalog and click Start Ingestion.' +
        '</div>';
      return;
    }
    const html = items.map(it => {
      const isActive = (it.slug === activeSlug) ? ' active' : '';
      const logo = it.logo
        ? '<img class="fw-lib-logo" src="' + it.logo + '" alt="">'
        : '';
      return '<div class="fw-lib-item' + isActive + '" data-slug="' + it.slug + '">' +
        logo +
        '<div style="flex:1;min-width:0">' +
        '<div class="fw-lib-name">' + (it.framework_name || it.slug) + '</div>' +
        '<div class="fw-lib-meta">' + (it.page_count || 0) + ' pages · ' +
        fmtAge(it.ingested_at) + '</div>' +
        '</div>' +
        '<button class="fw-lib-refresh" data-slug="' + it.slug +
        '" title="Refresh (re-download)">↻</button>' +
        '<button class="fw-lib-delete" data-slug="' + it.slug +
        '" title="Delete this ingestion">🗑</button>' +
        '</div>';
    }).join('');
    sidebarList.innerHTML = html;
    sidebarList.querySelectorAll('.fw-lib-item').forEach(el => {
      el.addEventListener('click', async ev => {
        if (ev.target.closest('.fw-lib-refresh, .fw-lib-delete')) return;
        const slug = el.dataset.slug;
        sidebarList.querySelectorAll('.fw-lib-item').forEach(
          x => x.classList.remove('active'));
        el.classList.add('active');
        await loadManifestForSlug(slug);
        farthestStep = Math.max(farthestStep, 3);
        showStep(3);
      });
    });
    sidebarList.querySelectorAll('.fw-lib-refresh').forEach(b => {
      b.addEventListener('click', ev => {
        ev.stopPropagation();
        triggerIngest(b.dataset.slug, true);
      });
    });
    // Newly-rendered refresh buttons must pick up the current ingest state
    // (a re-render from loadLibrary() during an active run would otherwise
    // give them a fresh enabled state).
    refreshGenerateState();
    sidebarList.querySelectorAll('.fw-lib-delete').forEach(b => {
      b.addEventListener('click', async ev => {
        ev.stopPropagation();
        const slug = b.dataset.slug;
        const row = b.closest('.fw-lib-item');
        const displayName = row.querySelector('.fw-lib-name')?.textContent || slug;

        const ok = await showConfirm(
          'Delete ingestion',
          'Permanently delete "' + displayName + '"? ' +
          'Wipes the manifest + every page body from MinIO. ' +
          'This cannot be undone.',
          'Delete'
        );
        if (!ok) return;

        // Replace 🗑 with spinner + lock the row so a stray click can't
        // re-fire delete or jump to another framework mid-DELETE.
        const refresh = row.querySelector('.fw-lib-refresh');
        const originalLabel = b.innerHTML;
        b.innerHTML = '<div class="fw-spinner"></div>';
        b.setAttribute('disabled', 'disabled');
        if (refresh) refresh.setAttribute('disabled', 'disabled');
        row.style.pointerEvents = 'none';
        row.style.opacity = '0.7';

        try {
          const r = await fetch(API + '/ingestion/' + slug, {method: 'DELETE'});
          if (!r.ok) throw new Error('HTTP ' + r.status);

          // Clear Step 3 if the deleted framework was the one being viewed.
          if (activeSlug === slug) {
            activeSlug = null;
            pageGrid.innerHTML =
              '<div class="fw-empty">Pick an item from the sidebar or ' +
              'generate a new study.</div>';
            pagesSummary.innerHTML = '';
          }
          // Remove the row in place — snappier than a full library reload.
          row.remove();
          if (sidebarList.querySelectorAll('.fw-lib-item').length === 0) {
            sidebarList.innerHTML =
              '<div class="fw-sidebar-empty">' +
              'No ingested frameworks yet. Pick one in the catalog and ' +
              'click Start Ingestion.' +
              '</div>';
          }
          syncStepLocks();   // library may now be empty → lock Steps 2+3
        } catch (e) {
          // Restore on failure so the user can try again.
          b.innerHTML = originalLabel;
          b.removeAttribute('disabled');
          if (refresh) refresh.removeAttribute('disabled');
          row.style.pointerEvents = '';
          row.style.opacity = '';
          showToast('Delete failed: ' + String(e));
        }
      });
    });
  }

  async function loadLibrary() {
    try {
      const r = await fetch(API + '/ingestion');
      if (!r.ok) { renderSidebar([]); syncStepLocks(); return; }
      renderSidebar(await r.json());
    } catch (e) {
      renderSidebar([]);
    }
    syncStepLocks();   // unlock/lock Steps 2+3 based on library presence
  }

  // ============================================================
  // Init
  // ============================================================
  countEl.textContent = total + ' of ' + total;
  renderStepper();
  refreshGenerateState();   // initial pass — disabled until a tile is picked
  loadLibrary();
})();
