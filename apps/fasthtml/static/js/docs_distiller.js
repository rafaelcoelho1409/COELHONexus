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
  const plannerCardsEl    = document.querySelector('#fw-planner-cards');
  // plannerProgressLbl removed 2026-05-18 — the "Step N of 8" counter
  // moved into the status pill (`WORKING · N/8`) for less header noise.
  const plannerFwLogosEl  = document.querySelector('#fw-planner-fw-logos');
  const plannerFwNameEl   = document.querySelector('#fw-planner-fw-name');

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
  // off_topic verdict-table sort state (column + direction). Survives
  // re-renders so SSE refreshes preserve the operator's current sort.
  let _offTopicSort = {col: null, dir: 'asc'};
  // Latest off_topic state values cached at render time so a sort-header
  // click can re-render the card without refetching /state.
  let _lastOffTopicValues = null;
  let plannerPollAbort = false;
  // Substep order MUST match `NODE_ORDER` in
  // services/docs_distiller/planner/graph.py AND the field each node
  // writes (`state.<field>`).
  const PLANNER_SUBSTEP_FIELDS = [
    'raw_files',                // corpus_load
    'embeddings_ref',           // embed_corpus
    'relevant_files',           // off_topic
    'cluster_assignments_ref',  // cluster
    'refine_assignments_ref',   // refine
    'cluster_labels_ref',       // label
    'chapter_plan_ref',         // reduce
    'plan_path',                // plan_write
  ];
  // Parallel to PLANNER_SUBSTEP_FIELDS — the node name (matches the
  // server-side step name in SSE events). Used by the SSE handler to
  // map step → previous step → expected checkpoint field.
  const PLANNER_NODE_ORDER = [
    'corpus_load', 'embed_corpus', 'off_topic',
    'cluster', 'refine', 'label',
    'reduce', 'plan_write',
  ];
  // Short labels for the graph canvas (same text as the card titles —
  // hardcoded here to keep StageGraph independent of DOM-card scraping).
  const PLANNER_NODE_LABELS = [
    'Corpus load', 'Embed corpus', 'Off-topic filter',
    'Cluster', 'Refine (LITA)', 'Label',
    'Reduce (outline)', 'Plan write',
  ];
  // Populated from GET /planner/info — names of substeps actually wired
  // into the runtime graph. Stubs aren't included; their cards render
  // as "future" so the user doesn't expect them to advance.
  let plannerImplemented = new Set();

  // ============================================================
  // StageGraph — Cytoscape DAG canvas per LangGraph stage
  // (Planner / Synth / Curator / Critic / Assembler).
  //
  // Activates only when `?ui=graph` is on the URL (Day 1 of the
  // canvas-redesign sprint — `docs/UI-ARCHITECTURE-SOTA-2026-05-18.md`).
  // The legacy vertical-cards layout stays the default until Day 5
  // when the flag flips and the cards code is deleted.
  //
  // Public API (kept tiny so reuse across stages is mechanical):
  //   const g = StageGraph.create(containerEl, {
  //     nodes:       [{id, label, status, kpi?}, ...],   // status: pending|running|done|failed|future
  //     edges:       [{source, target}, ...],
  //     onNodeClick: (nodeId) => { ... },
  //   });
  //   g.setStatus(nodeId, status, kpiText?)
  //   g.reset()                       // back to initial nodes' statuses
  //   g.cy                            // the underlying Cytoscape instance
  // ============================================================
  const UI_MODE = (function() {
    try {
      const p = new URLSearchParams(window.location.search);
      const v = (p.get('ui') || '').toLowerCase();
      if (v === 'graph') return 'graph';
      return 'cards';
    } catch (e) { return 'cards'; }
  })();

  const StageGraph = (function() {
    // Visual spec lives in the Cytoscape `style` array (Cytoscape uses
    // its own selector engine, not CSS, for node/edge appearance).
    // App-level CSS handles the container + status pill + drawer.
    function _cyStyles() {
      return [
        {
          selector: 'node',
          style: {
            'shape':            'round-rectangle',
            'width':            200,
            'height':           60,
            'background-color': '#ffffff',
            'border-width':     1,
            'border-color':     '#e5e5e5',
            // Function-mapped label so KPI shows as a second line
            // when `data(kpi)` is set. Single line otherwise.
            'label': function(ele) {
              const base = ele.data('label') || '';
              const kpi = ele.data('kpi') || '';
              return kpi ? (base + '\n' + kpi) : base;
            },
            'color':            '#2a2a2a',
            'text-valign':      'center',
            'text-halign':      'center',
            'font-family':      'Raleway, Helvetica Neue, Arial, sans-serif',
            'font-size':        14,
            'font-weight':      500,
            'text-wrap':        'wrap',
            'text-max-width':   '180px',
            'line-height':      1.18,
            'min-zoomed-font-size': 8,
            'transition-property':       'border-color border-width opacity background-color',
            'transition-duration':       '180ms',
          },
        },
        {
          selector: "node[status = 'future']",
          style: {
            'opacity':           0.55,
            'background-color': '#f5f5f5',
            'border-color':     '#cccccc',
            'color':            '#999999',
          },
        },
        {
          selector: "node[status = 'pending']",
          style: {
            'opacity':           1,
            'background-color': '#ffffff',
            'border-color':     '#e5e5e5',
            'color':            '#2a2a2a',
          },
        },
        {
          selector: "node[status = 'running']",
          style: {
            // Sky-blue fill — visually distinct from burgundy (primary)
            // AND from red (failed). SOTA convention for "actively
            // processing" across Linear/GitHub Actions/Dagster/LangSmith.
            // Matches CSS var --running-bg.
            'background-color': '#e0f2fe',
            'border-color':     '#0369a1',
            'border-width':     2,
            'color':            '#0c4a6e',
          },
        },
        {
          selector: "node[status = 'done']",
          style: {
            // Pastel green fill — same shade family as the done icon
            // color (#2a8b46). Dark text + green border keep the
            // "completed" read clean.
            'background-color': '#e5f4e9',
            'border-color':     '#2a8b46',
            'border-width':     2,
            'color':            '#1a3a23',
          },
        },
        {
          selector: "node[status = 'failed']",
          style: {
            // Matches --error-bg / --error-border / --error-text from
            // the app's CSS vars — single source of truth for "this
            // failed" across cards and canvas.
            'background-color': '#fde7e9',
            'border-color':     '#e8a3aa',
            'border-width':     3,
            'color':            '#7a2228',
          },
        },
        {
          selector: 'edge',
          style: {
            'width':              1,
            'line-color':         '#cccccc',
            'curve-style':        'bezier',
            'target-arrow-shape': 'triangle',
            'target-arrow-color': '#cccccc',
            'arrow-scale':        0.7,
          },
        },
        {
          selector: "edge[active = 'true']",
          style: {
            // Active edge matches the running node color (sky blue) so
            // the "flow" reads as continuous from upstream to running.
            'line-color':         '#0369a1',
            'target-arrow-color': '#0369a1',
            'width':              2,
            'line-style':         'dashed',
            'line-dash-pattern':  [6, 4],
            // line-dash-offset is animated by the marching-ants timer
            // attached at create() time (Cytoscape's canvas renderer
            // doesn't honor CSS @keyframes, so JS drives the offset).
            'line-dash-offset':   0,
          },
        },
      ];
    }

    function create(containerEl, options) {
      if (!containerEl) {
        console.warn('[StageGraph] no container element');
        return null;
      }
      if (typeof cytoscape === 'undefined') {
        console.warn('[StageGraph] Cytoscape not loaded — canvas disabled');
        return null;
      }
      const { nodes = [], edges = [], onNodeClick } = options || {};
      const elements = [
        ...nodes.map(n => ({
          data: {
            id:     n.id,
            label:  n.label || n.id,
            status: n.status || 'future',
            kpi:    n.kpi || '',
          },
        })),
        ...edges.map(e => ({
          data: {
            id:     `${e.source}__${e.target}`,
            source: e.source,
            target: e.target,
            active: 'false',
          },
        })),
      ];
      // Dagre is registered as a Cytoscape extension when its bundle
      // loads; we fall back to breadthfirst if it didn't load (e.g.,
      // CDN blocked) so the graph always renders SOMETHING.
      const hasDagre = (
        typeof cytoscape.use === 'function' &&
        typeof window.cytoscapeDagre !== 'undefined'
      );
      if (hasDagre && !cytoscape._dagreRegistered) {
        cytoscape.use(window.cytoscapeDagre);
        cytoscape._dagreRegistered = true;
      }
      const layoutConfig = hasDagre
        ? { name: 'dagre', rankDir: 'TB', nodeSep: 36, rankSep: 56,
            padding: 32, animate: false, fit: false }
        : { name: 'breadthfirst', directed: true, padding: 32,
            spacingFactor: 1.4, animate: false, grid: false, fit: false };
      const cy = cytoscape({
        container:              containerEl,
        elements,
        style:                  _cyStyles(),
        layout:                 layoutConfig,
        minZoom:                0.6,
        maxZoom:                1.8,
        userZoomingEnabled:     true,    // user can zoom; default still 1.0
        userPanningEnabled:     true,    // and pan if the graph overflows
        boxSelectionEnabled:    false,
        autounselectify:        true,
        wheelSensitivity:       0.15,
      });
      if (typeof onNodeClick === 'function') {
        cy.on('tap', 'node', evt => onNodeClick(evt.target.data('id')));
      }
      // Initial nodes' status as remembered for reset().
      const _initial = {};
      cy.nodes().forEach(n => { _initial[n.id()] = n.data('status'); });
      // Marching-ants animation on active edges — drives the
      // line-dash-offset since Cytoscape's canvas renderer doesn't
      // honor CSS @keyframes. Timer runs continuously at low cost
      // (~16fps); style updates are no-ops when no edge is active.
      const _antsInterval = setInterval(() => {
        const active = cy.edges('[active = "true"]');
        if (active.length === 0) return;
        const next = ((_antsInterval._offset || 0) + 1) % 10;
        _antsInterval._offset = next;
        active.style('line-dash-offset', next);
      }, 60);
      return {
        cy,
        setStatus(nodeId, status, kpiText) {
          const node = cy.getElementById(nodeId);
          if (node.length === 0) return;
          node.data('status', status);
          if (kpiText !== undefined) node.data('kpi', kpiText);
          // Mark the incoming edge `active` while this node is running;
          // clear all edges when transitioning out of running.
          if (status === 'running') {
            cy.edges().forEach(e => {
              e.data('active', e.target().id() === nodeId ? 'true' : 'false');
            });
          } else if (status === 'done' || status === 'failed' ||
                     status === 'pending' || status === 'future') {
            cy.edges('[active = "true"]').forEach(e => {
              if (e.target().id() === nodeId) e.data('active', 'false');
            });
          }
        },
        reset() {
          cy.nodes().forEach(n => {
            n.data('status', _initial[n.id()] || 'pending');
            n.data('kpi', '');
          });
          cy.edges().forEach(e => e.data('active', 'false'));
        },
        destroy() {
          // Stop the marching-ants timer and tear down Cytoscape.
          // Currently only called by tests; kept for API completeness.
          clearInterval(_antsInterval);
          try { cy.destroy(); } catch (e) {}
        },
      };
    }
    return { create };
  })();

  // Module-scoped planner graph instance — populated by initPlannerCanvas
  // once Cytoscape has loaded. null when ?ui=cards (the default).
  let plannerGraph = null;

  // Tell Cytoscape to recompute its drawing area + re-fit nodes after
  // the Planner panel transitions from display:none to display:block.
  // Cytoscape latches container dimensions at init time; without
  // `resize()` the graph stays invisible (0x0 viewport) even after
  // the panel becomes active. Called from showStep(3) below, from
  // initPlannerCanvas (if Step 3 is already active at page load),
  // and from a window resize listener.
  function _resizePlannerCanvas() {
    if (!plannerGraph || !plannerGraph.cy) return;
    // requestAnimationFrame defers to the next paint — the CSS panel
    // transition (display:block) needs one frame to apply non-zero
    // dimensions before Cytoscape measures them. Without the rAF,
    // resize() reads stale 0x0 bounds and the graph stays hidden.
    requestAnimationFrame(() => {
      _runPlannerLayoutAndCenter('first');
      // Second-pass after a longer delay — handles the case where the
      // container's final size is only known after CSS transitions /
      // flex reflows complete. Without this the graph latches the
      // canvas's transient pre-reflow width.
      setTimeout(() => _runPlannerLayoutAndCenter('second'), 250);
    });
  }

  function _runPlannerLayoutAndCenter(passLabel) {
    if (!plannerGraph || !plannerGraph.cy) return;
    try {
      const cy = plannerGraph.cy;
      cy.resize();
      const hasDagre = !!cytoscape._dagreRegistered;
      const layout = cy.layout(hasDagre
        ? { name: 'dagre', rankDir: 'TB', nodeSep: 36, rankSep: 56,
            padding: 32, animate: false, fit: false }
        : { name: 'breadthfirst', directed: true, padding: 32,
            spacingFactor: 1.4, animate: false, fit: false }
      );
      layout.one('layoutstop', () => {
        try {
          cy.fit(cy.elements(), 32);
          cy.center(cy.elements());
          _forceCenterHorizontal(cy, '[plannerGraph ' + passLabel + ']');
        } catch (e) {
          console.warn('[plannerGraph] center pipeline failed:', e);
        }
      });
      layout.run();
    } catch (e) {
      console.warn('[plannerGraph] resize ' + passLabel + ' failed:', e);
    }
  }

  // Brute-force horizontal recentering with detailed logging so we can
  // SEE what Cytoscape thinks the dimensions are. The empty catch
  // blocks in earlier versions silently swallowed the actual problem.
  function _forceCenterHorizontal(cy, tag) {
    tag = tag || '[graph]';
    const containerW = cy.width();
    const containerH = cy.height();
    const bb = cy.elements().renderedBoundingBox();
    const pan = cy.pan();
    const zoom = cy.zoom();
    console.log(
      tag + ' centering: containerW=' + containerW +
      ' containerH=' + containerH +
      ' zoom=' + zoom.toFixed(3) +
      ' pan=(' + pan.x.toFixed(1) + ',' + pan.y.toFixed(1) + ')' +
      ' bb={x1=' + (bb ? bb.x1.toFixed(1) : '?') +
      ' x2=' + (bb ? bb.x2.toFixed(1) : '?') +
      ' w=' + (bb ? bb.w.toFixed(1) : '?') + '}'
    );
    if (!containerW || !bb || !bb.w) {
      console.warn(tag + ' centering ABORTED — bad dims');
      return;
    }
    const graphMidX = bb.x1 + bb.w / 2;
    const containerMidX = containerW / 2;
    const dx = containerMidX - graphMidX;
    const graphMidY = bb.y1 + bb.h / 2;
    const containerMidY = containerH / 2;
    const dy = containerMidY - graphMidY;
    console.log(
      tag + ' delta: dx=' + dx.toFixed(1) + ' dy=' + dy.toFixed(1)
    );
    if (Math.abs(dx) > 0.5 || Math.abs(dy) > 0.5) {
      cy.panBy({ x: dx, y: dy });
      const newPan = cy.pan();
      console.log(
        tag + ' panned to (' + newPan.x.toFixed(1) + ',' +
        newPan.y.toFixed(1) + ')'
      );
    }
  }
  // Defensive: re-fit on window resize so the canvas stays responsive.
  // Throttle to one rAF per resize burst.
  let _resizeRafPending = false;
  window.addEventListener('resize', () => {
    if (_resizeRafPending) return;
    _resizeRafPending = true;
    requestAnimationFrame(() => {
      _resizeRafPending = false;
      if (plannerGraph) _resizePlannerCanvas();
    });
  });
  // ResizeObserver — catches container size changes from sources other
  // than window resize (CSS transitions, flex reflows, sidebar
  // collapses). Critical for the left-clipping bug: the canvas's
  // post-display:flex final width can land 100+ ms after the initial
  // mount, and Cytoscape latches the transient pre-reflow value.
  function _attachCanvasResizeObserver(containerId, resizeFn) {
    if (typeof ResizeObserver === 'undefined') return;
    const el = document.getElementById(containerId);
    if (!el) return;
    let lastW = 0;
    let debounce = null;
    const ro = new ResizeObserver(entries => {
      for (const e of entries) {
        const w = Math.round(e.contentRect.width);
        if (w === lastW || w === 0) continue;
        lastW = w;
        console.log('[' + containerId + '] container resized to', w);
        if (debounce) clearTimeout(debounce);
        debounce = setTimeout(() => { debounce = null; resizeFn(); }, 80);
      }
    });
    ro.observe(el);
  }

  // ============================================================
  // Day 2 — Stage pill + graph state mirror (SSE → canvas wiring)
  //
  // Top-of-stage pill summarizes the WHOLE pipeline at a glance
  // (idle / working / done / failed). Driven by the same SSE events
  // that flip per-node statuses. CSS handles the visual via
  // `[data-status]` attribute selectors on `.fw-stage-pill`.
  // ============================================================
  function _setPlannerStagePill(status, labelOverride) {
    const pill = document.getElementById('fw-planner-pill');
    const text = document.getElementById('fw-planner-pill-text');
    if (!pill || !text) return;
    const labels = {
      idle:     'Idle',
      working:  'Working',
      done:     'Completed',
      failed:   'Failed',
      cancelled:'Cancelled',
    };
    pill.dataset.status = status;
    text.textContent = labelOverride || labels[status] || status;
  }

  // Per-node KPI badge — ONE number shown as a small second-line under
  // the label. Source is the per-node `*_stats` dict in state values
  // (same dicts the cards use for their KPI grids). Returns '' when
  // the node hasn't run yet.
  function _kpiForNode(nodeId, values) {
    if (!values) return '';
    const stats = (key) => values[key] || null;
    switch (nodeId) {
      case 'corpus_load': {
        const s = stats('corpus_stats');
        return s && s.files ? `n=${s.files}` : '';
      }
      case 'embed_corpus': {
        const s = stats('embed_stats');
        if (!s) return '';
        if (s.dim) return `dim=${s.dim}`;
        if (s.files) return `n=${s.files}`;
        return '';
      }
      case 'off_topic': {
        const s = stats('off_topic_stats');
        return s && (s.kept !== undefined)
          ? `kept=${s.kept}/${(s.kept + (s.dropped || 0))}` : '';
      }
      case 'cluster': {
        const s = stats('cluster_stats');
        return s && (s.n_clusters !== undefined)
          ? `k=${s.n_clusters}` : '';
      }
      case 'refine': {
        const s = stats('refine_stats');
        return s && (s.n_changed !== undefined)
          ? `reassigned=${s.n_changed}` : '';
      }
      case 'label': {
        const s = stats('label_stats');
        return s && (s.n_clusters !== undefined)
          ? `k=${s.n_clusters}` : '';
      }
      case 'reduce': {
        const s = stats('reduce_stats');
        return s && (s.n_chapters !== undefined)
          ? `ch=${s.n_chapters}` : '';
      }
      case 'plan_write': {
        const s = stats('plan_write_stats');
        return s && (s.n_chapters !== undefined)
          ? `ch=${s.n_chapters}` : '';
      }
    }
    return '';
  }

  // Mirror of renderPlannerCards for the Cytoscape canvas. Loops the
  // canonical node order, derives status per node from state field
  // presence (same logic as the cards path), and pushes to
  // plannerGraph.setStatus. No-op when the canvas isn't mounted
  // (?ui=cards) — keeps the call sites uniform.
  function _renderPlannerGraph(values) {
    if (!plannerGraph) return;
    let doneCount = 0;
    let anyRunning = false;
    let anyFailed = false;
    for (let i = 0; i < PLANNER_NODE_ORDER.length; i++) {
      const nodeId = PLANNER_NODE_ORDER[i];
      const field = PLANNER_SUBSTEP_FIELDS[i];
      const present = _fieldPresent(values, field);
      const isImpl = plannerImplemented.has(nodeId);
      let status;
      if (present) {
        status = 'done';
        doneCount++;
      } else if (!isImpl) {
        status = 'future';
      } else if (i === doneCount && plannerThreadId !== null) {
        status = 'running';
        anyRunning = true;
      } else {
        status = 'pending';
      }
      const kpi = present ? _kpiForNode(nodeId, values) : '';
      plannerGraph.setStatus(nodeId, status, kpi);
    }
    // Derive stage pill from aggregate state. Failed has priority,
    // then running, then all-done, else idle. The terminal SSE
    // handler overrides this with explicit done/failed/cancelled.
    // Progress count (N/8) is folded INTO the pill while working —
    // replaces the separate "Step N of 8" label that used to live in
    // the header actions cluster.
    const explicitStatus = (values && values.status) || null;
    const implCount = PLANNER_NODE_ORDER.filter(n => plannerImplemented.has(n)).length;
    const progress = implCount ? doneCount + '/' + implCount : null;
    if (explicitStatus === 'failed') {
      _setPlannerStagePill('failed');
      anyFailed = true;
    } else if (explicitStatus === 'cancelled') {
      _setPlannerStagePill('cancelled');
    } else if (anyRunning || plannerThreadId !== null) {
      _setPlannerStagePill('working',
        progress ? 'Working · ' + progress : null);
    } else if (
      doneCount > 0 && doneCount === implCount
    ) {
      _setPlannerStagePill('done');
    } else if (doneCount === 0) {
      _setPlannerStagePill('idle');
    }
    return { doneCount, anyRunning, anyFailed };
  }

  // Build the drawer context object for a planner node from the
  // current checkpoint state. Separate from `open()` so live state
  // refreshes can reuse the same logic via `_refreshOpenPlannerDrawer`.
  function _buildPlannerNodeCtx(nodeId, values) {
    const idx = PLANNER_NODE_ORDER.indexOf(nodeId);
    if (idx < 0) return null;
    const label = PLANNER_NODE_LABELS[idx] || nodeId;
    const thisField = PLANNER_SUBSTEP_FIELDS[idx];
    let status = 'pending';
    if (_fieldPresent(values, thisField)) status = 'done';
    else if (!plannerImplemented.has(nodeId)) status = 'future';
    else if (plannerThreadId) status = 'running';
    // KPI strip for the sticky header — same compact format as the
    // node-label KPI badge but split into key/value chips.
    const kpiText = _kpiForNode(nodeId, values);
    const kpis = {};
    if (kpiText) {
      const eqIdx = kpiText.indexOf('=');
      if (eqIdx > 0) kpis[kpiText.slice(0, eqIdx)] = kpiText.slice(eqIdx + 1);
    }
    // PRIMARY content — the SAME rich HTML the legacy card body
    // showed. Custom per-substep renderer if this node has produced
    // output; otherwise the drawer renders a status-aware placeholder.
    const renderer = SUBSTEP_RENDERERS[idx];
    const resultsHtml = (renderer && _fieldPresent(values, thisField))
      ? renderer(values)
      : null;
    // Raw JSON kept as collapsed debug aids (only when present).
    const inputs = idx > 0 && _fieldPresent(values, PLANNER_SUBSTEP_FIELDS[idx - 1])
      ? JSON.stringify({ [PLANNER_SUBSTEP_FIELDS[idx - 1]]: values[PLANNER_SUBSTEP_FIELDS[idx - 1]] }, null, 2)
      : null;
    const outputs = _fieldPresent(values, thisField)
      ? JSON.stringify({ [thisField]: values[thisField] }, null, 2)
      : null;
    return { label, status, kpis, resultsHtml, inputs, outputs };
  }

  // Opens the NodeDrawer for a planner node. Fetches fresh state for
  // an accurate initial render; subsequent updates flow in via the
  // SSE handler + _refreshOpenPlannerDrawer.
  async function _openPlannerNodeDrawer(nodeId) {
    let values = {};
    // plannerThreadId is set ONLY while a run is in flight — terminal
    // SSE handler nulls it on done/failed/cancelled. For a completed
    // thread we need the localStorage entry (same fallback the page-
    // refresh recovery uses) so the drawer can fetch /state and the
    // renderer can show the rich card body content.
    let tid = plannerThreadId;
    if (!tid && activeSlug) {
      try { tid = localStorage.getItem(_plannerStorageKey(activeSlug)); }
      catch (e) {}
    }
    if (tid) {
      try {
        const r = await fetch(API + '/planner/debug/graph/' + tid + '/state');
        if (r.ok) values = (await r.json()).values || {};
      } catch (e) { /* drawer opens with empty results */ }
    }
    const ctx = _buildPlannerNodeCtx(nodeId, values);
    if (ctx) NodeDrawer.open('planner', nodeId, ctx);
  }

  // Called from renderPlannerCards on every state refresh so the
  // open drawer's results panel updates as the pipeline progresses
  // (e.g. cluster card's KPI grid materializes the moment `cluster`
  // commits its checkpoint, without the user having to re-click).
  function _refreshOpenPlannerDrawer(values) {
    if (NodeDrawer.openStage !== 'planner') return;
    const nodeId = NodeDrawer.openNodeId;
    if (!nodeId) return;
    const ctx = _buildPlannerNodeCtx(nodeId, values);
    if (ctx) NodeDrawer.updateContext(ctx);
  }

  // ============================================================
  // NodeDrawer — right-side drawer showing a single graph node's
  // live activity (Day 3 of UI-redesign sprint). Opens when a user
  // clicks a node on the planner/synth canvas; subscribes to the SSE
  // event stream for that node and streams events into a sticky-
  // bottom log with rAF batching + 200-line cap.
  //
  // Public API:
  //   NodeDrawer.open(stage, nodeId, ctx)  // ctx = {label, kpis, status, prompt?, inputs?, outputs?}
  //   NodeDrawer.close()
  //   NodeDrawer.isOpenFor(stage, nodeId)
  //   NodeDrawer.appendEvent(ev)           // route an SSE event to the log + status
  //   NodeDrawer.updateContext(ctx)        // refresh static sections (inputs/outputs)
  // ============================================================
  const NodeDrawer = (function() {
    const elDrawer    = document.getElementById('fw-node-drawer');
    const elIcon      = document.getElementById('fw-node-drawer-icon');
    const elTitle     = document.getElementById('fw-node-drawer-title');
    const elMeta      = document.getElementById('fw-node-drawer-meta');
    const elKpis      = document.getElementById('fw-node-drawer-kpis');
    const elLog       = document.getElementById('fw-node-drawer-log');
    const elLogEmpty  = document.getElementById('fw-node-drawer-log-empty');
    const elDetails   = document.getElementById('fw-node-drawer-details');
    const elClose     = document.getElementById('fw-node-drawer-close');

    const MAX_LOG_LINES = 200;
    const STATUS_ICON = {
      future: '⏳', pending: '○', running: '◐',
      done: '●', failed: '✕', cancelled: '∅',
    };

    let _openStage = null;        // 'planner' | 'synth' | null
    let _openNodeId = null;
    let _pendingEvents = [];
    let _flushScheduled = false;
    let _userPinnedScroll = true; // true = auto-scroll to bottom; false = user scrolled up
    // "Since last viewed" tracking: maps `${stage}/${nodeId}` → epoch ms
    // of last drawer-open for that node. Events whose timestamp is
    // newer than the previous lastSeen get an `.is-new` highlight.
    // Per-session (not persisted) — chat-style affordance.
    const _lastSeenAt = new Map();
    let _prevSeenForOpen = 0;     // captured at open(); 0 = first open ever

    function _fmtTs(ts) {
      const d = typeof ts === 'number' ? new Date(ts * 1000) : new Date();
      const h = String(d.getHours()).padStart(2, '0');
      const m = String(d.getMinutes()).padStart(2, '0');
      const s = String(d.getSeconds()).padStart(2, '0');
      return `${h}:${m}:${s}`;
    }

    function _makeLogLine(ev) {
      const div = document.createElement('div');
      div.className = 'fw-node-drawer-log-line';
      const kind = (ev && ev.kind) || 'info';
      div.dataset.kind = kind;
      // Severity coloring: errors burgundy, warnings amber, info gray.
      if (kind === 'error' || ev.error) div.classList.add('severity-error');
      else if (kind === 'warning')      div.classList.add('severity-warn');
      // "Since last viewed" highlight — events newer than the previous
      // drawer-open get a subtle left-border accent. Only after first
      // open (_prevSeenForOpen > 0); on a node's first-ever open every
      // event would be "new" which carries no signal.
      const evTsMs = (typeof ev.ts === 'number') ? ev.ts * 1000 : Date.now();
      if (_prevSeenForOpen > 0 && evTsMs > _prevSeenForOpen) {
        div.classList.add('is-new');
      }
      // Extract a tidy event payload (drop noisy fields).
      const tidy = {};
      Object.keys(ev || {}).forEach(k => {
        if (k === 'ts' || k === 'step' || k === 'kind') return;
        tidy[k] = ev[k];
      });
      const tidyStr = Object.keys(tidy).length
        ? ' ' + Object.entries(tidy)
            .map(([k, v]) => `${k}=${typeof v === 'object'
              ? JSON.stringify(v).slice(0, 60) : String(v).slice(0, 60)}`)
            .join(' ')
        : '';
      div.textContent = `▸ ${_fmtTs(ev.ts)} ${kind}${tidyStr}`;
      return div;
    }

    function _scheduleFlush() {
      if (_flushScheduled) return;
      _flushScheduled = true;
      requestAnimationFrame(() => {
        _flushScheduled = false;
        if (_pendingEvents.length === 0 || !elLog) return;
        // Hide the empty-state placeholder on first line.
        if (elLogEmpty) elLogEmpty.style.display = 'none';
        const frag = document.createDocumentFragment();
        _pendingEvents.forEach(ev => frag.appendChild(_makeLogLine(ev)));
        elLog.appendChild(frag);
        _pendingEvents = [];
        // Cap at MAX_LOG_LINES — evict oldest from top.
        while (elLog.childElementCount > MAX_LOG_LINES) {
          elLog.removeChild(elLog.firstChild);
        }
        // Sticky-bottom: only auto-scroll if the user hasn't scrolled
        // up to inspect earlier events.
        if (_userPinnedScroll) {
          elLog.scrollTop = elLog.scrollHeight;
        }
      });
    }

    function _updateStatusIcon(status) {
      if (!elIcon) return;
      elIcon.textContent = STATUS_ICON[status] || '○';
      elIcon.dataset.status = status || 'pending';
    }

    function _renderKpis(kpis) {
      if (!elKpis) return;
      if (!kpis || typeof kpis !== 'object') {
        elKpis.innerHTML = '';
        elKpis.style.display = 'none';
        return;
      }
      const entries = Object.entries(kpis).filter(([, v]) =>
        v !== undefined && v !== null && v !== '');
      if (!entries.length) {
        elKpis.innerHTML = '';
        elKpis.style.display = 'none';
        return;
      }
      elKpis.innerHTML = entries.map(([k, v]) =>
        '<span class="fw-node-drawer-kpi">' +
          '<span class="fw-node-drawer-kpi-label">' + escapeHtml(k) + '</span>' +
          '<span class="fw-node-drawer-kpi-value">' +
            escapeHtml(typeof v === 'object' ? JSON.stringify(v) : String(v)) +
          '</span>' +
        '</span>'
      ).join('');
      elKpis.style.display = '';
    }

    function _renderDetails(ctx) {
      if (!elDetails) return;
      // Primary content = the SAME rich HTML the legacy card body
      // showed (KPI grids, tables, outline cards, bandit charts).
      // Caller passes `resultsHtml` precomputed via the stage's
      // SUBSTEP_RENDERERS[idx](values). Falls back to a waiting
      // placeholder when the node hasn't produced output yet.
      const resultsBlock = ctx.resultsHtml
        ? '<div class="fw-node-drawer-results">' + ctx.resultsHtml + '</div>'
        : '<div class="fw-empty fw-node-drawer-waiting">' +
          (ctx.status === 'running'
            ? 'Running — results will appear once this node commits its checkpoint.'
            : ctx.status === 'failed'
            ? 'This node failed before producing output. See the activity log for details.'
            : ctx.status === 'future'
            ? 'Not yet implemented — substep will activate when its node code ships.'
            : 'Waiting for this node to run.') +
          '</div>';
      // Raw inputs/outputs JSON kept as a collapsed debugging aid
      // (only when present — hides when there's nothing to show).
      const debug = [];
      if (ctx.inputs) debug.push({
        id: 'inputs',  title: 'Inputs (upstream state, raw)',
        content: '<pre>' + escapeHtml(ctx.inputs) + '</pre>',
      });
      if (ctx.outputs) debug.push({
        id: 'outputs', title: 'Outputs (this node, raw)',
        content: '<pre>' + escapeHtml(ctx.outputs) + '</pre>',
      });
      const debugBlock = debug.length
        ? debug.map(s =>
            '<details class="fw-node-drawer-detail" data-section="' + s.id + '">' +
              '<summary>' + escapeHtml(s.title) + '</summary>' +
              '<div class="fw-node-drawer-detail-body">' + s.content + '</div>' +
            '</details>'
          ).join('')
        : '';
      elDetails.innerHTML = resultsBlock + debugBlock;
    }

    function _populate(stage, nodeId, ctx) {
      // Capture lastSeenAt BEFORE bumping it — so events arriving in
      // this open() session compare against the previous timestamp,
      // not the current one. First-ever open of a node has 0.
      const key = stage + '/' + nodeId;
      _prevSeenForOpen = _lastSeenAt.get(key) || 0;
      _lastSeenAt.set(key, Date.now());
      _openStage  = stage;
      _openNodeId = nodeId;
      _pendingEvents = [];
      _userPinnedScroll = true;
      if (elTitle) elTitle.textContent = ctx.label || nodeId;
      if (elMeta)  elMeta.textContent  = stage + ' · ' + nodeId;
      _updateStatusIcon(ctx.status || 'pending');
      _renderKpis(ctx.kpis);
      _renderDetails(ctx);
      // Reset log (each drawer-open starts fresh; events stream live).
      if (elLog) elLog.innerHTML = '';
      if (elLogEmpty) elLogEmpty.style.display = '';
    }

    function open(stage, nodeId, ctx) {
      if (!elDrawer) return;
      ctx = ctx || {};
      const wasVisible = elDrawer.classList.contains('visible');
      const isSameNode = (_openStage === stage && _openNodeId === nodeId);
      const elBody = document.getElementById('fw-node-drawer-body');
      // Cross-fade when switching to a different node while the drawer
      // is already open — avoids the hard content-swap flicker.
      // Same-node re-opens skip the fade (no perceptible change anyway).
      if (wasVisible && !isSameNode && elBody) {
        elBody.classList.add('fw-node-drawer-fading');
        setTimeout(() => {
          _populate(stage, nodeId, ctx);
          elBody.classList.remove('fw-node-drawer-fading');
        }, 140);
      } else {
        _populate(stage, nodeId, ctx);
      }
      elDrawer.classList.add('visible');
      // Focus close for keyboard a11y.
      if (elClose) setTimeout(() => elClose.focus(), 100);
    }

    function close() {
      if (!elDrawer) return;
      elDrawer.classList.remove('visible');
      _openStage = null;
      _openNodeId = null;
    }

    function isOpenFor(stage, nodeId) {
      return _openStage === stage && _openNodeId === nodeId;
    }

    function appendEvent(ev) {
      if (!ev || !_openNodeId) return;
      _pendingEvents.push(ev);
      _scheduleFlush();
      // Side effects on status: `done`/`failed`/`start` swap the
      // drawer's status icon to match the canvas node.
      if (ev.kind === 'start')   _updateStatusIcon('running');
      else if (ev.kind === 'done') _updateStatusIcon('done');
      else if (ev.kind === 'error') _updateStatusIcon('failed');
    }

    function updateContext(ctx) {
      if (!_openNodeId) return;
      ctx = ctx || {};
      if (ctx.status !== undefined) _updateStatusIcon(ctx.status);
      if (ctx.kpis   !== undefined) _renderKpis(ctx.kpis);
      // Re-render details only if any of the section sources changed —
      // cheap enough to do unconditionally for now.
      _renderDetails(ctx);
    }

    // Detect user scroll-away — lock auto-scroll until they return to
    // bottom. Threshold of 24px so a small wheel nudge doesn't flip it.
    if (elLog) {
      elLog.addEventListener('scroll', () => {
        const atBottom = (elLog.scrollHeight - elLog.scrollTop - elLog.clientHeight) < 24;
        _userPinnedScroll = atBottom;
      });
    }
    if (elClose) elClose.addEventListener('click', close);
    document.addEventListener('keydown', (e) => {
      if (e.key === 'Escape' && elDrawer && elDrawer.classList.contains('visible')) {
        close();
      }
    });

    return { open, close, isOpenFor, appendEvent, updateContext,
             get openNodeId() { return _openNodeId; },
             get openStage()  { return _openStage; } };
  })();

  function _initPlannerCanvas() {
    if (UI_MODE !== 'graph') {
      console.log('[plannerGraph] UI_MODE=cards (default) — canvas not mounted');
      return;
    }
    console.log('[plannerGraph] UI_MODE=graph — mounting Cytoscape canvas');
    const root = document.getElementById('fw-planner-graph');
    const canvasEl = document.getElementById('fw-planner-canvas');
    if (!root || !canvasEl) {
      console.warn('[plannerGraph] missing #fw-planner-graph or #fw-planner-canvas in DOM');
      return;
    }
    // Visibility is managed exclusively by _toggleStageEmpty (single
    // source of truth). Canvas init no longer touches display so it
    // can't race the toggle. Cytoscape may mount against a 0×0
    // container if the wrapper is hidden — that's fine; the toggle
    // calls _resizePlannerCanvas() the moment the wrapper becomes
    // visible.
    // Wait for Cytoscape (loaded with `defer` from CDN). Poll briefly.
    const startedAt = Date.now();
    function tryInit() {
      if (typeof cytoscape !== 'undefined') {
        const nodes = PLANNER_NODE_ORDER.map((id, i) => ({
          id,
          label:  PLANNER_NODE_LABELS[i] || id,
          status: plannerImplemented.has(id) ? 'pending' : 'future',
        }));
        const edges = [];
        for (let i = 0; i < PLANNER_NODE_ORDER.length - 1; i++) {
          edges.push({
            source: PLANNER_NODE_ORDER[i],
            target: PLANNER_NODE_ORDER[i + 1],
          });
        }
        const w = canvasEl.offsetWidth;
        const h = canvasEl.offsetHeight;
        console.log(
          `[plannerGraph] canvas container ready, dims=${w}x${h}` +
          (w === 0 || h === 0
            ? ' (WARNING: zero dim — graph will be invisible until ' +
              '_resizePlannerCanvas runs after panel becomes active)'
            : ''),
        );
        plannerGraph = StageGraph.create(canvasEl, {
          nodes, edges,
          onNodeClick: (nodeId) => _openPlannerNodeDrawer(nodeId),
        });
        console.log(
          `[plannerGraph] Cytoscape initialized with ${nodes.length} ` +
          `nodes, ${edges.length} edges`,
        );
        // If Step 3 is already the active panel at init time, kick a
        // resize+fit immediately. Otherwise the first resize fires from
        // showStep(3) below — Cytoscape inits inside a display:none
        // ancestor with 0x0 bounds, and without resize() it stays
        // invisible even after the panel becomes active.
        if (plannerGraph) _resizePlannerCanvas();
        _attachCanvasResizeObserver('fw-planner-canvas', _resizePlannerCanvas);
        return;
      }
      if (Date.now() - startedAt > 5000) {
        console.warn(
          '[plannerGraph] Cytoscape failed to load within 5s — ' +
          'canvas disabled, falling back to cards layout',
        );
        // Revert visibility via the canonical toggle (cards visible,
        // graph wrapper hidden) — but only if a slug is active;
        // otherwise the empty-state placeholder stays correct.
        const cardsFallback = document.getElementById('fw-planner-cards');
        if (cardsFallback) cardsFallback.style.display = '';
        root.style.display = 'none';
        return;
      }
      setTimeout(tryInit, 80);
    }
    tryInit();
  }

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
    // Step 3 — Cytoscape latches container dimensions at init time;
    // the canvas was initialized while the panel was display:none so
    // its viewport is 0x0 until we explicitly tell it to resize after
    // the panel becomes visible. Idempotent — no-op when ?ui=cards.
    if (n === 3 && plannerGraph) _resizePlannerCanvas();
    if (n === 4 && synthGraph)   _resizeSynthCanvas();
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
    // Same per-slug recovery for the synth step (Step 4). View-only:
    // the actual /resume only fires for explicit Start Synth clicks or
    // recoverActiveSynth on page-load — navigating between slugs paints
    // cached state, never triggers compute.
    _tryResumeActiveSynth(slug).catch(() => {});
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
        plannerWipeBtn.setAttribute('title',
          "Delete this framework's planner cache " +
          '(MinIO embeddings + Postgres checkpoints + browser state)');
      } else {
        plannerWipeBtn.setAttribute('disabled', 'disabled');
        plannerWipeBtn.setAttribute('title', running
          ? 'Cannot wipe while a planner run is in flight.'
          : 'Pick a framework first.');
      }
    }
    // Framework chip — logo(s) + catalog name. Mirrors the Step 2
    // progress framework strip; same `frameworkInfo` source.
    setPlannerFramework(activeSlug);
    // Empty-state placeholder — show "pick a framework" when no slug
    // is active, hide the cards/canvas in that case so the user isn't
    // confused by an inert pipeline UI dangling from prior context.
    _toggleStageEmpty('planner', !activeSlug);
  }

  // Toggles the "Pick a framework from the library to view the
  // {stage} pipeline" placeholder for a stage panel. SINGLE SOURCE OF
  // TRUTH for graph-wrapper + cards visibility — canvas init MUST NOT
  // touch these directly or it races this toggle. When switching to
  // the graph view, also kicks a resize so Cytoscape picks up the
  // freshly-visible container dimensions (otherwise the graph latches
  // 0×0 from when the wrapper was hidden).
  function _toggleStageEmpty(stage, showEmpty) {
    const emptyEl  = document.getElementById('fw-' + stage + '-empty');
    const cardsEl  = document.getElementById('fw-' + stage + '-cards');
    const graphEl  = document.getElementById('fw-' + stage + '-graph');
    if (!emptyEl) return;
    if (showEmpty) {
      emptyEl.style.display = '';
      if (cardsEl) cardsEl.style.display = 'none';
      if (graphEl) graphEl.style.display = 'none';
    } else {
      emptyEl.style.display = 'none';
      // Restore the active render path based on UI flag.
      if (UI_MODE === 'graph') {
        if (cardsEl) cardsEl.style.display = 'none';
        if (graphEl) graphEl.style.display = 'flex';
        // Re-fit Cytoscape now that the wrapper has real dimensions.
        if (stage === 'planner' && plannerGraph) _resizePlannerCanvas();
        if (stage === 'synth'   && synthGraph)   _resizeSynthCanvas();
      } else {
        if (cardsEl) cardsEl.style.display = '';
        if (graphEl) graphEl.style.display = 'none';
      }
    }
  }

  function setPlannerFramework(slug) {
    if (!plannerFwNameEl || !plannerFwLogosEl) return;
    if (!slug) {
      plannerFwNameEl.textContent = 'Pick a framework to start.';
      plannerFwNameEl.classList.add('fw-planner-fw-name-empty');
      plannerFwLogosEl.innerHTML = '';
      plannerFwLogosEl.style.display = 'none';
      return;
    }
    const info = frameworkInfo[slug] || {name: slug, logos: []};
    plannerFwNameEl.textContent = info.name || slug;
    plannerFwNameEl.classList.remove('fw-planner-fw-name-empty');
    if (info.logos && info.logos.length) {
      plannerFwLogosEl.innerHTML = info.logos.map(u =>
        '<img class="fw-planner-fw-logo" src="' + u + '" alt="">'
      ).join('');
      plannerFwLogosEl.style.display = '';
    } else {
      plannerFwLogosEl.innerHTML = '';
      plannerFwLogosEl.style.display = 'none';
    }
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
    // Day 2: also reset the Cytoscape canvas + stage pill so a fresh
    // Start Planner click presents a clean visual baseline.
    if (plannerGraph) plannerGraph.reset();
    _setPlannerStagePill('idle');
  }

  function _fieldPresent(values, field) {
    // `field in values` (even when value is null) counts as "this node
    // ran" — some nodes may legitimately write null as their output.
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
      // Sortable columns: click any header to sort asc; click again to
      // toggle desc. Sort state survives re-renders via module scope.
      _lastOffTopicValues = values;
      const decisions = (s.judge_decisions || []).slice();
      // Apply current sort state.
      const sortCol = _offTopicSort.col;
      const sortDir = _offTopicSort.dir === 'desc' ? -1 : 1;
      const _key = d => {
        if (sortCol === 'verdict')    return (d.verdict || '');
        if (sortCol === 'deployment') return ((d.deployment || '').split('/').pop() || '');
        if (sortCol === 'latency')    return (d.latency_s !== undefined && d.latency_s !== null) ? d.latency_s : -1;
        if (sortCol === 'page')       return ((d.key || '').split('/').pop() || '');
        return 0;   // 'index' / null: keep original order
      };
      if (sortCol) {
        decisions.sort((a, b) => {
          const ka = _key(a); const kb = _key(b);
          if (ka < kb) return -1 * sortDir;
          if (ka > kb) return 1 * sortDir;
          return 0;
        });
      }
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
          'position:sticky;top:0;background:var(--card);' +
          'text-align:left;padding:8px 12px;font-size:0.7rem;' +
          'color:var(--text-muted);text-transform:uppercase;' +
          'border-bottom:1px solid var(--border);z-index:2;cursor:pointer;' +
          'user-select:none';
        const _arrow = (col) => {
          if (_offTopicSort.col !== col) return ' <span style="opacity:0.3">↕</span>';
          return _offTopicSort.dir === 'desc'
            ? ' <span style="color:var(--text)">↓</span>'
            : ' <span style="color:var(--text)">↑</span>';
        };
        const th = (col, label) =>
          '<th data-sort-col="' + col + '" style="' + headStyle + '">' +
            escapeHtml(label) + _arrow(col) +
          '</th>';
        table =
          '<div class="fw-stat-dist" style="margin-top:14px">' +
            '<div class="fw-stat-dist-title">LLM verdict (' +
              decisions.length + ' decisions, click column headers to sort)</div>' +
            '<div style="max-height:340px;overflow-y:auto;border:1px solid var(--border);border-radius:4px;background:var(--card)">' +
              '<table data-table="off-topic-verdicts" style="width:100%;border-collapse:collapse;font-family:Raleway">' +
                '<thead><tr>' +
                  th('index',      'In') +
                  th('verdict',    'Verdict') +
                  th('deployment', 'Deployment') +
                  th('latency',    'Latency') +
                  th('page',       'Page') +
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

    // cluster — UMAP+HDBSCAN density clustering with soft membership.
    // KPI cards: #clusters / #noise / #boundary docs / wall_ms. Compact
    // cluster-size distribution row underneath so the operator can spot
    // pathologies (one giant cluster, all-noise, etc.).
    3: function renderCluster(values) {
      const s = values.cluster_stats || {};
      if (!s.n_docs) {
        return '<div class="fw-empty">no cluster stats reported</div>';
      }
      const kpi = (label, value, sub) =>
        '<div class="fw-stat-card">' +
          '<div class="fw-stat-card-label">' + escapeHtml(label) + '</div>' +
          '<div class="fw-stat-card-value">' + escapeHtml(value) + '</div>' +
          (sub ? '<div class="fw-stat-card-sub">' + escapeHtml(sub) + '</div>' : '') +
        '</div>';

      const noisePct = s.n_docs ? Math.round(s.n_noise / s.n_docs * 100) : 0;
      const boundaryPct = s.n_docs ? Math.round(s.n_boundary / s.n_docs * 100) : 0;
      const cards =
        kpi('Clusters', String(s.n_clusters || 0),
            'on ' + (s.n_docs || 0).toLocaleString() + ' docs') +
        kpi('Noise',    String(s.n_noise || 0),
            noisePct + '% unassigned') +
        kpi('Boundary', String(s.n_boundary || 0),
            boundaryPct + '% (max-prob < ' + (s.boundary_floor || 0.5) + ')') +
        kpi('Wall',     (s.wall_ms || 0) + ' ms',
            'UMAP→HDBSCAN');

      // Cluster size distribution — sparkline-style row.
      let dist = '';
      const sizes = s.cluster_sizes || [];
      if (sizes.length) {
        const maxSize = Math.max(...sizes);
        const bars = sizes.map(n => {
          const pct = Math.max(4, Math.round(n / maxSize * 100));
          return '<div title="' + n + ' docs" style="display:inline-block;' +
                 'width:' + pct + '%;max-width:48px;height:14px;' +
                 'background:var(--accent,#4a7);margin-right:2px;border-radius:2px;' +
                 'vertical-align:bottom"></div>';
        }).join('');
        dist =
          '<div class="fw-stat-dist" style="margin-top:14px">' +
            '<div class="fw-stat-dist-title">Cluster sizes (top ' +
              sizes.length + ', descending) — max ' + maxSize + ' docs</div>' +
            '<div style="padding:6px 0">' + bars + '</div>' +
          '</div>';
      }

      const fallback = s.fallback
        ? ' · <strong style="color:var(--accent)">' + escapeHtml(s.fallback) + '</strong>'
        : '';
      const foot =
        '<div class="fw-stat-foot">' +
          'UMAP <strong>n_components=' + (s.umap_dim || '?') + '</strong>' +
          ' · HDBSCAN <strong>min_cluster=' + (s.min_cluster_size || '?') + '</strong>' +
          ' · blob ' + Math.round((s.blob_bytes || 0) / 1024) + ' KB' +
          fallback +
        '</div>';

      return '<div class="fw-stat-grid">' + cards + '</div>' + dist + foot;
    },

    // refine — LITA boundary-doc reassignment via bandit-routed big-LLM.
    // KPI cards: boundary count + reassigned + null + wall. Recent
    // decisions table shows per-doc verdicts with deployment + latency.
    4: function renderRefine(values) {
      const s = values.refine_stats || {};
      const total = s.n_boundary || 0;
      if (!s.n_docs && !total) {
        return '<div class="fw-empty">no refine stats reported</div>';
      }
      const changed = s.n_changed || 0;
      const nulld   = s.n_null || 0;
      const errs    = s.n_errors || 0;
      const wall    = s.wall_ms || 0;
      const depUsage = s.deployment_usage || [];

      const kpi = (label, value, sub) =>
        '<div class="fw-stat-card">' +
          '<div class="fw-stat-card-label">' + escapeHtml(label) + '</div>' +
          '<div class="fw-stat-card-value">' + escapeHtml(value) + '</div>' +
          (sub ? '<div class="fw-stat-card-sub">' + escapeHtml(sub) + '</div>' : '') +
        '</div>';

      const changePct = total ? Math.round(changed / total * 100) : 0;
      const nullPct = total ? Math.round(nulld / total * 100) : 0;
      const topDep = depUsage[0]
        ? (depUsage[0].deployment.split('/').pop() + ' · ' + depUsage[0].calls + ' calls')
        : '—';
      const cards =
        kpi('Boundary docs', String(total),
            'max_prob < ' + (s.boundary_floor || 0.60)) +
        kpi('Reassigned', String(changed), changePct + '% of boundary') +
        kpi('Sent to noise', String(nulld),
            nullPct + '% null' + (errs ? ' · ' + errs + ' errors' : '')) +
        kpi('Top deployment', topDep,
            depUsage.length > 1 ? '+' + (depUsage.length - 1) + ' more' : null);

      // Bandit deployment breakdown (same pattern as off_topic).
      let depRow = '';
      if (depUsage.length) {
        const drows = depUsage.slice(0, 10).map(d =>
          '<tr>' +
            '<td style="padding:3px 12px 3px 0;font-size:0.78rem">' +
              escapeHtml((d.deployment || '?').split('/').pop()) + '</td>' +
            '<td style="padding:3px 0;font-family:JetBrains Mono,monospace;font-size:0.78rem;color:var(--text-muted)">' +
              d.calls + ' calls</td>' +
          '</tr>'
        ).join('');
        depRow =
          '<div class="fw-stat-dist" style="margin-top:14px">' +
            '<div class="fw-stat-dist-title">Bandit deployment usage (top ' +
              Math.min(10, depUsage.length) + ')</div>' +
            '<table style="width:100%;border-collapse:collapse;font-family:Raleway">' +
              '<tbody>' + drows + '</tbody>' +
            '</table>' +
          '</div>';
      }

      const fallback = s.skipped
        ? ' · <strong style="color:var(--accent)">' + escapeHtml(s.skipped) + '</strong>'
        : '';
      const cache = s.cache_hit ? ' · cache HIT' : '';
      const foot =
        '<div class="fw-stat-foot">' +
          'router <strong>pareto-bandit/dd-grader</strong>' +
          ' · top-K ' + (s.top_k || '?') +
          ' · prompt <code style="font-family:JetBrains Mono,monospace;font-size:0.72rem">' +
            escapeHtml(s.prompt_version || '?') + '</code>' +
          ' · ' + wall + ' ms' +
          cache + fallback +
        '</div>';

      return '<div class="fw-stat-grid">' + cards + '</div>' + depRow + foot;
    },

    // label — KeyLLM-style cluster naming via bandit-routed big-LLM with
    // Universal Self-Consistency + 2-round sibling-aware re-labeling.
    // KPI cards: clusters / unanimous vs USC-voted / round 2 / wall.
    // Below: full label list as a sortable table so the operator can
    // verify names match cluster contents.
    5: function renderLabel(values) {
      const s = values.label_stats || {};
      const n = s.n_clusters || 0;
      const labelsMap = s.labels || {};
      if (!n && Object.keys(labelsMap).length === 0) {
        return '<div class="fw-empty">no label stats reported</div>';
      }
      const unanimous = s.n_unanimous || 0;
      const usc = s.n_usc_voted || 0;
      const round2 = s.n_round2 || 0;
      const errs = s.n_errors || 0;
      const wall = s.wall_ms || 0;

      const kpi = (label, value, sub) =>
        '<div class="fw-stat-card">' +
          '<div class="fw-stat-card-label">' + escapeHtml(label) + '</div>' +
          '<div class="fw-stat-card-value">' + escapeHtml(value) + '</div>' +
          (sub ? '<div class="fw-stat-card-sub">' + escapeHtml(sub) + '</div>' : '') +
        '</div>';

      const unanimousPct = n ? Math.round(unanimous / n * 100) : 0;
      const cards =
        kpi('Clusters labeled', String(n),
            'wall ' + wall + ' ms' + (s.cache_hit ? ' · cache HIT' : '')) +
        kpi('Unanimous', String(unanimous),
            unanimousPct + '% on first try') +
        kpi('USC-voted', String(usc),
            'samples disagreed → LLM picked best') +
        kpi('Round 2 re-labels', String(round2),
            'with sibling-aware context' +
              (errs ? ' · ' + errs + ' errors' : ''));

      // Label table — full list, sorted by cluster ID, scrollable.
      const entries = Object.entries(labelsMap)
        .map(([k, v]) => [parseInt(k, 10), v])
        .sort((a, b) => a[0] - b[0]);
      let table = '';
      if (entries.length) {
        const rows = entries.map(([cid, label]) => {
          const cidLabel = cid < 0 ? 'noise' : '#' + cid;
          const cidColor = cid < 0 ? 'var(--text-muted)' : 'var(--text)';
          return '<tr>' +
            '<td style="padding:4px 12px 4px 8px;font-family:JetBrains Mono,monospace;font-size:0.78rem;color:' +
              cidColor + '">' + escapeHtml(cidLabel) + '</td>' +
            '<td style="padding:4px 0;font-size:0.85rem;font-weight:600">' +
              escapeHtml(label || '?') + '</td>' +
          '</tr>';
        }).join('');
        const headStyle =
          'position:sticky;top:0;background:var(--card);' +
          'text-align:left;padding:8px 12px;font-size:0.7rem;' +
          'color:var(--text-muted);text-transform:uppercase;' +
          'border-bottom:1px solid var(--border);z-index:2';
        table =
          '<div class="fw-stat-dist" style="margin-top:14px">' +
            '<div class="fw-stat-dist-title">Cluster labels (' +
              entries.length + ' total)</div>' +
            '<div style="max-height:340px;overflow-y:auto;border:1px solid var(--border);border-radius:4px;background:var(--card)">' +
              '<table style="width:100%;border-collapse:collapse;font-family:Raleway">' +
                '<thead><tr>' +
                  '<th style="' + headStyle + ';padding-left:8px">Cluster</th>' +
                  '<th style="' + headStyle + '">Label</th>' +
                '</tr></thead>' +
                '<tbody>' + rows + '</tbody>' +
              '</table>' +
            '</div>' +
          '</div>';
      }

      // Bandit deployment usage
      const depUsage = s.deployment_usage || [];
      let depRow = '';
      if (depUsage.length) {
        const drows = depUsage.slice(0, 10).map(d =>
          '<tr>' +
            '<td style="padding:3px 12px 3px 0;font-size:0.78rem">' +
              escapeHtml((d.deployment || '?').split('/').pop()) + '</td>' +
            '<td style="padding:3px 0;font-family:JetBrains Mono,monospace;font-size:0.78rem;color:var(--text-muted)">' +
              d.calls + ' calls</td>' +
          '</tr>'
        ).join('');
        depRow =
          '<div class="fw-stat-dist" style="margin-top:14px">' +
            '<div class="fw-stat-dist-title">Bandit deployment usage</div>' +
            '<table style="width:100%;border-collapse:collapse;font-family:Raleway">' +
              '<tbody>' + drows + '</tbody>' +
            '</table>' +
          '</div>';
      }

      const foot =
        '<div class="fw-stat-foot">' +
          'router <strong>pareto-bandit/dd-grader</strong>' +
          ' · N=' + (s.n_samples || '?') + ' samples + USC vote' +
          ' · prompt <code style="font-family:JetBrains Mono,monospace;font-size:0.72rem">' +
            escapeHtml(s.prompt_version || '?') + '</code>' +
        '</div>';

      return '<div class="fw-stat-grid">' + cards + '</div>' + table + depRow + foot;
    },

    // reduce — 4-12 chapter outline merged from labeled clusters.
    // KPI cards: chapters / input clusters / repairs / wall_ms.
    // Below: the full ordered outline with title + description + member
    // cluster IDs. This is the FINAL human-facing artifact of the
    // planner pipeline.
    6: function renderReduce(values) {
      const s = values.reduce_stats || {};
      const outline = s.outline || {};
      const chapters = outline.chapters || [];
      if (!chapters.length) {
        return '<div class="fw-empty">no reduce stats reported</div>';
      }

      const kpi = (label, value, sub) =>
        '<div class="fw-stat-card">' +
          '<div class="fw-stat-card-label">' + escapeHtml(label) + '</div>' +
          '<div class="fw-stat-card-value">' + escapeHtml(value) + '</div>' +
          (sub ? '<div class="fw-stat-card-sub">' + escapeHtml(sub) + '</div>' : '') +
        '</div>';

      const cards =
        kpi('Chapters', String(chapters.length),
            'from ' + (s.n_clusters_in || 0) + ' clusters') +
        kpi('Samples', String(s.n_samples || '?'),
            'N=3 + USC vote + self-refine') +
        kpi('Coverage repairs', String(s.n_repairs || 0),
            s.forced_repair ? 'forced fallback applied' : 'auto-fixed') +
        kpi('Wall', (s.wall_ms || 0) + ' ms',
            s.cache_hit ? 'cache HIT' : 'cold');

      // Full ordered outline — chapter cards
      const sortedChapters = chapters.slice().sort(
        (a, b) => (a.order || 0) - (b.order || 0),
      );
      const chapterRows = sortedChapters.map(ch => {
        const memberIds = (ch.member_cluster_ids || []).slice().sort((a,b) => a-b);
        const memberCidStr = memberIds.length
          ? memberIds.map(c => '#' + c).join(' ')
          : '<em style="color:var(--text-muted)">no clusters</em>';
        return '<tr style="border-bottom:1px solid var(--border)">' +
          '<td style="padding:8px 12px 8px 0;font-family:JetBrains Mono,monospace;font-size:0.78rem;color:var(--text-muted);vertical-align:top">' +
            (ch.order || '?') + '</td>' +
          '<td style="padding:8px 12px 8px 0;vertical-align:top;width:30%">' +
            '<div style="font-weight:700;font-size:0.95rem">' +
              escapeHtml(ch.title || '?') + '</div>' +
            '<div style="font-family:JetBrains Mono,monospace;font-size:0.7rem;color:var(--text-muted);margin-top:4px">' +
              memberCidStr + '</div>' +
          '</td>' +
          '<td style="padding:8px 0;vertical-align:top;font-size:0.85rem;color:var(--text-muted)">' +
            escapeHtml(ch.description || '') +
          '</td>' +
          '</tr>';
      }).join('');
      const headStyle =
        'position:sticky;top:0;background:var(--card);' +
        'text-align:left;padding:10px 12px;font-size:0.7rem;' +
        'color:var(--text-muted);text-transform:uppercase;' +
        'border-bottom:1px solid var(--border);z-index:2';
      const table =
        '<div class="fw-stat-dist" style="margin-top:14px">' +
          '<div class="fw-stat-dist-title">Chapter outline (' +
            sortedChapters.length + ' chapters, ordered)</div>' +
          '<div style="max-height:400px;overflow-y:auto;border:1px solid var(--border);border-radius:4px;background:var(--card)">' +
            '<table style="width:100%;border-collapse:collapse;font-family:Raleway">' +
              '<thead><tr>' +
                '<th style="' + headStyle + ';padding-left:8px;width:40px">#</th>' +
                '<th style="' + headStyle + '">Title</th>' +
                '<th style="' + headStyle + '">Description</th>' +
              '</tr></thead>' +
              '<tbody>' + chapterRows + '</tbody>' +
            '</table>' +
          '</div>' +
        '</div>';

      const fallback = s.skipped
        ? ' · <strong style="color:var(--accent)">' + escapeHtml(s.skipped) + '</strong>'
        : '';
      const errorPart = s.error
        ? ' · <strong style="color:var(--error-text)">' + escapeHtml(s.error) + '</strong>'
        : '';
      const foot =
        '<div class="fw-stat-foot">' +
          'router <strong>pareto-bandit/dd-grader</strong>' +
          ' · single-call + USC + self-refine' +
          ' · prompt <code style="font-family:JetBrains Mono,monospace;font-size:0.72rem">' +
            escapeHtml(s.prompt_version || '?') + '</code>' +
          fallback + errorPart +
        '</div>';

      return '<div class="fw-stat-grid">' + cards + '</div>' + table + foot;
    },

    // plan_write — consumer-facing final plan with hydrated `sources`.
    // KPI cards: chapters / sources / unassigned / wall_ms.
    // Below: the final outline with title, description, per-chapter
    // source count + first-N source paths (so a developer can sanity-
    // check which docs ended up where). Last card of the pipeline.
    7: function renderPlanWrite(values) {
      const s = values.plan_write_stats || {};
      const plan = s.plan || {};
      const chapters = (plan.chapters || []).slice();
      if (!chapters.length) {
        // Two cases: (a) plan_path missing entirely — node hasn't run
        // yet; (b) plan_path set but stats not yet refreshed from the
        // checkpoint commit (race window between SSE `done` and the
        // /state poll catching the latest checkpoint). Show a neutral
        // running-style message instead of the error-looking
        // placeholders previously rendered.
        if (values.plan_path) {
          return '<div class="fw-empty">plan persisted at <code style="font-family:JetBrains Mono,monospace">' +
            escapeHtml(values.plan_path) +
            '</code> — refreshing chapter details…</div>';
        }
        return '<div class="fw-empty">waiting for plan_write to commit…</div>';
      }

      const kpi = (label, value, sub) =>
        '<div class="fw-stat-card">' +
          '<div class="fw-stat-card-label">' + escapeHtml(label) + '</div>' +
          '<div class="fw-stat-card-value">' + escapeHtml(value) + '</div>' +
          (sub ? '<div class="fw-stat-card-sub">' + escapeHtml(sub) + '</div>' : '') +
        '</div>';

      const nSources = s.n_sources || (plan.stats || {}).n_sources || 0;
      const nUnassigned = s.n_unassigned || (plan.stats || {}).n_unassigned || 0;
      const nDropped = s.n_dropped || (plan.stats || {}).n_dropped || 0;
      const corpusN = (plan.provenance || {}).corpus_doc_count || 0;
      const cards =
        kpi('Chapters', String(chapters.length),
            'final ordered outline') +
        kpi('Sources',  String(nSources),
            corpusN ? 'of ' + corpusN + ' corpus docs' : 'hydrated from refine') +
        kpi('Unassigned', String(nUnassigned),
            nDropped ? nDropped + ' empty chapters dropped' : 'none dropped') +
        kpi('Wall', (s.wall_ms || 0) + ' ms',
            s.cache_hit ? 'cache HIT' : 'cold');

      const sortedChapters = chapters.slice().sort(
        (a, b) => (a.order || 0) - (b.order || 0),
      );
      const headStyle =
        'position:sticky;top:0;background:var(--card);' +
        'text-align:left;padding:10px 12px;font-size:0.7rem;' +
        'color:var(--text-muted);text-transform:uppercase;' +
        'border-bottom:1px solid var(--border);z-index:2';
      const chapterRows = sortedChapters.map(ch => {
        const srcs = (ch.sources || []).slice();
        const previewSrcs = srcs.slice(0, 4).map(p => {
          const tail = p.split('/').slice(-2).join('/');
          return '<div style="font-family:JetBrains Mono,monospace;font-size:0.7rem;color:var(--text-muted);white-space:nowrap;overflow:hidden;text-overflow:ellipsis;max-width:100%">' +
            escapeHtml(tail) + '</div>';
        }).join('');
        const moreSrcs = srcs.length > 4
          ? '<div style="font-family:JetBrains Mono,monospace;font-size:0.7rem;color:var(--text-muted);font-style:italic">… ' +
              (srcs.length - 4) + ' more</div>'
          : '';
        return '<tr style="border-bottom:1px solid var(--border)">' +
          '<td style="padding:8px 12px 8px 0;font-family:JetBrains Mono,monospace;font-size:0.78rem;color:var(--text-muted);vertical-align:top">' +
            (ch.order || '?') + '</td>' +
          '<td style="padding:8px 12px 8px 0;vertical-align:top;width:32%">' +
            '<div style="font-weight:700;font-size:0.95rem">' +
              escapeHtml(ch.title || '?') + '</div>' +
            '<div style="font-family:JetBrains Mono,monospace;font-size:0.7rem;color:var(--text-muted);margin-top:4px">' +
              escapeHtml(ch.id || '') + ' · ' + (ch.n_sources || srcs.length) + ' sources' +
            '</div>' +
          '</td>' +
          '<td style="padding:8px 12px 8px 0;vertical-align:top;font-size:0.85rem;color:var(--text-muted)">' +
            escapeHtml(ch.description || '') +
          '</td>' +
          '<td style="padding:8px 0;vertical-align:top">' +
            previewSrcs + moreSrcs +
          '</td>' +
          '</tr>';
      }).join('');
      const table =
        '<div class="fw-stat-dist" style="margin-top:14px">' +
          '<div class="fw-stat-dist-title">Final plan (' +
            sortedChapters.length + ' chapters, hydrated sources)</div>' +
          '<div style="max-height:460px;overflow-y:auto;border:1px solid var(--border);border-radius:4px;background:var(--card)">' +
            '<table style="width:100%;border-collapse:collapse;font-family:Raleway">' +
              '<thead><tr>' +
                '<th style="' + headStyle + ';padding-left:8px;width:40px">#</th>' +
                '<th style="' + headStyle + '">Chapter</th>' +
                '<th style="' + headStyle + '">Description</th>' +
                '<th style="' + headStyle + ';width:34%">Sources (sample)</th>' +
              '</tr></thead>' +
              '<tbody>' + chapterRows + '</tbody>' +
            '</table>' +
          '</div>' +
        '</div>';

      const prov = plan.provenance || {};
      const provLine =
        '<div class="fw-stat-foot">' +
          'wrote <code style="font-family:JetBrains Mono,monospace;font-size:0.72rem">' +
            escapeHtml(s.store_path || values.plan_path || '') + '</code>' +
          ' · hash <code style="font-family:JetBrains Mono,monospace;font-size:0.72rem">' +
            escapeHtml((s.manifest_hash || plan.manifest_hash || '').slice(0, 12)) + '</code>' +
          ' · upstream prompts ' +
          escapeHtml(JSON.stringify(prov.prompt_versions || {})) +
        '</div>';

      return '<div class="fw-stat-grid">' + cards + '</div>' + table + provLine;
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
    // Day 2: mirror the same state into the Cytoscape canvas. No-op
    // when ?ui=cards (plannerGraph is null). Drives node colors,
    // KPI badges, and the top-of-stage status pill (which now also
    // carries the N/8 progress count while working).
    _renderPlannerGraph(values);
    // Drawer live-refresh: if the user has the drawer open for a
    // planner node, re-hydrate its Results panel with the latest
    // SUBSTEP_RENDERERS output. Lets the drawer evolve in lockstep
    // with the card body without forcing the user to re-click.
    _refreshOpenPlannerDrawer(values);
  }

  function markPlannerFailed(message) {
    // Find the first card still running (or first pending) and flag it.
    let failedNodeId = null;
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
        failedNodeId = PLANNER_NODE_ORDER[i];
        break;
      }
    }
    // Day 2: mirror to canvas + flip stage pill to failed.
    if (plannerGraph && failedNodeId) {
      plannerGraph.setStatus(failedNodeId, 'failed');
    }
    _setPlannerStagePill('failed');
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
    // Day 2: mirror to the Cytoscape canvas — flip the corresponding
    // graph node to 'running' so the burgundy border + active-edge
    // animation kick in immediately on the SSE `start` event (without
    // waiting for the next /state refresh). Also flip the top-of-stage
    // pill to 'working' on the very first per-step start of a run.
    if (plannerGraph) {
      plannerGraph.setStatus(stepName, 'running');
      // Pill carries the in-flight step's ordinal so the user sees a
      // crisp "Working · 3/8" without waiting for the next state poll.
      const stepIdx = PLANNER_NODE_ORDER.indexOf(stepName);
      const implCount = PLANNER_NODE_ORDER.filter(n => plannerImplemented.has(n)).length;
      const progress = (stepIdx >= 0 && implCount)
        ? (stepIdx + '/' + implCount) : null;
      _setPlannerStagePill('working',
        progress ? 'Working · ' + progress : null);
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
    } else if (stepName === 'cluster') {
      if (ev.kind === 'start')              text = '· clustering ' + (ev.n_docs||0).toLocaleString() + ' docs…';
      else if (ev.kind === 'umap_start')    text = '· UMAP ' + (ev.in_dim||'?') + '-D → ' + (ev.out_dim||'?') + '-D (cosine metric, ' + (ev.n_docs||0) + ' docs)';
      else if (ev.kind === 'hdbscan_start') text = '· HDBSCAN density clustering on ' + (ev.reduced_dim||'?') + '-D embeddings';
      else if (ev.kind === 'done')          text = '✓ ' + (ev.n_clusters||0) + ' clusters · ' + (ev.n_noise||0) + ' noise · ' + (ev.n_boundary||0) + ' boundary (' + (ev.wall_ms||0) + ' ms)';
    } else if (stepName === 'refine') {
      if (ev.kind === 'start')              text = '· reading cluster state…';
      else if (ev.kind === 'context_prepared') text = '· prepared c-TF-IDF context for ' + (ev.n_clusters||0) + ' clusters; LLM-judging ' + (ev.n_boundary||0) + ' boundary docs…';
      else if (ev.kind === 'llm_progress')  text = '· LLM judged ' + (ev.judged||0).toLocaleString() + ' / ' + (ev.total||0).toLocaleString() + ' (reassigned ' + (ev.changed||0) + ', null ' + (ev.null||0) + (ev.err ? ', err ' + ev.err : '') + ')';
      else if (ev.kind === 'done')          text = '✓ ' + (ev.n_changed||0) + ' reassigned · ' + (ev.n_null||0) + ' sent to noise (' + (ev.wall_ms||0) + ' ms)';
    } else if (stepName === 'label') {
      if (ev.kind === 'start')                 text = '· preparing label context…';
      else if (ev.kind === 'context_prepared') text = '· c-TF-IDF + rep-doc context ready for ' + (ev.n_clusters||0) + ' clusters; round 1 USC labeling…';
      else if (ev.kind === 'llm_progress')     text = '· ' + (ev.round || 'round1') + ': labeled ' + (ev.judged||0) + ' / ' + (ev.total||0) + ' (unanimous ' + (ev.unanimous||0) + ', USC ' + (ev.usc||0) + (ev.err ? ', err ' + ev.err : '') + ')';
      else if (ev.kind === 'round2_start')     text = '· round 2: re-labeling ' + (ev.n_round2||0) + ' USC-split clusters with sibling context…';
      else if (ev.kind === 'done')             text = '✓ ' + (ev.n_clusters||0) + ' clusters named' + (ev.n_round2 ? ' (' + ev.n_round2 + ' via round 2)' : '') + ' · ' + (ev.wall_ms||0) + ' ms';
    } else if (stepName === 'reduce') {
      if (ev.kind === 'start')                 text = '· reading cluster + refine + label artifacts…';
      else if (ev.kind === 'context_prepared') text = '· prepared context for ' + (ev.n_clusters_in||0) + ' input clusters; generating N=3 outline samples…';
      else if (ev.kind === 'samples_generated') text = '· ' + (ev.n_samples||0) + ' samples generated; USC voting…';
      else if (ev.kind === 'usc_voted')        text = '· USC vote done; running self-refine pass (feedback → refine)…';
      else if (ev.kind === 'refined')          text = '· self-refine done; validating coverage…';
      else if (ev.kind === 'repair_attempt')   text = '· repair attempt ' + (ev.attempt||0) + ': missing ' + (ev.missing||0) + ', dup ' + (ev.duplicate||0) + ', unknown ' + (ev.unknown||0);
      else if (ev.kind === 'done')             text = '✓ ' + (ev.n_chapters||0) + ' chapters' + (ev.n_repairs ? ' (' + ev.n_repairs + ' repair' + (ev.n_repairs > 1 ? 's' : '') + ')' : '') + (ev.forced_repair ? ' [forced]' : '') + ' · ' + (ev.wall_ms||0) + ' ms';
    } else if (stepName === 'plan_write') {
      if (ev.kind === 'start')           text = '· hashing inputs… (manifest ' + ((ev.manifest_hash||'').slice(0,8)) + ')';
      else if (ev.kind === 'loaded')     text = '· loaded ' + (ev.n_chapters_in||0) + ' chapters · ' + (ev.n_clusters||0) + ' clusters · ' + (ev.n_docs||0) + ' docs';
      else if (ev.kind === 'sanitized')  text = '· sanitized · ' + (ev.n_chapters||0) + ' chapters · ' + (ev.n_sources||0) + ' sources' + (ev.n_dropped ? ' · ' + ev.n_dropped + ' empty dropped' : '') + (ev.n_unassigned ? ' · ' + ev.n_unassigned + ' unassigned' : '');
      else if (ev.kind === 'done')       text = '✓ ' + (ev.n_chapters||0) + ' chapters · ' + (ev.n_sources||0) + ' sources persisted (' + ((ev.cache_hit) ? 'cache hit' : ((ev.wall_ms||0) + ' ms')) + ')';
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
    cluster:      'cluster_assignments_ref',
    refine:       'refine_assignments_ref',
    label:        'cluster_labels_ref',
    reduce:       'chapter_plan_ref',
    plan_write:   'plan_path',
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
      // Only "fresh" events (within the last ~20 seconds) count for
      // orphan-detect. Without this, the Redis snapshot replay of an
      // old run's events (e.g. a previous cluster start that errored)
      // would suppress the auto-/resume needed to actually run the
      // step now.
      if (ev.ts && (Date.now() / 1000 - ev.ts) < 20) {
        _liveEventReceived = true;
      }

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
          _setPlannerStagePill('cancelled');
        } else {
          // Day 2: explicit done → flip pill so the at-a-glance
          // indicator transitions out of 'working' even before the
          // user navigates away. _renderPlannerGraph's aggregate
          // logic also sets this, but the explicit signal is
          // race-safer (covers the all-impl-done detection edge).
          _setPlannerStagePill('done');
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
        // Day 3: route the same event into NodeDrawer if it's open for
        // this node. The drawer's rAF batching + sticky-bottom log
        // turns the SSE stream into a live activity tail.
        if (NodeDrawer.isOpenFor('planner', ev.step)) {
          NodeDrawer.appendEvent(ev);
        }
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
      // Terminal means "no more work to do":
      //   - failed/cancelled: explicit user/system halt, regardless of
      //     how many nodes ran
      //   - done AND all currently-wired nodes have committed: full
      //     completion under the current IMPLEMENTED set
      // CRITICAL: status="done" ALONE isn't enough. If new nodes were
      // added to IMPLEMENTED after the run finished, the thread shows
      // status="done" but missing the new node's field. Treating that as
      // terminal would skip the auto-/resume that needs to run the new
      // node — exactly the cluster-not-syncing bug.
      const allImplDone = _allImplementedComplete(values);
      const effectivelyDone = (
        status === 'failed' || status === 'cancelled' ||
        (status === 'done' && allImplDone) ||
        allImplDone
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
      // Resume policy: ONLY auto-/resume for an orphaned in-flight task
      // (status === 'running' with no live events arriving within
      // _ORPHAN_DETECT_MS — pod restart killed the bg task). DO NOT
      // auto-/resume on the "status=done but new nodes pending" case
      // here; that would trigger compute every time the user clicks
      // a framework tile, cascading into parallel runs across slugs.
      // Extending an existing thread with new nodes is an EXPLICIT
      // action — the user clicks Start Planner, which routes through
      // smart Start Planner (POST /resume if thread exists). Or
      // page-load recoverActivePlanner does it for the single restored
      // slug. Navigation between slugs is view-only.
      plannerThreadId = tid;
      refreshPlannerStartState();
      renderPlannerCards(values);
      _liveEventReceived = false;
      pollPlannerState(tid);
      if (status === 'running') {
        setTimeout(async () => {
          if (plannerThreadId === tid && !_liveEventReceived) {
            try {
              await fetch(API + '/planner/' + tid + '/resume',
                {method: 'POST'});
            } catch (e) {}
          }
        }, _ORPHAN_DETECT_MS);
      }
      return true;
    } catch (e) {
      _forgetActivePlanner(slug);
      return false;
    }
  }

  async function startPlanner() {
    if (!activeSlug || plannerThreadId) return;
    resetPlannerCards();

    // Smart resume: if a thread already exists for this slug, reuse its
    // thread_id and POST /resume instead of /planner/{slug}. LangGraph's
    // ainvoke(None, config) on the expanded graph automatically skips
    // already-checkpointed nodes and runs only the new downstream ones.
    // Net: adding a 4th planner node + clicking Start Planner on a slug
    // that has steps 1-3 cached → only step 4 actually executes.
    let tid = null;
    let isResume = false;
    try {
      const r = await fetch(API + '/planner/recent');
      if (r.ok) {
        const data = await r.json();
        const found = ((data && data.recent) || [])
          .find(item => item.slug === activeSlug);
        if (found && found.thread_id) {
          tid = found.thread_id;
          isResume = true;
        }
      }
    } catch (e) { /* fall through to fresh thread */ }

    if (!tid) tid = _genPlannerThreadId(activeSlug);
    plannerThreadId = tid;
    _rememberActivePlanner(activeSlug, tid);   // page-refresh recovery
    refreshPlannerStartState();   // button flips to "Cancel Planner"
    // Kick off polling in parallel with the main POST so the user sees
    // cards advance progressively.
    pollPlannerState(tid);
    try {
      // Mode is fixed to "llm" (the unified LITA-pattern planner) —
      // the dropdown was removed; the server still defaults `mode=llm`
      // if omitted, so we don't even need to pass it.
      const url = isResume
        ? API + '/planner/' + tid + '/resume'
        : API + '/planner/' + activeSlug +
          '?mode=llm&thread_id=' + encodeURIComponent(tid);
      const r = await fetch(url, {method: 'POST'});
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

  // Card-head click → toggle expanded body.
  // Header-cell click in the off_topic verdict table → sort by that column.
  plannerCardsEl.addEventListener('click', ev => {
    // Sort header click — take precedence over card-head expansion.
    const sortTh = ev.target.closest('th[data-sort-col]');
    if (sortTh) {
      ev.stopPropagation();
      const col = sortTh.dataset.sortCol;
      if (_offTopicSort.col === col) {
        // Toggle direction; third click clears the sort.
        if (_offTopicSort.dir === 'asc') _offTopicSort.dir = 'desc';
        else { _offTopicSort.col = null; _offTopicSort.dir = 'asc'; }
      } else {
        _offTopicSort.col = col;
        _offTopicSort.dir = 'asc';
      }
      // Re-render the off_topic card body from cached values (no refetch).
      const c = cardEl(2);   // off_topic substep idx
      if (c && _lastOffTopicValues) {
        const body = c.querySelector('.fw-planner-card-body');
        const renderer = SUBSTEP_RENDERERS[2];
        if (body && renderer) {
          body.innerHTML = renderer(_lastOffTopicValues);
        }
      }
      return;
    }
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
        // Library click swaps the ACTIVE FRAMEWORK without changing the
        // user's current step — they stay wherever they were navigating
        // (Catalog on first interaction, otherwise whatever step they
        // last opened). farthestStep bumped to the max so all 5 steps
        // stay reachable via the stepper for the newly-selected slug.
        farthestStep = Math.max(farthestStep, 5);
        renderStepper();
        refreshPlannerStartState();
        if (typeof refreshSynthStartState === 'function') {
          refreshSynthStartState();
        }
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
    // Page-load behaviour (per user UX rule): NEVER auto-activate a
    // framework on reload — the user lands on Catalog (Step 1) and
    // must click a library item to pick a framework. The previous
    // behaviour (auto-pick the first cached slug + jump to Step 3)
    // was confusing because the sidebar wouldn't show any item as
    // active even though the Planner panel had data.
    //
    // This function now ONLY hydrates the planner localStorage from
    // the server-side /planner/recent endpoint (useful for browsers
    // that wipe localStorage like Brave / Safari private mode). The
    // hydrated entries make _tryResumeActivePlanner(slug) work later
    // when the user explicitly clicks a library item.
    if (activeSlug) return;     // some other path already activated
    const keys = [];
    try {
      for (let i = 0; i < localStorage.length; i++) {
        const k = localStorage.key(i);
        if (k && k.startsWith('dd:planner:active:')) keys.push(k);
      }
    } catch (e) { return; }
    if (keys.length) return;   // localStorage already populated; nothing to do
    // localStorage empty — try to seed it from the server's recent list.
    try {
      const r = await fetch(API + '/planner/recent');
      if (!r.ok) return;
      const data = await r.json();
      const recent = (data && data.recent) || [];
      for (const item of recent) {
        try {
          localStorage.setItem(_plannerStorageKey(item.slug), item.thread_id);
        } catch (e) {}
      }
      if (recent.length) {
        try { localStorage.setItem(_LAST_PLANNER_SLUG_KEY, recent[0].slug); }
        catch (e) {}
      }
    } catch (e) {
      console.warn('[planner-recover] /planner/recent failed:', e);
    }
  }

  async function loadPlannerInfo() {
    try {
      const r = await fetch(API + '/planner/info');
      if (!r.ok) return;
      const data = await r.json();
      plannerImplemented = new Set(data.implemented || []);
      // Mode dropdown removed 2026-05-18 — the unified LITA-pattern
      // planner is the only mode now (see PLANNER-ARCHITECTURE-2026-05-17
      // .md). Server still returns `modes` for backwards compatibility
      // but the client no longer renders the picker.
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
  // Initial empty-state — show the "pick a framework" placeholder on
  // both Planner + Synth panels until a slug becomes active. The
  // refresh*StartState functions will hide them the moment a library
  // item or tile is clicked.
  _toggleStageEmpty('planner', true);
  _toggleStageEmpty('synth',   true);
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
    // Day 1: mount Cytoscape canvas if `?ui=graph` is on the URL. Runs
    // AFTER loadPlannerInfo so the initial node statuses (future vs
    // pending) reflect the server's IMPLEMENTED set.
    try { _initPlannerCanvas(); }
    catch (e) { console.warn('[init] planner-canvas failed:', e); }
    try { await recoverActivePlanner(); }
    catch (e) { console.warn('[init] planner-recover failed:', e); }
    try { await loadSynthInfo(); }
    catch (e) { console.warn('[init] synth-info failed:', e); }
    // Day 5: mount the synth Cytoscape canvas if ?ui=graph. Runs after
    // loadSynthInfo so the initial node statuses respect IMPLEMENTED.
    try { _initSynthCanvas(); }
    catch (e) { console.warn('[init] synth-canvas failed:', e); }
    try { await recoverActiveSynth(); }
    catch (e) { console.warn('[init] synth-recover failed:', e); }
  })();

  // ============================================================
  // Step 4 — Synth (UI scaffolding; nodes ship incrementally per
  // `docs/SYNTH-ARCHITECTURE-SOTA-2026-05-18.md`). Mirrors the planner
  // structure 1:1 — when synth nodes ship, the per-substep renderers +
  // live-progress text get filled in and IMPLEMENTED grows server-side.
  // ============================================================
  const synthStartBtn    = document.querySelector('#fw-synth-start');
  const synthWipeBtn     = document.querySelector('#fw-synth-wipe');
  const synthCardsEl     = document.querySelector('#fw-synth-cards');
  const synthBudgetSel   = document.querySelector('#fw-synth-budget');
  const synthFwLogosEl   = document.querySelector('#fw-synth-fw-logos');
  const synthFwNameEl    = document.querySelector('#fw-synth-fw-name');

  // Substep order MUST match `NODE_ORDER` in
  // services/docs_distiller/synth/graph.py (when that ships) AND the
  // field each node writes (`state.<field>`).
  // Field names are TENTATIVE — they're placeholders that match the
  // SOTA architecture doc. Update when the real graph.py lands.
  const SYNTH_SUBSTEP_FIELDS = [
    'synth_cache_hit',       // cache_lookup
    'normalized_corpus_ref', // corpus_normalize
    'outline_dag_ref',       // outline_sdp
    'digest_ref',            // digest_construct
    'vault_ref',             // vault_sentinelize
    'sawc_drafts_ref',       // sawc_write
    'checklist_results_ref', // checklist_eval
    'mgsr_actions_ref',      // mgsr_replan
    'chapters_path',         // render_audit_write
  ];
  const SYNTH_NODE_ORDER = [
    'cache_lookup', 'corpus_normalize', 'outline_sdp', 'digest_construct',
    'vault_sentinelize', 'sawc_write', 'checklist_eval',
    'mgsr_replan', 'render_audit_write',
  ];
  // Short labels for the graph canvas (parallel to SYNTH_NODE_ORDER).
  // Same shape as PLANNER_NODE_LABELS — kept hardcoded here so the
  // StageGraph module stays independent of DOM-card scraping.
  const SYNTH_NODE_LABELS = [
    'Cache lookup', 'Corpus normalize', 'Outline (SDP)', 'Digest',
    'Vault sentinelize', 'SAWC write', 'Checklist eval',
    'MGSR replan', 'Render + audit',
  ];
  // Per-step "primary checkpoint field" for SSE→state-refresh races.
  const SYNTH_STEP_TO_FIELD = {
    cache_lookup:       'synth_cache_hit',
    corpus_normalize:   'normalized_corpus_ref',
    outline_sdp:        'outline_dag_ref',
    digest_construct:   'digest_ref',
    vault_sentinelize:  'vault_ref',
    sawc_write:         'sawc_drafts_ref',
    checklist_eval:     'checklist_results_ref',
    mgsr_replan:        'mgsr_actions_ref',
    render_audit_write: 'chapters_path',
  };

  // Populated from GET /synth/info. Cards whose substep isn't in this
  // set stay "⏳ future" — same pattern as plannerImplemented.
  let synthImplemented = new Set();
  let synthThreadId = null;
  let _synthLiveEventReceived = false;
  let synthPollAbort = false;

  // Per-substep custom body renderers, keyed by idx (matches
  // SYNTH_SUBSTEP_FIELDS). Empty until nodes ship — each renderer gets
  // added as its corresponding node lands. Until then, cards with
  // `present` field fall back to formatFieldValue/JSON dump.
  const SYNTH_SUBSTEP_RENDERERS = {};

  // ============================================================
  // Day 5 — Synth canvas parity. Mirrors planner's helpers so each
  // shipped synth node lights up the same way Planner does today.
  // The canvas appears under ?ui=graph; cards remain the default view.
  // ============================================================
  let synthGraph = null;     // Cytoscape instance once mounted

  function _setSynthStagePill(status, labelOverride) {
    const pill = document.getElementById('fw-synth-pill');
    const text = document.getElementById('fw-synth-pill-text');
    if (!pill || !text) return;
    const labels = {
      idle: 'Idle', working: 'Working', done: 'Completed',
      failed: 'Failed', cancelled: 'Cancelled',
    };
    pill.dataset.status = status;
    text.textContent = labelOverride || labels[status] || status;
  }

  // KPI extraction per synth node. Currently every field is empty
  // because no synth nodes ship state yet — populated as each lands.
  // Format mirrors _kpiForNode (planner side): returns 'k=v' string or
  // empty. When synth nodes start emitting real `*_stats`, fill these.
  function _kpiForSynthNode(nodeId, values) {
    if (!values) return '';
    const stats = (key) => values[key] || null;
    switch (nodeId) {
      case 'cache_lookup':       { const s = stats('synth_cache_stats');
        return s && s.hit !== undefined ? `hit=${s.hit}` : ''; }
      case 'corpus_normalize':   { const s = stats('normalize_stats');
        return s && s.files !== undefined ? `n=${s.files}` : ''; }
      case 'outline_sdp':        { const s = stats('outline_stats');
        return s && s.sections !== undefined ? `sec=${s.sections}` : ''; }
      case 'digest_construct':   { const s = stats('digest_stats');
        return s && s.sources !== undefined ? `src=${s.sources}` : ''; }
      case 'vault_sentinelize':  { const s = stats('vault_stats');
        return s && s.refs !== undefined ? `refs=${s.refs}` : ''; }
      case 'sawc_write':         { const s = stats('sawc_stats');
        return s && s.drafts !== undefined ? `drafts=${s.drafts}` : ''; }
      case 'checklist_eval':     { const s = stats('checklist_stats');
        return s && s.pass !== undefined ? `pass=${s.pass}` : ''; }
      case 'mgsr_replan':        { const s = stats('mgsr_stats');
        return s && s.actions !== undefined ? `act=${s.actions}` : ''; }
      case 'render_audit_write': { const s = stats('render_stats');
        return s && s.chapters !== undefined ? `ch=${s.chapters}` : ''; }
    }
    return '';
  }

  function _renderSynthGraph(values) {
    if (!synthGraph) return;
    let doneCount = 0;
    let anyRunning = false;
    for (let i = 0; i < SYNTH_NODE_ORDER.length; i++) {
      const nodeId = SYNTH_NODE_ORDER[i];
      const field = SYNTH_SUBSTEP_FIELDS[i];
      const present = _synthFieldPresent(values, field);
      const isImpl = synthImplemented.has(nodeId);
      let status;
      if (present)      { status = 'done'; doneCount++; }
      else if (!isImpl) { status = 'future'; }
      else if (i === doneCount && synthThreadId !== null) {
        status = 'running'; anyRunning = true;
      } else            { status = 'pending'; }
      synthGraph.setStatus(nodeId, status,
        present ? _kpiForSynthNode(nodeId, values) : '');
    }
    const explicitStatus = (values && values.status) || null;
    const implCount = SYNTH_NODE_ORDER.filter(n => synthImplemented.has(n)).length;
    const progress = implCount ? doneCount + '/' + implCount : null;
    if (explicitStatus === 'failed')        _setSynthStagePill('failed');
    else if (explicitStatus === 'cancelled') _setSynthStagePill('cancelled');
    else if (anyRunning || synthThreadId !== null) {
      _setSynthStagePill('working',
        progress ? 'Working · ' + progress : null);
    } else if (doneCount > 0 && doneCount === implCount) {
      _setSynthStagePill('done');
    } else if (doneCount === 0) {
      _setSynthStagePill('idle');
    }
  }

  function _buildSynthNodeCtx(nodeId, values) {
    const idx = SYNTH_NODE_ORDER.indexOf(nodeId);
    if (idx < 0) return null;
    const label = SYNTH_NODE_LABELS[idx] || nodeId;
    const thisField = SYNTH_SUBSTEP_FIELDS[idx];
    let status = 'pending';
    if (_synthFieldPresent(values, thisField)) status = 'done';
    else if (!synthImplemented.has(nodeId)) status = 'future';
    else if (synthThreadId) status = 'running';
    const kpiText = _kpiForSynthNode(nodeId, values);
    const kpis = {};
    if (kpiText) {
      const eqIdx = kpiText.indexOf('=');
      if (eqIdx > 0) kpis[kpiText.slice(0, eqIdx)] = kpiText.slice(eqIdx + 1);
    }
    // Synth's SUBSTEP_RENDERERS is empty until nodes ship; same
    // pattern as planner — when a renderer lands, drawer gets the
    // rich KPI/table/outline view automatically.
    const renderer = SYNTH_SUBSTEP_RENDERERS[idx];
    const resultsHtml = (renderer && _synthFieldPresent(values, thisField))
      ? renderer(values)
      : null;
    const inputs = idx > 0 && _synthFieldPresent(values, SYNTH_SUBSTEP_FIELDS[idx - 1])
      ? JSON.stringify({ [SYNTH_SUBSTEP_FIELDS[idx - 1]]: values[SYNTH_SUBSTEP_FIELDS[idx - 1]] }, null, 2)
      : null;
    const outputs = _synthFieldPresent(values, thisField)
      ? JSON.stringify({ [thisField]: values[thisField] }, null, 2)
      : null;
    return { label, status, kpis, resultsHtml, inputs, outputs };
  }

  async function _openSynthNodeDrawer(nodeId) {
    let values = {};
    // Same fallback as planner: localStorage thread id covers the
    // post-terminal case when synthThreadId has been nulled.
    let tid = synthThreadId;
    if (!tid && activeSlug) {
      try { tid = localStorage.getItem(_synthStorageKey(activeSlug)); }
      catch (e) {}
    }
    if (tid) {
      try {
        const r = await fetch(API + '/synth/debug/graph/' + tid + '/state');
        if (r.ok) values = (await r.json()).values || {};
      } catch (e) { /* drawer opens with empty results */ }
    }
    const ctx = _buildSynthNodeCtx(nodeId, values);
    if (ctx) NodeDrawer.open('synth', nodeId, ctx);
  }

  function _refreshOpenSynthDrawer(values) {
    if (NodeDrawer.openStage !== 'synth') return;
    const nodeId = NodeDrawer.openNodeId;
    if (!nodeId) return;
    const ctx = _buildSynthNodeCtx(nodeId, values);
    if (ctx) NodeDrawer.updateContext(ctx);
  }

  function _resizeSynthCanvas() {
    if (!synthGraph || !synthGraph.cy) return;
    requestAnimationFrame(() => {
      _runSynthLayoutAndCenter('first');
      setTimeout(() => _runSynthLayoutAndCenter('second'), 250);
    });
  }

  function _runSynthLayoutAndCenter(passLabel) {
    if (!synthGraph || !synthGraph.cy) return;
    try {
      const cy = synthGraph.cy;
      cy.resize();
      const hasDagre = !!cytoscape._dagreRegistered;
      const layout = cy.layout(hasDagre
        ? { name: 'dagre', rankDir: 'TB', nodeSep: 36, rankSep: 56,
            padding: 32, animate: false, fit: false }
        : { name: 'breadthfirst', directed: true, padding: 32,
            spacingFactor: 1.4, animate: false, fit: false }
      );
      layout.one('layoutstop', () => {
        try {
          cy.fit(cy.elements(), 32);
          cy.center(cy.elements());
          _forceCenterHorizontal(cy, '[synthGraph ' + passLabel + ']');
        } catch (e) {
          console.warn('[synthGraph] center pipeline failed:', e);
        }
      });
      layout.run();
    } catch (e) {
      console.warn('[synthGraph] resize ' + passLabel + ' failed:', e);
    }
  }

  function _initSynthCanvas() {
    if (UI_MODE !== 'graph') return;
    const root = document.getElementById('fw-synth-graph');
    const canvasEl = document.getElementById('fw-synth-canvas');
    if (!root || !canvasEl) return;
    // Visibility managed by _toggleStageEmpty (single source of truth)
    // — mirror of the planner-side fix. Canvas init no longer races
    // the toggle by setting display directly.
    const startedAt = Date.now();
    function tryInit() {
      if (typeof cytoscape !== 'undefined') {
        const nodes = SYNTH_NODE_ORDER.map((id, i) => ({
          id,
          label:  SYNTH_NODE_LABELS[i] || id,
          status: synthImplemented.has(id) ? 'pending' : 'future',
        }));
        const edges = [];
        for (let i = 0; i < SYNTH_NODE_ORDER.length - 1; i++) {
          edges.push({ source: SYNTH_NODE_ORDER[i],
                       target: SYNTH_NODE_ORDER[i + 1] });
        }
        console.log(
          `[synthGraph] canvas container ready, dims=${canvasEl.offsetWidth}x${canvasEl.offsetHeight}`
        );
        synthGraph = StageGraph.create(canvasEl, {
          nodes, edges,
          onNodeClick: (nodeId) => _openSynthNodeDrawer(nodeId),
        });
        console.log(
          `[synthGraph] Cytoscape initialized with ${nodes.length} nodes, ${edges.length} edges`
        );
        if (synthGraph) _resizeSynthCanvas();
        _attachCanvasResizeObserver('fw-synth-canvas', _resizeSynthCanvas);
        return;
      }
      if (Date.now() - startedAt > 5000) {
        console.warn('[synthGraph] Cytoscape failed to load within 5s; falling back to cards');
        const cardsFallback = document.getElementById('fw-synth-cards');
        if (cardsFallback) cardsFallback.style.display = '';
        root.style.display = 'none';
        return;
      }
      setTimeout(tryInit, 80);
    }
    tryInit();
  }

  // Window resize handler — rAF-throttled (mirrors planner equivalent).
  let _synthResizeRafPending = false;
  window.addEventListener('resize', () => {
    if (_synthResizeRafPending) return;
    _synthResizeRafPending = true;
    requestAnimationFrame(() => {
      _synthResizeRafPending = false;
      if (synthGraph) _resizeSynthCanvas();
    });
  });

  function synthCardEl(idx) {
    if (!synthCardsEl) return null;
    return synthCardsEl.querySelector(
      '.fw-planner-card[data-idx="' + idx + '"]');
  }

  function _synthStepIdx(stepName) {
    return SYNTH_SUBSTEP_FIELDS.findIndex((_, i) =>
      synthCardEl(i)?.dataset.substep === stepName);
  }

  function _synthFieldPresent(values, field) {
    return values && Object.prototype.hasOwnProperty.call(values, field);
  }

  function _synthAllImplementedComplete(values) {
    if (!synthImplemented || !synthImplemented.size) return false;
    for (let i = 0; i < SYNTH_NODE_ORDER.length; i++) {
      const step = SYNTH_NODE_ORDER[i];
      if (!synthImplemented.has(step)) continue;
      const field = SYNTH_SUBSTEP_FIELDS[i];
      if (!_synthFieldPresent(values, field)) return false;
    }
    return true;
  }

  function _synthLiveProgressEl(stepName, idx) {
    const c = synthCardEl(idx);
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

  function _markSynthCardRunning(stepName) {
    const idx = _synthStepIdx(stepName);
    if (idx < 0) return;
    const c = synthCardEl(idx);
    if (!c) return;
    if (c.classList.contains('done')) return;
    c.classList.add('running');
    c.classList.remove('failed', 'future');
    const icon = c.querySelector('.fw-planner-card-icon');
    if (icon) { icon.textContent = '◐'; icon.dataset.status = 'running'; }
    const body = c.querySelector('.fw-planner-card-body');
    if (body && body.querySelector('.fw-empty')) {
      body.innerHTML = '';
    }
    // Day 5: mirror to canvas + flip stage pill, same pattern as planner.
    if (synthGraph) {
      synthGraph.setStatus(stepName, 'running');
      const stepIdx = SYNTH_NODE_ORDER.indexOf(stepName);
      const implCount = SYNTH_NODE_ORDER.filter(n => synthImplemented.has(n)).length;
      const progress = (stepIdx >= 0 && implCount)
        ? (stepIdx + '/' + implCount) : null;
      _setSynthStagePill('working',
        progress ? 'Working · ' + progress : null);
    }
  }

  // Per-step live-progress text. Every step starts with a generic
  // "running…" line; specific event kinds get richer messages as nodes
  // ship + define their SSE event surface. Mirrors planner's
  // _renderLiveProgress pattern.
  function _renderSynthLiveProgress(stepName, ev) {
    const idx = _synthStepIdx(stepName);
    if (idx < 0) return;
    const c = synthCardEl(idx);
    if (c && c.classList.contains('done')) return;
    const el = _synthLiveProgressEl(stepName, idx);
    if (!el) return;
    let text = '';
    // Generic lifecycle fallbacks — every node SHOULD emit start/done at
    // minimum. Per-step custom kinds get added below as nodes ship.
    if (ev.kind === 'start')      text = '· starting ' + stepName + '…';
    else if (ev.kind === 'done')  text = '✓ done (' + (ev.wall_ms || 0) + ' ms)';
    else if (ev.kind === 'error') text = '✕ ' + (ev.error || 'failed');
    // Per-step rich progress lines added here as nodes ship. Examples
    // (placeholders to fill in when each node lands):
    //   if (stepName === 'outline_sdp')        { ... DAG stage timeline ... }
    //   if (stepName === 'digest_construct')   { ... per-source progress ... }
    //   if (stepName === 'sawc_write')         { ... per-section / per-iter ... }
    //   if (stepName === 'checklist_eval')     { ... criteria pass-rate ... }
    //   if (stepName === 'mgsr_replan')        { ... live replan actions ... }
    if (text) el.textContent = text;
  }

  function renderSynthCards(values) {
    if (!synthCardsEl) return;
    let doneCount = 0;
    for (let i = 0; i < SYNTH_SUBSTEP_FIELDS.length; i++) {
      const field = SYNTH_SUBSTEP_FIELDS[i];
      const c = synthCardEl(i);
      if (!c) continue;
      const icon = c.querySelector('.fw-planner-card-icon');
      const body = c.querySelector('.fw-planner-card-body');
      const present = _synthFieldPresent(values, field);
      const cardData = c.dataset.substep || '';
      const isImplemented = synthImplemented.has(cardData);
      if (present) {
        c.classList.add('done');
        c.classList.remove('running', 'failed', 'future');
        icon.textContent = '●'; icon.dataset.status = 'done';
        const renderer = SYNTH_SUBSTEP_RENDERERS[i];
        if (renderer) {
          body.innerHTML = renderer(values);
        } else {
          const v = values[field];
          body.innerHTML = '<pre>' + escapeHtml(formatFieldValue(v)) + '</pre>';
        }
        doneCount++;
      } else if (!isImplemented) {
        // Stub — render as future (⏳).
        c.classList.add('future');
        c.classList.remove('running', 'done', 'failed');
        icon.textContent = '⏳'; icon.dataset.status = 'future';
        body.innerHTML =
          '<div class="fw-empty">Substep not yet implemented — will be ' +
          'wired into the graph as its real logic lands.</div>';
      } else if (i === doneCount && synthThreadId !== null) {
        // First not-done IMPLEMENTED card while polling = currently running.
        c.classList.add('running');
        c.classList.remove('done', 'failed', 'future');
        icon.textContent = '◐'; icon.dataset.status = 'running';
      } else {
        c.classList.remove('running', 'done', 'failed', 'future');
        icon.textContent = '○'; icon.dataset.status = 'pending';
      }
    }
    // Mirror state into the Cytoscape canvas (no-op when ?ui=cards).
    // Drives node colors + KPI badges + the top-of-stage status pill.
    _renderSynthGraph(values);
    // Live-refresh drawer if open for a synth node (same pattern as
    // planner — _refreshOpenSynthDrawer is a no-op when not open).
    _refreshOpenSynthDrawer(values);
  }

  function markSynthFailed(message) {
    let failedNodeId = null;
    for (let i = 0; i < SYNTH_SUBSTEP_FIELDS.length; i++) {
      const c = synthCardEl(i);
      if (!c) continue;
      if (c.classList.contains('running') ||
          (!c.classList.contains('done') && !c.classList.contains('failed') &&
           !c.classList.contains('future'))) {
        c.classList.remove('running');
        c.classList.add('failed', 'expanded');
        const icon = c.querySelector('.fw-planner-card-icon');
        icon.textContent = '✕';
        icon.dataset.status = 'failed';
        c.querySelector('.fw-planner-card-body').innerHTML =
          '<div class="fw-planner-error">' + escapeHtml(message) + '</div>';
        failedNodeId = SYNTH_NODE_ORDER[i];
        break;
      }
    }
    if (synthGraph && failedNodeId) synthGraph.setStatus(failedNodeId, 'failed');
    _setSynthStagePill('failed');
  }

  function resetSynthCards() {
    SYNTH_SUBSTEP_FIELDS.forEach((_, i) => {
      const c = synthCardEl(i);
      if (!c) return;
      c.classList.remove('running', 'done', 'failed', 'expanded');
      const substep = c.dataset.substep || '';
      // Stubs go back to future (⏳); implemented nodes go to pending (○).
      const isImpl = synthImplemented.has(substep);
      c.classList.toggle('future', !isImpl);
      const icon = c.querySelector('.fw-planner-card-icon');
      icon.textContent = isImpl ? '○' : '⏳';
      icon.dataset.status = isImpl ? 'pending' : 'future';
      c.querySelector('.fw-planner-card-latency').textContent = '';
      c.querySelector('.fw-planner-card-body').innerHTML = isImpl
        ? '<div class="fw-empty">Output will appear here once the substep runs.</div>'
        : '<div class="fw-empty">Substep not yet implemented — will be ' +
          'wired into the graph as its real logic lands.</div>';
    });
    // Day 5: also reset the Cytoscape canvas + stage pill on Start.
    if (synthGraph) synthGraph.reset();
    _setSynthStagePill('idle');
  }

  function refreshSynthStartState() {
    if (!synthStartBtn) return;
    // Three states for the Start/Cancel button (mirrors planner):
    //  - running        → "Cancel Synth"
    //  - idle, ready    → "Start Synth" enabled
    //  - idle, blocked  → "Start Synth" disabled
    // Until the first synth node ships, "ready" requires the server's
    // /synth/info implemented list to be non-empty — otherwise clicking
    // Start would just hit the 503 stub. Show the button but disabled
    // with a clarifying tooltip so the user sees the path is wired but
    // not yet active.
    const running = synthThreadId !== null;
    if (running) {
      synthStartBtn.removeAttribute('disabled');
      synthStartBtn.classList.add('btn-outline');
      synthStartBtn.classList.remove('btn-primary');
      synthStartBtn.innerHTML = 'Cancel Synth';
    } else {
      const hasNodes = synthImplemented && synthImplemented.size > 0;
      const ready = activeSlug && activeRunId === null && hasNodes;
      if (ready) {
        synthStartBtn.removeAttribute('disabled');
        synthStartBtn.removeAttribute('title');
      } else {
        synthStartBtn.setAttribute('disabled', 'disabled');
        if (!hasNodes) {
          synthStartBtn.setAttribute(
            'title',
            'Synth pipeline not yet implemented — substeps light up as nodes ship.',
          );
        } else if (!activeSlug) {
          synthStartBtn.setAttribute('title', 'Pick a framework first.');
        }
      }
      synthStartBtn.classList.add('btn-primary');
      synthStartBtn.classList.remove('btn-outline');
      synthStartBtn.innerHTML = 'Start Synth';
    }
    if (synthWipeBtn) {
      if (activeSlug && !running && synthImplemented.size > 0) {
        synthWipeBtn.removeAttribute('disabled');
        synthWipeBtn.setAttribute('title',
          "Delete this framework's synth cache " +
          '(MinIO chapter artifacts + Postgres checkpoints + browser state)');
      } else {
        synthWipeBtn.setAttribute('disabled', 'disabled');
        synthWipeBtn.setAttribute('title', running
          ? 'Cannot wipe while a synth run is in flight.'
          : (synthImplemented.size === 0
              ? 'Synth pipeline not yet implemented.'
              : 'Pick a framework first.'));
      }
    }
    // Framework chip + stage-pill aggregate state.
    setSynthFramework(activeSlug);
    if (!running) {
      // When idle, pill reflects "have any synth output for this slug?"
      // — but since no nodes are implemented yet, default to 'idle'.
      // _renderSynthGraph overrides this on the next state refresh.
      _setSynthStagePill('idle');
    }
    // Empty-state placeholder — hide the cards/canvas when no slug
    // is active so the panel doesn't show an inert pipeline UI.
    _toggleStageEmpty('synth', !activeSlug);
  }

  function setSynthFramework(slug) {
    if (!synthFwNameEl || !synthFwLogosEl) return;
    if (!slug) {
      synthFwNameEl.textContent = 'Pick a framework to start.';
      synthFwNameEl.classList.add('fw-planner-fw-name-empty');
      synthFwLogosEl.innerHTML = '';
      synthFwLogosEl.style.display = 'none';
      return;
    }
    const info = frameworkInfo[slug] || {name: slug, logos: []};
    synthFwNameEl.textContent = info.name || slug;
    synthFwNameEl.classList.remove('fw-planner-fw-name-empty');
    if (info.logos && info.logos.length) {
      synthFwLogosEl.innerHTML = info.logos.map(u =>
        '<img class="fw-planner-fw-logo" src="' + u + '" alt="">'
      ).join('');
      synthFwLogosEl.style.display = '';
    } else {
      synthFwLogosEl.innerHTML = '';
      synthFwLogosEl.style.display = 'none';
    }
  }

  // Race-tolerant state fetch (mirrors planner's _refreshCardsFromState).
  async function _refreshSynthCardsFromState(threadId, expectedField) {
    const maxAttempts = expectedField ? 6 : 1;
    for (let i = 0; i < maxAttempts; i++) {
      try {
        const r = await fetch(API + '/synth/debug/graph/' + threadId + '/state');
        if (r.ok) {
          const data = await r.json();
          const values = data.values || {};
          if (!expectedField || _synthFieldPresent(values, expectedField)) {
            renderSynthCards(values);
            return;
          }
        }
      } catch (e) { /* transient */ }
      await sleep(250 + 150 * i);
    }
  }

  // SSE consumer — symmetric with pollPlannerState. Connects to
  // /synth/{thread_id}/events; per-step events drive live-progress text
  // and trigger state refresh at node boundaries.
  async function pollSynthState(threadId) {
    const url = API + '/synth/' + threadId + '/events';
    let es;
    try {
      es = new EventSource(url);
    } catch (e) {
      markSynthFailed('EventSource open failed: ' + String(e));
      synthThreadId = null;
      refreshSynthStartState();
      return;
    }
    es.onmessage = async (msg) => {
      if (synthThreadId !== threadId) {
        try { es.close(); } catch (_) {}
        return;
      }
      let ev;
      try { ev = JSON.parse(msg.data); } catch (_) { return; }
      // Only "fresh" events count for orphan-detection (same heuristic
      // as planner — Redis snapshot replay of an old run wouldn't
      // suppress a needed /resume).
      if (ev.ts && (Date.now() / 1000 - ev.ts) < 20) {
        _synthLiveEventReceived = true;
      }
      if (ev.step === 'synth' && ev.kind === 'terminal') {
        // Stub-router's empty-stream emits this immediately. Real impl
        // emits it after the graph reaches END.
        await _refreshSynthCardsFromState(threadId, 'status');
        const status = ev.status || 'done';
        if (status === 'failed') {
          markSynthFailed(ev.error || 'Synth failed.');
        } else if (status === 'cancelled') {
          showToast('Synth cancelled. Checkpoints up to the cancel point are preserved.');
          _setSynthStagePill('cancelled');
        } else if (status === 'not_implemented') {
          // Router stub — no run happened. Don't toast; the cards stay
          // in their "future" state which already communicates the gap.
        } else {
          _setSynthStagePill('done');
        }
        try { es.close(); } catch (_) {}
        synthThreadId = null;
        refreshSynthStartState();
        return;
      }
      if (ev.step) {
        if (ev.kind === 'start') {
          _markSynthCardRunning(ev.step);
          const stepIdx = SYNTH_NODE_ORDER.indexOf(ev.step);
          if (stepIdx > 0) {
            const prevStep = SYNTH_NODE_ORDER[stepIdx - 1];
            const prevField = SYNTH_STEP_TO_FIELD[prevStep];
            await _refreshSynthCardsFromState(threadId, prevField);
            _markSynthCardRunning(ev.step);
          }
        }
        if (ev.kind === 'done') {
          const field = SYNTH_STEP_TO_FIELD[ev.step];
          await _refreshSynthCardsFromState(threadId, field);
        }
        _renderSynthLiveProgress(ev.step, ev);
        // Day 5: route to NodeDrawer if open for this synth node.
        if (NodeDrawer.isOpenFor('synth', ev.step)) {
          NodeDrawer.appendEvent(ev);
        }
      }
    };
    es.onerror = () => {
      // SSE auto-reconnects; only act if WE'VE already disconnected.
      if (synthThreadId !== threadId) {
        try { es.close(); } catch (_) {}
      }
    };
  }

  // Per-slug isolation — same key shape as planner, separate namespace.
  function _synthStorageKey(slug) { return 'dd:synth:active:' + slug; }
  const _LAST_SYNTH_SLUG_KEY = 'dd:synth:last_slug';
  function _rememberActiveSynth(slug, tid) {
    try {
      localStorage.setItem(_synthStorageKey(slug), tid);
      localStorage.setItem(_LAST_SYNTH_SLUG_KEY, slug);
    } catch (e) {}
  }
  function _forgetActiveSynth(slug) {
    try { localStorage.removeItem(_synthStorageKey(slug)); } catch (e) {}
  }
  function _genSynthThreadId(slug) {
    const uuid = (typeof crypto !== 'undefined' && crypto.randomUUID)
      ? crypto.randomUUID()
      : 'xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx'.replace(/[xy]/g, c => {
          const r = Math.random() * 16 | 0;
          return (c === 'x' ? r : (r & 0x3 | 0x8)).toString(16);
        });
    return 'docs-distiller-synth/' + slug + '/' + uuid;
  }

  // Page-refresh recovery for synth — symmetric with the planner
  // counterpart. Critical: navigating between slugs only PAINTS cached
  // state; it does NOT trigger compute. Only explicit Start Synth click
  // and recoverActiveSynth (page-load, single slug) call /resume.
  async function _tryResumeActiveSynth(slug) {
    if (!synthCardsEl) return false;
    synthThreadId = null;
    resetSynthCards();
    refreshSynthStartState();

    let tid = null;
    try { tid = localStorage.getItem(_synthStorageKey(slug)); }
    catch (e) { return false; }
    if (!tid) return false;
    try {
      const r = await fetch(API + '/synth/debug/graph/' + tid + '/state');
      if (!r.ok) {
        _forgetActiveSynth(slug);
        return false;
      }
      const data = await r.json();
      const values = data.values || {};
      const status = values.status;
      const allImplDone = _synthAllImplementedComplete(values);
      const effectivelyDone = (
        status === 'failed' || status === 'cancelled' ||
        (status === 'done' && allImplDone) ||
        allImplDone
      );
      if (effectivelyDone) {
        renderSynthCards(values);
        return false;
      }
      synthThreadId = tid;
      refreshSynthStartState();
      renderSynthCards(values);
      _synthLiveEventReceived = false;
      pollSynthState(tid);
      // Orphan auto-/resume — only for 'running' threads with no fresh
      // SSE events arriving within _ORPHAN_DETECT_MS (mirror planner).
      if (status === 'running') {
        setTimeout(async () => {
          if (synthThreadId === tid && !_synthLiveEventReceived) {
            try {
              await fetch(API + '/synth/' + tid + '/resume',
                {method: 'POST'});
            } catch (e) {}
          }
        }, _ORPHAN_DETECT_MS);
      }
      return true;
    } catch (e) {
      _forgetActiveSynth(slug);
      return false;
    }
  }

  // Page-load auto-recovery — mirrors recoverActivePlanner.
  async function recoverActiveSynth() {
    // Don't override planner recovery if it already activated a slug;
    // synth recovery layers on top of an already-active slug context.
    if (!activeSlug) {
      // Try server-side recent list if localStorage is empty.
      let lastSlug = null;
      try { lastSlug = localStorage.getItem(_LAST_SYNTH_SLUG_KEY); }
      catch (e) {}
      const keys = [];
      try {
        for (let i = 0; i < localStorage.length; i++) {
          const k = localStorage.key(i);
          if (k && k.startsWith('dd:synth:active:')) keys.push(k);
        }
      } catch (e) { return; }
      if (!keys.length) {
        try {
          const r = await fetch(API + '/synth/recent');
          if (r.ok) {
            const data = await r.json();
            const recent = (data && data.recent) || [];
            for (const item of recent) {
              try {
                localStorage.setItem(
                  _synthStorageKey(item.slug), item.thread_id,
                );
              } catch (e) {}
            }
            if (recent.length) {
              try {
                localStorage.setItem(_LAST_SYNTH_SLUG_KEY, recent[0].slug);
              } catch (e) {}
            }
          }
        } catch (e) {}
        return;   // recovery handed off to next user action
      }
      // No-op for now — synth recovery activates a slug only if the
      // user hasn't picked one. Planner recovery already covers this
      // path; synth layers via _tryResumeActiveSynth at slug-activation.
    } else {
      // activeSlug already set by planner-recover or user click; just
      // resume any synth thread for it.
      await _tryResumeActiveSynth(activeSlug).catch(() => {});
    }
  }

  async function startSynth() {
    if (!activeSlug || synthThreadId) return;
    // Until any node ships, the POST returns 503 — surface it cleanly
    // as a toast rather than a failed card.
    if (!synthImplemented || !synthImplemented.size) {
      showToast('Synth pipeline not yet implemented. UI is ready; ' +
                'substeps light up as nodes ship.');
      return;
    }
    resetSynthCards();

    // Smart resume — symmetric with planner. If a thread already exists
    // for THIS slug (and only this slug — same per-slug isolation that
    // fixed the planner cascading bug), reuse its thread_id.
    let tid = null;
    let isResume = false;
    try {
      const r = await fetch(API + '/synth/recent');
      if (r.ok) {
        const data = await r.json();
        const found = ((data && data.recent) || [])
          .find(item => item.slug === activeSlug);
        if (found && found.thread_id) {
          tid = found.thread_id;
          isResume = true;
        }
      }
    } catch (e) {}

    if (!tid) tid = _genSynthThreadId(activeSlug);
    synthThreadId = tid;
    _rememberActiveSynth(activeSlug, tid);
    refreshSynthStartState();
    pollSynthState(tid);

    try {
      const budget = (synthBudgetSel && synthBudgetSel.value) || '5';
      const url = isResume
        ? API + '/synth/' + tid + '/resume'
        : API + '/synth/' + activeSlug +
          '?mode=quality' +
          '&thread_id=' + encodeURIComponent(tid) +
          '&budget=' + encodeURIComponent(budget);
      const r = await fetch(url, {method: 'POST'});
      if (!r.ok) {
        const txt = await r.text();
        markSynthFailed('HTTP ' + r.status + ': ' + txt.slice(0, 400));
        synthThreadId = null;
        refreshSynthStartState();
        return;
      }
      await r.json();
    } catch (e) {
      markSynthFailed('Request failed: ' + String(e));
      synthThreadId = null;
      refreshSynthStartState();
    }
  }

  async function cancelSynth() {
    if (!synthThreadId) return;
    const tid = synthThreadId;
    synthStartBtn.setAttribute('disabled', 'disabled');
    synthStartBtn.innerHTML =
      '<div class="fw-spinner" style="display:inline-block;' +
      'vertical-align:middle;margin-right:8px"></div>Cancelling…';
    try {
      await fetch(API + '/synth/' + tid + '/cancel', {method: 'POST'});
    } catch (e) {
      synthStartBtn.removeAttribute('disabled');
      synthStartBtn.innerHTML = 'Cancel Synth';
      showToast('Cancel request failed: ' + String(e));
    }
  }

  async function wipeSynth(slug) {
    if (!slug) return {error: 'no slug'};
    let result = {};
    try {
      const r = await fetch(API + '/synth/' + slug + '/wipe',
        {method: 'DELETE'});
      result = r.ok ? (await r.json()) : {http_status: r.status};
    } catch (e) { result = {error: String(e)}; }
    _forgetActiveSynth(slug);
    if (activeSlug === slug) {
      synthThreadId = null;
      resetSynthCards();
      refreshSynthStartState();
    }
    console.log('[ddWipeSynth]', slug, result);
    return result;
  }
  window.ddWipeSynth = wipeSynth;

  if (synthStartBtn) {
    synthStartBtn.addEventListener('click', () => {
      if (synthThreadId) cancelSynth();
      else startSynth();
    });
  }
  if (synthWipeBtn) {
    synthWipeBtn.addEventListener('click', async () => {
      if (!activeSlug || synthThreadId) return;
      const ok = await showConfirm(
        'Wipe synth cache for ' + activeSlug + '?',
        ('Deletes MinIO chapter artifacts + Postgres checkpoints + ' +
         'browser state for ' + activeSlug +
         '. Planner cache is untouched. This cannot be undone.'),
        'Wipe',
      );
      if (!ok) return;
      const result = await wipeSynth(activeSlug);
      if (result && result.error) {
        showToast('Wipe failed: ' + result.error);
      } else if (result && result.http_status) {
        showToast('Wipe failed: HTTP ' + result.http_status);
      } else {
        showToast('Synth cache wiped for ' + activeSlug + '.');
      }
    });
  }

  async function loadSynthInfo() {
    try {
      const r = await fetch(API + '/synth/info');
      if (!r.ok) return;
      const data = await r.json();
      synthImplemented = new Set(data.implemented || []);
      // Hydrate the budget dropdown from server modes if provided
      // (currently a 3-option static list; left here for future
      // server-driven extension symmetric with the planner mode pattern).
      // Re-render to convert any IMPLEMENTED entries from "future" to
      // "pending" (○) — same pattern as plannerImplemented.
      renderSynthCards({});
      refreshSynthStartState();
    } catch (e) { /* silent — defaults to all "future" */ }
  }
})();
