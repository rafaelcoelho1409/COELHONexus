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
  const plannerWipeBtn    = document.querySelector('#fw-planner-wipe');
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
  // Used by _tryResumeActivePlanner's orphan-detection timeout: cleared
  // when an SSE event arrives so we can distinguish a stuck "running"
  // state (no live task) from an actively-running one.
  let _liveEventReceived = false;
  let plannerPollAbort = false;
  // Substep order MUST match `NODE_ORDER` in
  // services/docs_distiller/planner/graph.py AND the field each node
  // writes (`state.<field>`).
  const PLANNER_SUBSTEP_FIELDS = [
    'raw_files',        // corpus_load
    'embeddings_ref',   // embed_corpus
    'relevant_files',   // off_topic
    'deduped_files',    // dedup
    'cached_plan',      // cache_lookup  (special: null is a valid completion)
    'shard_results',    // map
    'chapter_plan',     // reduce
    'validated_plan',   // validate
    'plan_path',        // plan_write
  ];
  // Parallel to PLANNER_SUBSTEP_FIELDS — the node name (matches the
  // server-side step name in SSE events). Used by the SSE handler to
  // map step → previous step → expected checkpoint field.
  const PLANNER_NODE_ORDER = [
    'corpus_load', 'embed_corpus', 'off_topic',
    'dedup', 'cache_lookup', 'map',
    'reduce', 'validate', 'plan_write',
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
    // Page-refresh recovery for the planner step. If localStorage knows
    // about a planner run for this slug, try to reconnect to its SSE
    // stream and paint whatever progress has happened so far. Mirrors
    // the loading-box recovery on the Ingestion step.
    _tryResumeActivePlanner(slug).catch(() => {});
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
    // Wipe button — enabled whenever a slug is active and no run is
    // currently in flight (wiping mid-run would corrupt LangGraph state).
    if (plannerWipeBtn) {
      if (activeSlug && !running) {
        plannerWipeBtn.removeAttribute('disabled');
      } else {
        plannerWipeBtn.setAttribute('disabled', 'disabled');
      }
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

    // embed_corpus — one-shot NIM pass; KPI cards show files / dim /
    // cache_hit / wall_ms / blob path. Cache-hit runs report ~10 ms
    // (just the HEAD + read); cold runs show the full embedding wall.
    1: function renderEmbedCorpus(values) {
      const s = values.embed_stats || {};
      if (!s.files) {
        return '<div class="fw-empty">no embed stats reported</div>';
      }
      const kpi = (label, value, sub) =>
        '<div class="fw-stat-card">' +
          '<div class="fw-stat-card-label">' + escapeHtml(label) + '</div>' +
          '<div class="fw-stat-card-value">' + escapeHtml(value) + '</div>' +
          (sub ? '<div class="fw-stat-card-sub">' + escapeHtml(sub) + '</div>' : '') +
        '</div>';

      const cacheLabel = s.cache_hit ? 'HIT' : 'cold';
      const cacheSub   = s.cache_hit
        ? 'reused stored vectors'
        : 'NIM embedding pass';
      const blobKB = s.blob_bytes
        ? Math.round(s.blob_bytes / 1024).toLocaleString() + ' KB blob'
        : null;

      const cards =
        kpi('Files',     s.files.toLocaleString(), null) +
        kpi('Dimensions', String(s.dim || 0),       'per-doc vector') +
        kpi('Cache',     cacheLabel,                cacheSub) +
        kpi('Wall time', (s.wall_ms || 0) + ' ms',  blobKB);

      const truncatedLine = (s.truncated_count !== undefined && s.truncated_count > 0)
        ? ' · truncated <strong>' + s.truncated_count.toLocaleString() + '</strong>'
        : '';

      const foot =
        '<div class="fw-stat-foot">' +
          'NIM <strong>nvidia/llama-nemotron-embed-1b-v2</strong>' +
          ' · hash <strong>' + escapeHtml(s.manifest_hash || '—') + '</strong>' +
          truncatedLine +
          ' · path <code style="font-family:JetBrains Mono,monospace;font-size:0.72rem">' +
            escapeHtml(s.store_path || '—') + '</code>' +
        '</div>';

      return '<div class="fw-stat-grid">' + cards + '</div>' + foot;
    },

    // off_topic — pure LLM-as-Judge (no cosine cleave). Every doc is
    // routed through the ParetoBandit-driven dd-grader cells: the bandit
    // picks the top-K best deployments by UCB score, calls each via
    // direct litellm, and submits reward signals so future calls learn
    // which deployments are reliable. KPI cards show keep/drop split +
    // bandit telemetry (deployments used + average reward). The verdict
    // sample table shows per-page judgments with the model that answered.
    2: function renderOffTopic(values) {
      const s = values.off_topic_stats || {};
      if (s.kept === undefined && s.dropped === undefined) {
        return '<div class="fw-empty">no off_topic stats reported</div>';
      }
      const kept     = s.kept    || 0;
      const dropped  = s.dropped || 0;
      const total    = s.total   || (kept + dropped);
      const dropPct  = total ? Math.round(dropped / total * 100) : 0;
      const elapsed  = s.elapsed_ms || 0;
      const judged   = s.llm_judged || 0;
      const lkeep    = s.llm_kept || 0;
      const ldrop    = s.llm_dropped || 0;
      const lerr     = s.llm_errors || 0;
      const depUsage = s.deployment_usage || [];

      const kpi = (label, value, sub) =>
        '<div class="fw-stat-card">' +
          '<div class="fw-stat-card-label">' + escapeHtml(label) + '</div>' +
          '<div class="fw-stat-card-value">' + escapeHtml(value) + '</div>' +
          (sub ? '<div class="fw-stat-card-sub">' + escapeHtml(sub) + '</div>' : '') +
        '</div>';

      const topDep = depUsage[0]
        ? (depUsage[0].deployment.split('/').pop() + ' · ' + depUsage[0].calls + ' calls')
        : '—';
      const cards =
        kpi('Kept',    kept.toLocaleString(),    'of ' + total.toLocaleString()) +
        kpi('Dropped', dropped.toLocaleString(), dropPct + '% off-topic') +
        kpi('LLM judged', judged.toLocaleString(),
            '+keep ' + lkeep + ' · -drop ' + ldrop +
            (lerr ? ' · err ' + lerr : '')) +
        kpi('Top deployment', topDep,
            depUsage.length > 1 ? '+' + (depUsage.length - 1) + ' more' : null);

      // LLM verdict table — focused on the NEW telemetry that matters
      // now (which model answered + latency), since cosine margin is
      // no longer a decision input. ALL decisions rendered into a
      // scrollable container (sticky header) so the operator can
      // inspect every per-page verdict without clicking through pages.
      const decisions = s.judge_decisions || [];
      let table = '';
      if (decisions.length) {
        const rows = decisions.map(d => {
          const keep = d.verdict === 'KEEP';
          const dot = keep
            ? '<span style="color:#2a8b46">●</span>'
            : '<span style="color:var(--error-text)">●</span>';
          const errBadge = d.error
            ? '<span title="' + escapeHtml(d.error) + '" style="margin-left:4px;font-size:0.7rem;color:var(--accent)">!</span>'
            : '';
          const leaf = (d.key || '').split('/').pop();
          const depShort = (d.deployment || '?').split('/').pop();
          const lat = (d.latency_s !== undefined && d.latency_s !== null)
            ? d.latency_s.toFixed(2) + 's' : '—';
          return '<tr>' +
            '<td style="padding:3px 8px 3px 0">' + dot + errBadge + '</td>' +
            '<td style="padding:3px 8px 3px 0;font-size:0.78rem;font-weight:600">' +
              escapeHtml(d.verdict || '—') + '</td>' +
            '<td style="padding:3px 8px 3px 0;font-family:JetBrains Mono,monospace;font-size:0.72rem;color:var(--text-muted)">' +
              escapeHtml(depShort) + '</td>' +
            '<td style="padding:3px 8px 3px 0;font-family:JetBrains Mono,monospace;font-size:0.72rem;color:var(--text-muted)">' +
              lat + '</td>' +
            '<td style="padding:3px 0;font-size:0.78rem;color:var(--text-muted)">' +
              escapeHtml(leaf) + '</td>' +
          '</tr>';
        }).join('');
        const headStyle =
          'position:sticky;top:0;background:var(--surface);' +
          'text-align:left;padding:6px 8px 8px 0;font-size:0.7rem;' +
          'color:var(--text-muted);text-transform:uppercase;' +
          'border-bottom:1px solid var(--border);z-index:1';
        table =
          '<div class="fw-stat-dist" style="margin-top:14px">' +
            '<div class="fw-stat-dist-title">LLM verdict (' +
              decisions.length + ' decisions, scroll to inspect all)</div>' +
            '<div style="max-height:340px;overflow-y:auto;border:1px solid var(--border);border-radius:4px">' +
              '<table style="width:100%;border-collapse:collapse;font-family:Raleway">' +
                '<thead><tr>' +
                  '<th style="' + headStyle + ';padding-left:8px">In</th>' +
                  '<th style="' + headStyle + '">Verdict</th>' +
                  '<th style="' + headStyle + '">Deployment</th>' +
                  '<th style="' + headStyle + '">Latency</th>' +
                  '<th style="' + headStyle + '">Page</th>' +
                '</tr></thead>' +
                '<tbody>' + rows + '</tbody>' +
              '</table>' +
            '</div>' +
          '</div>';
      }

      // Bandit deployment breakdown — show all that answered with reward avg.
      let depRow = '';
      if (depUsage.length) {
        const drows = depUsage.slice(0, 10).map(d => {
          const r = (d.reward_avg !== undefined && d.reward_avg !== null)
            ? d.reward_avg.toFixed(3) : '—';
          return '<tr>' +
            '<td style="padding:3px 12px 3px 0;font-size:0.78rem">' +
              escapeHtml((d.deployment || '?').split('/').pop()) + '</td>' +
            '<td style="padding:3px 12px 3px 0;font-family:JetBrains Mono,monospace;font-size:0.78rem">' +
              d.calls + ' calls</td>' +
            '<td style="padding:3px 0;font-family:JetBrains Mono,monospace;font-size:0.78rem;color:var(--text-muted)">' +
              'reward avg ' + r + '</td>' +
            '</tr>';
        }).join('');
        depRow =
          '<div class="fw-stat-dist" style="margin-top:14px">' +
            '<div class="fw-stat-dist-title">Bandit deployment usage (top ' +
              Math.min(10, depUsage.length) + ')</div>' +
            '<table style="width:100%;border-collapse:collapse;font-family:Raleway">' +
              '<tbody>' + drows + '</tbody>' +
            '</table>' +
          '</div>';
      }

      const embedModel = s.embed_model || 'nvidia/llama-nemotron-embed-1b-v2';
      const router = s.judge_router || 'pareto-bandit/dd-grader';
      const foot =
        '<div class="fw-stat-foot">' +
          'embed <strong>' + escapeHtml(embedModel) + '</strong>' +
          ' · judge <strong>' + escapeHtml(router) + '</strong>' +
          ' · LLM judge ' + judged + ' calls (concurrency ' +
            (s.judge_concurrency || '?') + ')' +
          ' · coherence ' + (s.domain_coherence || 0).toFixed(3) +
          ' · ' + elapsed + ' ms total' +
        '</div>';

      return '<div class="fw-stat-grid">' + cards + '</div>' + table + depRow + foot;
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

  // Live progress text per substep card (populated by SSE events).
  // Keyed by step name (matches the node names emitted server-side).
  function _liveProgressEl(stepName, idx) {
    const c = cardEl(idx);
    if (!c) return null;
    const body = c.querySelector('.fw-planner-card-body');
    if (!body) return null;
    let el = body.querySelector('.fw-planner-card-live');
    if (!el) {
      el = document.createElement('div');
      el.className = 'fw-planner-card-live';
      el.style.cssText =
        'font-family:JetBrains Mono,monospace;font-size:0.78rem;' +
        'color:var(--text-muted);padding:8px 12px;border-top:1px dashed var(--border);' +
        'margin-top:8px';
      body.appendChild(el);
    }
    return el;
  }

  function _stepIdx(stepName) {
    return PLANNER_SUBSTEP_FIELDS.findIndex((_, i) =>
      cardEl(i)?.dataset.substep === stepName);
  }

  function _markCardRunning(stepName) {
    const idx = _stepIdx(stepName);
    if (idx < 0) return;
    const c = cardEl(idx);
    if (!c) return;
    // Don't downgrade an already-completed card. Without this guard, SSE
    // snapshot replay during page-refresh recovery would re-process the
    // original `start` event for an already-done step and flip its
    // spinner back to running, hiding the KPI grid behind a stale
    // "filtering N files…" live-progress line.
    if (c.classList.contains('done')) return;
    c.classList.add('running');
    c.classList.remove('failed', 'future');
    const icon = c.querySelector('.fw-planner-card-icon');
    if (icon) { icon.textContent = '◐'; icon.dataset.status = 'running'; }
    // Clear the "Output will appear here..." placeholder so the live
    // progress sub-element has room.
    const body = c.querySelector('.fw-planner-card-body');
    if (body && body.querySelector('.fw-empty')) {
      body.innerHTML = '';
    }
  }

  function _renderLiveProgress(stepName, ev) {
    const idx = _stepIdx(stepName);
    if (idx < 0) return;
    const c = cardEl(idx);
    // Same reason as _markCardRunning: skip live-text rewrites for
    // cards already marked done by the LangGraph state snapshot.
    if (c && c.classList.contains('done')) return;
    const el = _liveProgressEl(stepName, idx);
    if (!el) return;
    let text = '';
    if (stepName === 'corpus_load') {
      if (ev.kind === 'start')      text = '· reading manifest…';
      else if (ev.kind === 'done')  text = '✓ ' + (ev.files||0).toLocaleString() + ' files, ' + ((ev.total_bytes||0)/1024|0) + ' KB';
    } else if (stepName === 'embed_corpus') {
      if (ev.kind === 'start')             text = '· starting NIM embed (' + (ev.files||0) + ' files)…';
      else if (ev.kind === 'chunks_prepared') text = '· ' + (ev.chunks_total||0).toLocaleString() + ' chunks prepared (' + (ev.docs_chunked||0) + '/' + (ev.docs_total||0) + ' docs split)';
      else if (ev.kind === 'batch')        text = '· embedding chunk ' + (ev.chunks_done||0).toLocaleString() + ' / ' + (ev.chunks_total||0).toLocaleString();
      else if (ev.kind === 'done')         text = '✓ ' + (ev.files||0).toLocaleString() + ' vectors @ ' + (ev.dim||'?') + '-D (' + (ev.cache_hit ? 'cache hit' : ((ev.wall_ms||0) + ' ms cold') ) + ')';
    } else if (stepName === 'off_topic') {
      if (ev.kind === 'start')              text = '· filtering ' + (ev.files||0).toLocaleString() + ' files…';
      else if (ev.kind === 'anchors_embedded') text = '· anchors embedded (pos + neg) · LLM-as-Judge routing via ParetoBandit/dd-grader';
      else if (ev.kind === 'llm_progress')  text = '· LLM judged ' + (ev.judged||0).toLocaleString() + ' / ' + (ev.total||0).toLocaleString() + ' (keep ' + (ev.llm_keep||0) + ', drop ' + (ev.llm_drop||0) + (ev.llm_err ? ', err ' + ev.llm_err : '') + ')';
      else if (ev.kind === 'done')          text = '✓ kept ' + (ev.kept||0).toLocaleString() + '/' + (ev.total||0).toLocaleString() + ' (' + (ev.wall_ms||0) + ' ms)';
    }
    if (text) el.textContent = text;
  }

  // Race-tolerant state fetch. The LangGraph checkpoint commit lands a
  // tick AFTER the node's `done` event fires on the SSE channel, so a
  // naive fetch right after `done` may see stale state. When the caller
  // knows which field is expected to have just appeared, we retry with
  // backoff until it's present (or we exhaust attempts).
  async function _refreshCardsFromState(threadId, expectedField) {
    const maxAttempts = expectedField ? 6 : 1;
    for (let i = 0; i < maxAttempts; i++) {
      try {
        const r = await fetch(API + '/planner/debug/graph/' + threadId + '/state');
        if (r.ok) {
          const data = await r.json();
          const values = data.values || {};
          if (!expectedField || _fieldPresent(values, expectedField)) {
            renderPlannerCards(values);
            return;
          }
        }
      } catch (e) { /* transient */ }
      await sleep(250 + 150 * i);   // ~250ms / 400 / 550 / 700 / 850 / 1000
    }
  }

  // Mapping: SSE step name → the state field that becomes present once
  // that node's checkpoint is committed. Used by the retry-fetch above
  // so we wait for the previous node's commit before re-rendering.
  const STEP_TO_FIELD = {
    corpus_load:  'raw_files',
    embed_corpus: 'embeddings_ref',
    off_topic:    'relevant_files',
  };

  async function pollPlannerState(threadId) {
    // 2026-canonical pattern: Server-Sent Events instead of HTTP polling.
    // Backend pub/sub channel (Redis) is bridged by the FastAPI
    // /planner/{thread_id}/events endpoint which streams text/event-stream.
    // Each event carries {step, kind, ts, ...}; we route to the matching
    // substep card and render either a live progress sub-line or
    // (on "done") fetch the full state and let renderPlannerCards
    // redraw the card with KPI grids.
    //
    // Name kept for back-compat with existing callers (startPlanner).
    const url = API + '/planner/' + threadId + '/events';
    let es;
    try {
      es = new EventSource(url);
    } catch (e) {
      markPlannerFailed('EventSource open failed: ' + String(e));
      plannerThreadId = null;
      refreshPlannerStartState();
      return;
    }
    es.onmessage = async (msg) => {
      if (plannerThreadId !== threadId) {
        try { es.close(); } catch (_) {}
        return;
      }
      let ev;
      try { ev = JSON.parse(msg.data); } catch (_) { return; }
      _liveEventReceived = true;   // orphan-detect timer relies on this

      // Planner-level terminal event: end the stream + reset UI.
      if (ev.step === 'planner' && ev.kind === 'terminal') {
        // Pull the final state once so the cards reflect the very last
        // checkpoint. status field is set by aupdate_state right before
        // the terminal SSE event is emitted, so retry-by-status is the
        // race-safe expected field.
        await _refreshCardsFromState(threadId, 'status');
        const status = ev.status || 'done';
        if (status === 'failed') {
          markPlannerFailed(ev.error || 'Planner failed.');
        } else if (status === 'cancelled') {
          showToast('Planner cancelled. Checkpoints up to the cancel point are preserved.');
        }
        try { es.close(); } catch (_) {}
        plannerThreadId = null;
        // Intentionally NOT calling _forgetActivePlanner here — the
        // localStorage entry stays so a page refresh can still recover
        // the completed cards via the same thread_id. The entry only
        // clears on explicit Wipe Planner or on the next Start Planner
        // on this slug (which overwrites it).
        refreshPlannerStartState();
        return;
      }

      // Per-step lifecycle.
      if (ev.step) {
        if (ev.kind === 'start') {
          _markCardRunning(ev.step);
          // Previous step's checkpoint is necessarily committed by the
          // time the NEXT step starts (graph is sequential), so refresh
          // state to paint the previous card's full KPI grid. Skip for
          // the first step (no previous).
          const stepIdx = PLANNER_NODE_ORDER.indexOf(ev.step);
          if (stepIdx > 0) {
            const prevStep = PLANNER_NODE_ORDER[stepIdx - 1];
            const prevField = STEP_TO_FIELD[prevStep];
            await _refreshCardsFromState(threadId, prevField);
            // _markCardRunning was called BEFORE the state refresh; if
            // renderPlannerCards happens to have flipped this card back
            // to pending (because its field isn't in state yet), re-mark
            // it running here so the spinner stays correct.
            _markCardRunning(ev.step);
          }
        }
        _renderLiveProgress(ev.step, ev);
      }
    };
    es.onerror = (_e) => {
      // Browser auto-reconnects EventSource on transient errors; we
      // only intervene if the run was already torn down server-side.
      if (plannerThreadId !== threadId) {
        try { es.close(); } catch (_) {}
      }
    };
  }

  function _plannerStorageKey(slug) {
    return 'dd:planner:active:' + slug;
  }

  // Full planner wipe for `slug` — DELETE backend (MinIO embeddings +
  // Postgres LangGraph checkpoints) + clear localStorage + reset cards
  // if currently viewing that slug. Exposed on `window.ddWipePlanner`
  // so an operator can run `ddWipePlanner('pydantic')` from the
  // browser console without leaving the page.
  async function wipePlanner(slug) {
    if (!slug) return {error: 'no slug'};
    let result = {};
    try {
      const r = await fetch(API + '/planner/' + slug + '/wipe',
        {method: 'DELETE'});
      result = r.ok ? (await r.json()) : {http_status: r.status};
    } catch (e) {
      result = {error: String(e)};
    }
    _forgetActivePlanner(slug);
    if (activeSlug === slug) {
      plannerThreadId = null;
      resetPlannerCards();
      refreshPlannerStartState();
    }
    console.log('[ddWipePlanner]', slug, result);
    return result;
  }
  window.ddWipePlanner = wipePlanner;

  // Separate key tracking the LAST slug the user kicked off a planner
  // run for. recoverActivePlanner uses this to disambiguate when multiple
  // slugs have localStorage entries — without it, the JS scan order is
  // undefined and we might auto-activate the wrong framework on reload.
  const _LAST_PLANNER_SLUG_KEY = 'dd:planner:last_slug';

  function _rememberActivePlanner(slug, tid) {
    try {
      localStorage.setItem(_plannerStorageKey(slug), tid);
      localStorage.setItem(_LAST_PLANNER_SLUG_KEY, slug);
    } catch (e) { /* private mode etc — silently ignore */ }
  }

  function _forgetActivePlanner(slug) {
    try { localStorage.removeItem(_plannerStorageKey(slug)); }
    catch (e) { /* ignore */ }
  }

  // Page-refresh recovery: when the user reloads while a planner is
  // mid-run, reconnect to the SSE stream + replay snapshot events so the
  // UI catches up to the live state, mirroring the loading-box recovery
  // on the Ingestion step. After a pod restart the in-flight bg task is
  // dead but the LangGraph checkpoints persist — if no SSE events arrive
  // within _ORPHAN_DETECT_MS, we POST /resume which makes LangGraph
  // continue from the last committed checkpoint (completed nodes skipped).
  // Returns true if a run was resumed.
  const _ORPHAN_DETECT_MS = 6000;

  // Returns true if every CURRENTLY-IMPLEMENTED planner node has its
  // output field present in `values`. Lets us treat a stuck `status:
  // "running"` (e.g. pod-restart killed the bg task before
  // aupdate_state(status='done') ran) as effectively-terminal so we
  // don't burn orphan-detect timers + /resume calls on a run that
  // actually finished.
  function _allImplementedComplete(values) {
    if (!values) return false;
    if (!plannerImplemented || !plannerImplemented.size) return false;
    for (let i = 0; i < PLANNER_NODE_ORDER.length; i++) {
      const step = PLANNER_NODE_ORDER[i];
      if (!plannerImplemented.has(step)) continue;
      const field = PLANNER_SUBSTEP_FIELDS[i];
      if (!_fieldPresent(values, field)) return false;
    }
    return true;
  }

  async function _tryResumeActivePlanner(slug) {
    // Tear down any prior session FIRST so a switch from framework A
    // (which had cached planner state) to framework B doesn't leave
    // A's KPI grids on B's cards. plannerThreadId !== new tid implies
    // the previous SSE loop should self-exit on its next message
    // (see the guard inside pollPlannerState). We also reset the
    // visual state so a slug with no localStorage entry shows pending
    // cards instead of inheriting the previous slug's render.
    plannerThreadId = null;
    resetPlannerCards();
    refreshPlannerStartState();

    let tid = null;
    try { tid = localStorage.getItem(_plannerStorageKey(slug)); }
    catch (e) { return false; }
    if (!tid) return false;
    try {
      const r = await fetch(API + '/planner/debug/graph/' + tid + '/state');
      if (!r.ok) {
        _forgetActivePlanner(slug);
        return false;
      }
      const data = await r.json();
      const values = data.values || {};
      const status = values.status;
      const effectivelyDone = (
        status === 'done' || status === 'failed' || status === 'cancelled' ||
        _allImplementedComplete(values)
      );
      if (effectivelyDone) {
        // Terminal (or all-impl-done) — paint final state, don't subscribe.
        // KEEP localStorage entry so subsequent page refreshes can still
        // recover the cached cards. Entry only clears on explicit
        // Wipe Planner OR when a new run on this slug overwrites it.
        renderPlannerCards(values);
        return false;
      }
      // Still "running" — paint what we have so far + reconnect to SSE.
      // If no events arrive within _ORPHAN_DETECT_MS, the bg task was
      // killed by a pod restart; POST /resume to make LangGraph pick
      // up from the last checkpoint.
      plannerThreadId = tid;
      refreshPlannerStartState();
      renderPlannerCards(values);
      _liveEventReceived = false;
      pollPlannerState(tid);
      setTimeout(async () => {
        if (plannerThreadId === tid && !_liveEventReceived) {
          try {
            await fetch(API + '/planner/' + tid + '/resume', {method: 'POST'});
          } catch (e) { /* leave the cards in their current state */ }
        }
      }, _ORPHAN_DETECT_MS);
      return true;
    } catch (e) {
      _forgetActivePlanner(slug);
      return false;
    }
  }

  async function startPlanner() {
    if (!activeSlug || plannerThreadId) return;
    resetPlannerCards();
    // Generate thread_id client-side so the Cancel button + polling
    // loop both have a real ID from click 1 (no 'pending' dead-zone).
    const tid = _genPlannerThreadId(activeSlug);
    plannerThreadId = tid;
    _rememberActivePlanner(activeSlug, tid);   // page-refresh recovery
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
      // POST now returns immediately with status="running" — the
      // background graph task runs server-side and the polling loop
      // (pollPlannerState above) owns terminal-state detection +
      // resetting plannerThreadId / the button. Nothing to do here.
      await r.json();   // drain the body
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

  // Wipe-planner button — destructive, gated by a confirm dialog. Hits
  // the backend DELETE /planner/{slug}/wipe (MinIO embeddings + Postgres
  // checkpoints) then clears localStorage + resets cards.
  if (plannerWipeBtn) {
    plannerWipeBtn.addEventListener('click', async () => {
      if (!activeSlug || plannerThreadId) return;
      const ok = await showConfirm(
        'Wipe planner cache for ' + activeSlug + '?',
        'Deletes MinIO embedding blobs (forces a cold re-embed next ' +
        'run), Postgres LangGraph checkpoints (all threads for this ' +
        'slug), and the browser-cached thread_id. Cannot be undone.',
        'Wipe',
      );
      if (!ok) return;
      plannerWipeBtn.setAttribute('disabled', 'disabled');
      const orig = plannerWipeBtn.textContent;
      plannerWipeBtn.textContent = 'Wiping…';
      try {
        const result = await wipePlanner(activeSlug);
        const minio = (result && result.minio_blobs_deleted) || 0;
        const pg = result && result.postgres_rows_deleted;
        const pgTotal = pg
          ? Object.values(pg).reduce(
              (a, b) => a + (typeof b === 'number' ? b : 0), 0)
          : 0;
        showToast('Planner cache wiped for ' + activeSlug +
          ' (' + minio + ' MinIO blobs, ' + pgTotal + ' Postgres rows).');
      } catch (e) {
        showToast('Wipe failed: ' + String(e));
      } finally {
        plannerWipeBtn.textContent = orig;
        refreshPlannerStartState();
      }
    });
  }

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

  // Page-load auto-resume for planner runs. Mirrors recoverActiveRuns
  // (ingestion side) but driven by localStorage instead of a backend
  // active-runs endpoint, because the planner's active thread_id is
  // generated client-side. Activates the most recent slug with a
  // surviving /state so a plain page reload (no framework click)
  // restores the cached substep cards.
  async function recoverActivePlanner() {
    if (activeSlug) return;  // ingestion recovery already took over

    // Collect all planner localStorage entries, then SORT by preference:
    // (1) the last-active slug first, (2) the rest alphabetically. This
    // makes the auto-activation predictable on browsers with multiple
    // cached slugs (otherwise the JS Object key order would pick at
    // random and could land on the wrong framework).
    let lastSlug = null;
    try { lastSlug = localStorage.getItem(_LAST_PLANNER_SLUG_KEY); }
    catch (e) {}

    const keys = [];
    try {
      for (let i = 0; i < localStorage.length; i++) {
        const k = localStorage.key(i);
        if (k && k.startsWith('dd:planner:active:')) keys.push(k);
      }
    } catch (e) { return; }
    if (!keys.length) {
      // Brave / Safari / private mode sometimes wipe localStorage. Fall
      // back to the server-side discovery endpoint that lists the most
      // recent thread per slug (queried straight from Postgres). This
      // path doesn't depend on any client-side state at all.
      // Brave / Safari / private mode sometimes wipe localStorage. Fall
      // back to the server-side discovery endpoint that lists the most
      // recent thread per slug (queried straight from Postgres).
      try {
        const r = await fetch(API + '/planner/recent');
        if (r.ok) {
          const data = await r.json();
          const recent = (data && data.recent) || [];
          if (recent.length) {
            for (const item of recent) {
              try {
                localStorage.setItem(
                  _plannerStorageKey(item.slug), item.thread_id,
                );
              } catch (e) {}
            }
            try {
              localStorage.setItem(_LAST_PLANNER_SLUG_KEY, recent[0].slug);
            } catch (e) {}
            return await recoverActivePlanner();
          }
        }
      } catch (e) {
        console.warn('[planner-recover] /planner/recent failed:', e);
      }
      return;
    }
    keys.sort((a, b) => {
      const slugA = a.slice('dd:planner:active:'.length);
      const slugB = b.slice('dd:planner:active:'.length);
      if (slugA === lastSlug) return -1;
      if (slugB === lastSlug) return 1;
      return slugA.localeCompare(slugB);
    });
    console.log('[planner-recover] candidates (priority order):',
      keys.map(k => k.slice('dd:planner:active:'.length)));

    const probeResults = [];
    for (const k of keys) {
      const slug = k.slice('dd:planner:active:'.length);
      let tid;
      try { tid = localStorage.getItem(k); } catch (e) { continue; }
      if (!tid) continue;
      try {
        const r = await fetch(API + '/planner/debug/graph/' + tid + '/state');
        if (!r.ok) {
          console.log('[planner-recover]', slug, 'HTTP', r.status);
          probeResults.push(slug + '=' + r.status);
          try { localStorage.removeItem(k); } catch (e) {}
          continue;
        }
        // Sanity check: state must have at least one known node field
        // before we activate. An empty state means the thread row exists
        // but no node ran (or the thread is from a different schema).
        const data = await r.json();
        const values = data.values || {};
        const haveAnyField = PLANNER_SUBSTEP_FIELDS.some(f =>
          _fieldPresent(values, f));
        if (!haveAnyField) {
          console.log('[planner-recover]', slug, 'state empty, skipping');
          probeResults.push(slug + '=empty');
          continue;
        }
        console.log('[planner-recover] activating', slug, 'thread=' + tid);
        await loadManifestForSlug(slug);
        farthestStep = Math.max(farthestStep, 3);
        showStep(3);
        return;
      } catch (e) {
        console.log('[planner-recover]', slug, 'fetch failed:', e);
        probeResults.push(slug + '=err');
      }
    }
    console.log('[planner-recover] no candidate had valid /state:',
      probeResults);
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
  // Sequence init steps WITHOUT chaining — if one fails the next still
  // runs. Each step's exception (if any) is logged to console only;
  // the user-visible recovery outcome lives on the planner cards.
  (async () => {
    try { await loadLibrary(); }
    catch (e) { console.warn('[init] library failed:', e); }
    try { await recoverActiveRuns(); }
    catch (e) { console.warn('[init] ingestion-recover failed:', e); }
    try { await loadPlannerInfo(); }
    catch (e) { console.warn('[init] planner-info failed:', e); }
    try { await recoverActivePlanner(); }
    catch (e) { console.warn('[init] planner-recover failed:', e); }
  })();
})();
