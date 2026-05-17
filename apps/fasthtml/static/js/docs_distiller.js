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
  const progressLogos = document.querySelector('#fw-progress-logos');
  const progressFramework = document.querySelector('#fw-progress-framework');
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
  // -------- planner (Step 3) --------
  const plannerStartBtn   = document.querySelector('#fw-planner-start');
  const plannerSubtitle   = document.querySelector('#fw-planner-subtitle');
  const plannerCardsEl    = document.querySelector('#fw-planner-cards');
  const plannerProgressLbl= document.querySelector('#fw-planner-progress-label');
  const plannerModeSel    = document.querySelector('#fw-planner-mode');

  // State
  let activeChip = 'All';
  let query = '';
  let selected = null;            // slug picked in Step 1
  let activeSlug = null;          // slug currently shown in Step 3
  let activeRunId = null;         // run currently being polled
  let pollAbort = false;
  let currentStep = 1;
  let farthestStep = 1;
  // -------- planner --------
  let plannerThreadId = null;
  let plannerPollAbort = false;
  // Substep order MUST match `NODE_ORDER` in
  // services/docs_distiller/planner/graph.py AND the field each node
  // writes (`state.<field>`).
  const PLANNER_SUBSTEP_FIELDS = [
    'raw_files',        // corpus_load
    'relevant_files',   // off_topic
    'deduped_files',    // dedup
    'cached_plan',      // cache_lookup  (special: null is a valid completion)
    'shard_results',    // map
    'chapter_plan',     // reduce
    'validated_plan',   // validate
    'plan_path',        // plan_write
  ];
  // Populated from GET /planner/info — names of substeps actually wired
  // into the runtime graph. Stubs aren't included; their cards render
  // as "future" so the user doesn't expect them to advance.
  let plannerImplemented = new Set();

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
  // slug → {name, logo} lookup. Built from the rendered tiles (catalog)
  // and augmented from the library sidebar (which has logos too). Used
  // by the loading box to label the active ingestion + by recovery.
  // ============================================================
  const frameworkInfo = {};   // slug → {name, logos: [url, ...]}
  function indexTilesForFramework() {
    tiles.forEach(t => {
      const slug = t.dataset.slug;
      const name = t.dataset.name;
      // Multi-logo tile carries a strip of `.fw-tile-logo-multi`;
      // single-logo tile carries `.fw-tile-logo`. Collect whichever.
      const multi = Array.from(t.querySelectorAll('.fw-tile-logo-multi'));
      const single = t.querySelector('.fw-tile-logo');
      const logos = multi.length
        ? multi.map(i => i.src)
        : (single ? [single.src] : []);
      frameworkInfo[slug] = {name, logos};
    });
  }
  indexTilesForFramework();

  function setProgressFramework(slug) {
    const info = frameworkInfo[slug] || {name: slug, logos: []};
    progressFramework.textContent = info.name || slug;
    if (info.logos && info.logos.length) {
      progressLogos.innerHTML = info.logos.map(u =>
        '<img class="fw-progress-logo" src="' + u + '" alt="">'
      ).join('');
      progressLogos.style.display = '';
    } else {
      progressLogos.innerHTML = '';
      progressLogos.style.display = 'none';
    }
  }

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
    // Step 3 — Planner. Refresh start-button enablement based on active
    // ingestion + currently selected sidebar slug.
    if (n === 3) {
      refreshPlannerStartState();
    }
    renderStepper();
  }

  function syncStepLocks() {
    // Steps 2/3/4 unlock when EITHER an ingestion is running OR the library
    // has at least one finalized framework. Otherwise lock back to Step 1.
    const hasLibrary =
      sidebarList.querySelectorAll('.fw-lib-item').length > 0;
    const ingestActive = activeRunId !== null;
    if (hasLibrary || ingestActive) {
      farthestStep = Math.max(farthestStep, 4);
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
    if (currentStep >= 4) return;
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
    // Reset cancel button (a previous cancelled run may have left it
    // in the "Cancelling…" + spinner state).
    cancelBtn.disabled = false;
    cancelBtn.innerHTML = 'Cancel ingestion';
    if (activeSlug) setProgressFramework(activeSlug);
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
          jumpTo(3);   // ingestion → Planner (natural next action)
          refreshPlannerStartState();
          return;
        }
        if (st === 'failed' || st === 'cancelled') {
          const cancelledSlug = activeSlug;
          activeRunId = null;
          refreshGenerateState();
          // Hide the live progress box + restore Step 2 + Step 4 to their
          // initial pick-a-framework state. The dispatcher has already
          // wiped MinIO; we just need the UI to reflect that.
          progressBox.style.display = 'none';
          step2Summary.innerHTML = '';
          step2Grid.innerHTML =
            '<div class="fw-empty">Pick a framework in the catalog or ' +
            'the sidebar to see its downloaded files.</div>';
          // If the user was viewing the cancelled framework on Step 4
          // (Study), clear that too — its files no longer exist.
          if (activeSlug === cancelledSlug) {
            activeSlug = null;
            pagesSummary.innerHTML = '';
            pageGrid.innerHTML =
              '<div class="fw-empty">Pick an item from the sidebar or ' +
              'generate a new study.</div>';
            // Drop sidebar "active" highlight (the cancelled row is gone
            // anyway after loadLibrary, but clear here too).
            sidebarList.querySelectorAll('.fw-lib-item.active')
              .forEach(x => x.classList.remove('active'));
          }
          await loadLibrary();
          refreshPlannerStartState();
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
    // Visible "we heard you" state — spinner + "Cancelling…" replaces
    // the button content, button stays disabled. The watcher in
    // dispatch.py picks up the cancel flag within ~1s, the worker wipes
    // MinIO partial state, pollRun's cancelled branch then hides the
    // entire progressBox (which contains this button), so we don't
    // need an explicit restore on success — pollRun's reset on the
    // NEXT run handles it.
    cancelBtn.disabled = true;
    cancelBtn.innerHTML =
      '<div class="fw-spinner" style="display:inline-block;' +
      'vertical-align:middle;margin-right:8px"></div>Cancelling…';
    progressStatus.textContent = 'cancelling';
    try {
      await fetch(API + '/runs/' + activeRunId + '/cancel', {method: 'POST'});
    } catch (e) {
      // If the POST itself fails, restore the button so the user can retry.
      cancelBtn.disabled = false;
      cancelBtn.innerHTML = 'Cancel ingestion';
      showToast('Cancel request failed: ' + String(e));
    }
  });

  // ============================================================
  // Step 3: Planner — start button, history poll, substep cards
  // ============================================================
  function refreshPlannerStartState() {
    // Three states for the Start/Cancel button:
    //  - idle, ready    → "Start Planner" enabled
    //  - idle, blocked  → "Start Planner" disabled (no slug or ingest active)
    //  - running        → button becomes "Cancel Planner" (always enabled
    //                     during a run; same behavior pattern as Step 2's
    //                     ingestion cancel)
    const running = plannerThreadId !== null;
    if (running) {
      plannerStartBtn.removeAttribute('disabled');
      plannerStartBtn.classList.add('btn-outline');
      plannerStartBtn.classList.remove('btn-primary');
      plannerStartBtn.innerHTML = 'Cancel Planner';
    } else {
      const ready = activeSlug && activeRunId === null;
      if (ready) plannerStartBtn.removeAttribute('disabled');
      else plannerStartBtn.setAttribute('disabled', 'disabled');
      plannerStartBtn.classList.add('btn-primary');
      plannerStartBtn.classList.remove('btn-outline');
      plannerStartBtn.innerHTML = 'Start Planner';
    }
    plannerSubtitle.textContent = activeSlug
      ? ('Framework: ' + activeSlug + (running ? ' · planner running' : ''))
      : 'Pick a framework, then start to generate the chapter plan.';
  }

  function cardEl(idx) {
    return plannerCardsEl.querySelector(
      '.fw-planner-card[data-idx="' + idx + '"]');
  }

  function resetPlannerCards() {
    PLANNER_SUBSTEP_FIELDS.forEach((_, i) => {
      const c = cardEl(i);
      if (!c) return;
      c.classList.remove('running', 'done', 'failed', 'expanded');
      const icon = c.querySelector('.fw-planner-card-icon');
      icon.textContent = '○';
      icon.dataset.status = 'pending';
      c.querySelector('.fw-planner-card-latency').textContent = '';
      c.querySelector('.fw-planner-card-body').innerHTML =
        '<div class="fw-empty">Output will appear here once the substep runs.</div>';
    });
    plannerProgressLbl.textContent = '';
  }

  function _fieldPresent(values, field) {
    // cached_plan is special: the cache_lookup node writes `null` as a
    // valid completion (cache miss). Treat `field in values` (even when
    // value is null) as "this node ran".
    return values && Object.prototype.hasOwnProperty.call(values, field);
  }

  // Per-substep custom body renderers. Each returns an HTML string for
  // the card body. Keyed by substep idx (matches PLANNER_SUBSTEP_FIELDS).
  // Substeps without an entry here fall back to formatFieldValue/JSON.
  const SUBSTEP_RENDERERS = {
    // corpus_load — KPI-card grid + percentile distribution + meta footer.
    // Design follows 2026 dashboard best practices: 4 headline KPI cards
    // (one visual element max per card), then a compact percentile row,
    // then a metadata footer line. Avoids the "Christmas Tree" effect.
    0: function renderCorpusLoad(values) {
      const s = values.corpus_stats || {};
      if (!s.total_files) {
        return '<div class="fw-empty">no corpus stats reported</div>';
      }
      const rate = s.load_ms
        ? Math.round(s.total_files / s.load_ms * 1000)
        : 0;
      const ts = s.ingested_at
        ? new Date(s.ingested_at * 1000).toISOString().replace('T',' ').slice(0, 16) + ' UTC'
        : '—';

      // 4 KPI cards
      const kpi = (label, value, sub) =>
        '<div class="fw-stat-card">' +
          '<div class="fw-stat-card-label">' + escapeHtml(label) + '</div>' +
          '<div class="fw-stat-card-value">' + escapeHtml(value) + '</div>' +
          (sub ? '<div class="fw-stat-card-sub">' + escapeHtml(sub) + '</div>' : '') +
        '</div>';

      const cards =
        kpi('Files',        s.total_files.toLocaleString(), null) +
        kpi('Total bytes',  fmtBytes(s.total_bytes),        null) +
        kpi('Median page',  fmtBytes(s.median_bytes),       null) +
        kpi('Load time',    s.load_ms + ' ms',
                            rate ? rate.toLocaleString() + ' files/s' : null);

      // Compact distribution row — percentiles inline (log-scale not needed
      // when bytes span 3 orders of magnitude; raw numbers tell the story).
      const dist =
        '<div class="fw-stat-dist">' +
          '<div class="fw-stat-dist-title">Page size distribution</div>' +
          '<div class="fw-stat-dist-row">' +
            ['min', 'p10', 'median', 'p90', 'max'].map((k, i) => {
              const val = [s.min_bytes, s.p10_bytes, s.median_bytes,
                           s.p90_bytes, s.max_bytes][i];
              return '<div class="fw-stat-dist-cell">' +
                       '<div class="fw-stat-dist-key">' + k + '</div>' +
                       '<div class="fw-stat-dist-val">' + fmtBytes(val) + '</div>' +
                     '</div>';
            }).join('') +
          '</div>' +
        '</div>';

      // Footer — tier + ingested timestamp
      const foot =
        '<div class="fw-stat-foot">' +
          'Tier <strong>' + escapeHtml(s.tier_kind || '—') + '</strong>' +
          ' · ingested <strong>' + escapeHtml(ts) + '</strong>' +
        '</div>';

      return '<div class="fw-stat-grid">' + cards + '</div>' + dist + foot;
    },

    // off_topic — semantic noise filter. KPI cards (kept/dropped/
    // threshold/domain_coherence) + a "boundary cases" table showing
    // pages within ±0.05 of the threshold (false-positive / false-
    // negative candidates the operator might want to inspect).
    1: function renderOffTopic(values) {
      const s = values.off_topic_stats || {};
      if (s.kept === undefined && s.dropped === undefined) {
        return '<div class="fw-empty">no off_topic stats reported</div>';
      }
      const kept = s.kept || 0;
      const dropped = s.dropped || 0;
      const total = kept + dropped;
      const dropPct = total ? Math.round(dropped / total * 100) : 0;
      const elapsed = s.elapsed_ms || 0;

      const kpi = (label, value, sub) =>
        '<div class="fw-stat-card">' +
          '<div class="fw-stat-card-label">' + escapeHtml(label) + '</div>' +
          '<div class="fw-stat-card-value">' + escapeHtml(value) + '</div>' +
          (sub ? '<div class="fw-stat-card-sub">' + escapeHtml(sub) + '</div>' : '') +
        '</div>';

      const cards =
        kpi('Kept',    kept.toLocaleString(), 'of ' + total.toLocaleString()) +
        kpi('Dropped', dropped.toLocaleString(), dropPct + '% off-topic') +
        kpi('Threshold', String(s.threshold || 0), 'cosine cutoff') +
        kpi('Domain coherence', (s.domain_coherence || 0).toFixed(3),
            'mean cos of kept set');

      // Boundary table: pages within ±0.05 of threshold.
      const t = s.threshold || 0.30;
      const perFile = s.per_file_cosines || [];
      const boundary = perFile
        .filter(([_n, c, _k]) => Math.abs(c - t) <= 0.05)
        .sort((a, b) => Math.abs(a[1] - t) - Math.abs(b[1] - t))
        .slice(0, 12);

      let table = '';
      if (boundary.length) {
        const rows = boundary.map(([name, c, k]) =>
          '<tr>' +
            '<td>' + (k
              ? '<span style="color:#2a8b46">●</span>'
              : '<span style="color:var(--error-text)">●</span>') + '</td>' +
            '<td style="font-family:JetBrains Mono,monospace;font-size:0.78rem">' +
              c.toFixed(4) + '</td>' +
            '<td style="font-size:0.78rem;color:var(--text-muted)">' +
              escapeHtml(name) + '</td>' +
          '</tr>'
        ).join('');
        table =
          '<div class="fw-stat-dist" style="margin-top:14px">' +
            '<div class="fw-stat-dist-title">Boundary cases ' +
              '(±0.05 from threshold)</div>' +
            '<table style="width:100%;border-collapse:collapse;font-family:Raleway">' +
              '<thead><tr style="border-bottom:1px solid var(--border)">' +
                '<th style="text-align:left;padding:4px 8px 8px 0;font-size:0.7rem;color:var(--text-muted);text-transform:uppercase">In</th>' +
                '<th style="text-align:left;padding:4px 8px 8px 0;font-size:0.7rem;color:var(--text-muted);text-transform:uppercase">Cosine</th>' +
                '<th style="text-align:left;padding:4px 0 8px 0;font-size:0.7rem;color:var(--text-muted);text-transform:uppercase">Page</th>' +
              '</tr></thead>' +
              '<tbody>' + rows + '</tbody>' +
            '</table>' +
          '</div>';
      }

      const foot =
        '<div class="fw-stat-foot">' +
          'NIM <strong>nvidia/llama-nemotron-embed-1b-v2</strong>' +
          ' · ' + elapsed + ' ms total' +
        '</div>';

      return '<div class="fw-stat-grid">' + cards + '</div>' + table + foot;
    },
  };

  function renderPlannerCards(values) {
    // values = the latest checkpoint's accumulated state
    let doneCount = 0;
    for (let i = 0; i < PLANNER_SUBSTEP_FIELDS.length; i++) {
      const field = PLANNER_SUBSTEP_FIELDS[i];
      const c = cardEl(i);
      if (!c) continue;
      const icon = c.querySelector('.fw-planner-card-icon');
      const body = c.querySelector('.fw-planner-card-body');
      const present = _fieldPresent(values, field);
      // Substep name = the PLANNER_SUBSTEPS index → graph node name.
      // Lookup the implementation flag for visual treatment.
      const cardData = c.dataset.substep || '';
      const isImplemented = plannerImplemented.has(cardData);
      if (present) {
        c.classList.add('done');
        c.classList.remove('running', 'failed', 'future');
        icon.textContent = '●'; icon.dataset.status = 'done';
        const renderer = SUBSTEP_RENDERERS[i];
        if (renderer) {
          body.innerHTML = renderer(values);
        } else {
          const v = values[field];
          body.innerHTML = '<pre>' + escapeHtml(formatFieldValue(v)) + '</pre>';
        }
        doneCount++;
      } else if (!isImplemented) {
        // Substep stub — not wired into the runtime graph. Render as
        // "future" so the user sees it's a planned step, not a failure.
        c.classList.add('future');
        c.classList.remove('running', 'done', 'failed');
        icon.textContent = '⏳'; icon.dataset.status = 'future';
        body.innerHTML =
          '<div class="fw-empty">Substep not yet implemented — will be ' +
          'wired into the graph as its real logic lands.</div>';
      } else if (i === doneCount && plannerThreadId !== null) {
        // First not-done IMPLEMENTED card while polling = currently running
        c.classList.add('running');
        c.classList.remove('done', 'failed', 'future');
        icon.textContent = '◐'; icon.dataset.status = 'running';
      } else {
        c.classList.remove('running', 'done', 'failed', 'future');
        icon.textContent = '○'; icon.dataset.status = 'pending';
      }
    }
    plannerProgressLbl.textContent =
      'Step ' + doneCount + ' of ' + PLANNER_SUBSTEP_FIELDS.length;
  }

  function markPlannerFailed(message) {
    // Find the first card still running (or first pending) and flag it.
    for (let i = 0; i < PLANNER_SUBSTEP_FIELDS.length; i++) {
      const c = cardEl(i);
      if (!c) continue;
      if (c.classList.contains('running') ||
          (!c.classList.contains('done') && !c.classList.contains('failed'))) {
        c.classList.remove('running');
        c.classList.add('failed', 'expanded');
        const icon = c.querySelector('.fw-planner-card-icon');
        icon.textContent = '✕';
        icon.dataset.status = 'failed';
        c.querySelector('.fw-planner-card-body').innerHTML =
          '<div class="fw-planner-error">' + escapeHtml(message) + '</div>';
        break;
      }
    }
  }

  function formatFieldValue(v) {
    if (v === null || v === undefined) return String(v);
    if (Array.isArray(v)) {
      if (v.length === 0) return '[]';
      const head = v.slice(0, 20).map(x => '  ' + JSON.stringify(x)).join(',\n');
      const tail = v.length > 20 ? '\n  … (' + (v.length - 20) + ' more)' : '';
      return '[\n' + head + tail + '\n] (' + v.length + ' items)';
    }
    return JSON.stringify(v, null, 2);
  }

  function escapeHtml(s) {
    return String(s).replace(/[&<>"']/g, c => ({
      '&': '&amp;', '<': '&lt;', '>': '&gt;',
      '"': '&quot;', "'": '&#39;',
    }[c]));
  }

  async function pollPlanner(threadId) {
    plannerPollAbort = false;
    while (!plannerPollAbort && plannerThreadId === threadId) {
      try {
        // thread_id has slashes (docs-distiller/{slug}/{uuid}). Don't
        // encode — the FastAPI `:path` converter accepts slashes; the
        // smoke test in /history confirmed unencoded paths round-trip.
        const r = await fetch(
          API + '/planner/debug/graph/' + threadId + '/state');
        if (r.status === 404) { await sleep(700); continue; }
        if (!r.ok) { await sleep(1500); continue; }
        const data = await r.json();
        const values = data.values || {};
        renderPlannerCards(values);
        if (values.status === 'done') {
          plannerThreadId = null;
          refreshPlannerStartState();
          return;
        }
        if (values.status === 'failed') {
          markPlannerFailed(values.error || 'Planner failed.');
          plannerThreadId = null;
          refreshPlannerStartState();
          return;
        }
      } catch (e) { /* transient — retry */ }
      await sleep(1000);
    }
  }

  function _genPlannerThreadId(slug) {
    // Client-side UUID v4 — uses crypto.randomUUID where available,
    // falls back to a sufficient-quality polyfill for older browsers.
    const uuid = (typeof crypto !== 'undefined' && crypto.randomUUID)
      ? crypto.randomUUID()
      : 'xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx'.replace(/[xy]/g, c => {
          const r = Math.random() * 16 | 0;
          return (c === 'x' ? r : (r & 0x3 | 0x8)).toString(16);
        });
    return 'docs-distiller/' + slug + '/' + uuid;
  }

  async function pollPlannerState(threadId) {
    // Polls /debug/graph/.../state every 1.5s while this thread is
    // still the active one. Each tick re-renders the substep cards so
    // the user sees corpus_load complete → off_topic start → off_topic
    // complete as checkpoints land. Exits when startPlanner clears
    // plannerThreadId (or it changes to a new run).
    while (plannerThreadId === threadId) {
      try {
        const r = await fetch(
          API + '/planner/debug/graph/' + threadId + '/state');
        if (r.ok) {
          const data = await r.json();
          renderPlannerCards(data.values || {});
        }
        // 404 is normal in the brief window before the first checkpoint
        // is written — just retry next tick.
      } catch (e) { /* transient */ }
      await sleep(1500);
    }
  }

  async function startPlanner() {
    if (!activeSlug || plannerThreadId) return;
    resetPlannerCards();
    // Generate thread_id client-side so the Cancel button + polling
    // loop both have a real ID from click 1 (no 'pending' dead-zone).
    const tid = _genPlannerThreadId(activeSlug);
    plannerThreadId = tid;
    refreshPlannerStartState();   // button flips to "Cancel Planner"
    // Kick off polling in parallel with the main POST so the user sees
    // cards advance progressively.
    pollPlannerState(tid);
    try {
      const mode = (plannerModeSel && plannerModeSel.value) || 'llm';
      const r = await fetch(
        API + '/planner/' + activeSlug +
        '?mode=' + encodeURIComponent(mode) +
        '&thread_id=' + encodeURIComponent(tid),
        {method: 'POST'},
      );
      if (!r.ok) {
        const txt = await r.text();
        markPlannerFailed('HTTP ' + r.status + ': ' + txt.slice(0, 400));
        plannerThreadId = null;
        refreshPlannerStartState();
        return;
      }
      const data = await r.json();
      // POST returned — render terminal state, stop polling loop.
      renderPlannerCards(data.state || {});
      if (data.status === 'cancelled') {
        showToast('Planner cancelled. ' +
          'Checkpoints up to the cancel point are preserved.');
      } else if (data.status === 'failed') {
        markPlannerFailed(data.state?.error || 'Planner failed');
      }
      plannerThreadId = null;
      refreshPlannerStartState();
    } catch (e) {
      markPlannerFailed('Request failed: ' + String(e));
      plannerThreadId = null;
      refreshPlannerStartState();
    }
  }

  async function cancelPlanner() {
    if (!plannerThreadId) return;
    const tid = plannerThreadId;
    // Spinner + "Cancelling…" — mirrors the Step 2 ingestion cancel UX.
    plannerStartBtn.setAttribute('disabled', 'disabled');
    plannerStartBtn.innerHTML =
      '<div class="fw-spinner" style="display:inline-block;' +
      'vertical-align:middle;margin-right:8px"></div>Cancelling…';
    try {
      // Fire-and-forget — the cancel watcher on the server detects the
      // Redis flag within ~1s, raises CancelledError inside graph.ainvoke,
      // and the in-flight POST /planner/{slug} returns with
      // status='cancelled'. THAT response triggers the UI cleanup
      // (refreshPlannerStartState in startPlanner's finally).
      await fetch(API + '/planner/' + tid + '/cancel', {method: 'POST'});
    } catch (e) {
      // If the cancel POST itself fails, restore the button so the user
      // can retry. The startPlanner POST is still in flight either way.
      plannerStartBtn.removeAttribute('disabled');
      plannerStartBtn.innerHTML = 'Cancel Planner';
      showToast('Cancel request failed: ' + String(e));
    }
  }

  plannerStartBtn.addEventListener('click', () => {
    // Dual-purpose: Start when idle, Cancel when a thread_id is set.
    if (plannerThreadId) {
      cancelPlanner();
    } else {
      startPlanner();
    }
  });

  // Card-head click → toggle expanded body
  plannerCardsEl.addEventListener('click', ev => {
    const head = ev.target.closest('.fw-planner-card-head');
    if (!head) return;
    head.parentElement.classList.toggle('expanded');
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
        farthestStep = 4;
        showStep(4);   // jump to Study (cached → user wants to view)
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
    // Augment frameworkInfo from the library list so recovery + sidebar
    // clicks can label the loading box even for frameworks that aren't
    // in the catalog tile set (or were ingested via the audit endpoint).
    if (items) {
      items.forEach(it => {
        if (it.slug && !frameworkInfo[it.slug]) {
          // Prefer `logos` array from the catalog (multi-logo stack);
          // fall back to the single `logo` for everyday entries.
          const logos = (it.logos && it.logos.length)
            ? it.logos
            : (it.logo ? [it.logo] : []);
          frameworkInfo[it.slug] = {
            name: it.framework_name || it.slug,
            logos,
          };
        }
      });
    }
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
        farthestStep = Math.max(farthestStep, 4);
        showStep(4);   // sidebar click → Study (view existing files)
        refreshPlannerStartState();
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
  // Page-reload recovery — restore active-ingestion state from Redis.
  // ============================================================
  // Without this, refreshing the page mid-ingestion wipes the in-memory
  // activeRunId/activeSlug → the loading box vanishes and the user can
  // re-click Start Ingestion (which the backend single-flight lock would
  // deny with "locked", but the UX is jarring). With this, the UI
  // re-attaches to any still-running run on page load: resumes polling,
  // restores the progress display, blocks the Generate button.
  async function recoverActiveRuns() {
    try {
      const r = await fetch(API + '/runs/active');
      if (!r.ok) return;
      const data = await r.json();
      const runs = data.active || [];
      if (runs.length === 0) return;
      // Resume the first active run (single-flight lock is per-slug so
      // multiple concurrent runs across different slugs are theoretically
      // possible; we surface the first one — the others remain protected
      // by their own locks, user will see them when they finish).
      const run = runs[0];
      activeSlug = run.slug;
      activeRunId = run.run_id;
      farthestStep = Math.max(farthestStep, 2);
      refreshGenerateState();   // disables Start + sidebar refresh/delete
      showStep(2);              // reveal the live progress box
      setProgressFramework(run.slug);
      // Paint the last-known progress immediately so the UI is populated
      // before the first poll tick lands.
      if (run.progress) renderProgress(run.progress);
      pollRun(run.run_id);      // resume the poll loop
      showNotice(
        'Resumed in-flight ingestion of ' + run.slug + ' (started ' +
        fmtAge(run.progress?.updated_at) + ').'
      );
    } catch (e) { /* silent — nothing to recover */ }
  }

  async function loadPlannerInfo() {
    try {
      const r = await fetch(API + '/planner/info');
      if (!r.ok) return;
      const data = await r.json();
      plannerImplemented = new Set(data.implemented || []);
      // Hydrate the mode dropdown from the server's canonical mode list.
      // Server-rendered defaults work even without this, but a future
      // mode addition (e.g. "hybrid") shows up automatically.
      if (Array.isArray(data.modes) && plannerModeSel) {
        const currentVal = plannerModeSel.value;
        plannerModeSel.innerHTML = data.modes.map(m => {
          const label = m.enabled ? m.label : m.label + ' (soon)';
          const sel = (m.key === currentVal && m.enabled) ? ' selected' : '';
          const dis = m.enabled ? '' : ' disabled';
          return '<option value="' + m.key + '"' + sel + dis + '>' +
                 escapeHtml(label) + '</option>';
        }).join('');
        // If the previously-selected value got disabled, fall back to
        // the first enabled mode.
        const enabled = data.modes.filter(m => m.enabled);
        if (enabled.length && !enabled.find(m => m.key === plannerModeSel.value)) {
          plannerModeSel.value = enabled[0].key;
        }
      }
      // Re-render the cards now that we know which are implemented vs
      // future — turns unimplemented stubs into the "⏳ future" state.
      renderPlannerCards({});
    } catch (e) { /* silent — defaults to all "pending" */ }
  }

  // ============================================================
  // Init
  // ============================================================
  countEl.textContent = total + ' of ' + total;
  renderStepper();
  refreshGenerateState();   // initial pass — disabled until a tile is picked
  // Library first (populates sidebar + syncStepLocks), then recover any
  // mid-flight runs — recovery may flip currentStep to 2 which depends on
  // library state already being known.
  loadLibrary().then(recoverActiveRuns);
  loadPlannerInfo();
})();
