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
  // Canvas is now the ONLY render path (cards removed 2026-05-19).
  // The DAG canvas is strictly better for this domain — one-click
  // node inspection via NodeDrawer, visual stage ordering, KPI
  // badges per node, live SSE-driven coloring.
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
  // Always 'graph' since cards DOM no longer exists. Kept as a named
  // constant so the legacy `if (UI_MODE === 'graph')` guards stay
  // readable as "the canvas path" without renaming N call sites.
  const UI_MODE = 'graph';

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

    // reset() — clear in-flight events + DOM log without closing the
    // drawer. Used when the study orchestrator advances to the next
    // chapter so an already-open drawer doesn't keep stale events from
    // the previous chapter's run of the same node.
    function reset() {
      _pendingEvents = [];
      if (elLog) {
        while (elLog.firstChild) elLog.removeChild(elLog.firstChild);
      }
      if (elLogEmpty) elLogEmpty.style.display = '';
      _lastSeenAt.clear();
      _prevSeenForOpen = 0;
    }

    return { open, close, reset, isOpenFor, appendEvent, updateContext,
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
          'canvas unavailable. Reload the page to retry.',
        );
        // No cards fallback anymore (cards DOM was removed 2026-05-19).
        // Surface an in-place error so the user knows what happened
        // instead of staring at an empty pane.
        const canvasEl = document.getElementById('fw-planner-canvas');
        if (canvasEl) {
          canvasEl.innerHTML =
            '<div class="fw-empty">Cytoscape failed to load. ' +
            'Reload the page; if it persists, check the network panel ' +
            'for blocked /static/vendor/cytoscape.min.js.</div>';
        }
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
    // Steps 2-5 unlock when EITHER an ingestion is running OR the library
    // has at least one finalized framework. Otherwise lock back to Step 1.
    // Study (5) is included so it's clickable while Synth runs — it shows
    // its own empty-state until chapters render, then populates live. (It
    // used to cap at 4, which left Study "completely blocked" during a run
    // even though the user wants to peek at it.)
    const hasLibrary =
      sidebarList.querySelectorAll('.fw-lib-item').length > 0;
    const ingestActive = activeRunId !== null;
    if (hasLibrary || ingestActive) {
      farthestStep = Math.max(farthestStep, 5);
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
    // If the user switches frameworks while ALREADY on the Study stage,
    // the showStep(5) navigation hook won't fire — so refresh the Study
    // view in place. Without this, picking a framework on Step 5 left the
    // stale "Pick a framework with synthesized chapters" placeholder.
    if (currentStep === 5) {
      setStudyFramework(slug);
      refreshStudyVisibility();
      if (slug !== studyLoadedSlug) loadStudyChapters(slug);
    }
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
  // {stage} pipeline" placeholder for a stage panel. Single source of
  // truth for graph-wrapper visibility — canvas init MUST NOT touch
  // it directly or it races this toggle. On reveal, kicks a Cytoscape
  // resize so the canvas picks up freshly-visible container dimensions
  // (otherwise the graph latches 0×0 from when it was hidden).
  function _toggleStageEmpty(stage, showEmpty) {
    const emptyEl  = document.getElementById('fw-' + stage + '-empty');
    const graphEl  = document.getElementById('fw-' + stage + '-graph');
    if (!emptyEl) return;
    if (showEmpty) {
      emptyEl.style.display = '';
      if (graphEl) graphEl.style.display = 'none';
    } else {
      emptyEl.style.display = 'none';
      if (graphEl) graphEl.style.display = 'flex';
      // Re-fit Cytoscape now that the wrapper has real dimensions.
      if (stage === 'planner' && plannerGraph) _resizePlannerCanvas();
      if (stage === 'synth'   && synthGraph)   _resizeSynthCanvas();
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
    // Cards DOM removed 2026-05-19. Always null in the new graph-only
    // UI; the cards-rendering loops short-circuit cleanly via
    // `if (!c) continue;` while still calling `_renderPlannerGraph`
    // + `_refreshOpenPlannerDrawer` at the tail.
    if (!plannerCardsEl) return null;
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
      const present = _fieldPresent(values, field);
      if (!c) {
        // Cards DOM removed 2026-05-19. Still count done so the
        // tail-end `_renderPlannerGraph` has accurate progress.
        if (present) doneCount++;
        continue;
      }
      const icon = c.querySelector('.fw-planner-card-icon');
      const body = c.querySelector('.fw-planner-card-body');
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

  // Card-head click → toggle expanded body (legacy cards-mode handler).
  // Cards DOM was removed 2026-05-19 — `plannerCardsEl` is null in the
  // graph-only UI, so the handler is registered conditionally. The
  // off_topic verdict-table sort branch lived inside this handler too;
  // it now activates only when the planner drawer renders that table
  // (handled by SUBSTEP_RENDERERS[2] inside the drawer details panel,
  // which has its own delegate).
  if (plannerCardsEl) plannerCardsEl.addEventListener('click', ev => {
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

  // NOTE: synth-cards click-to-expand handler is registered LATER in
  // the IIFE (after `synthCardsEl` is declared at ~line 3504). Placing
  // it here previously hit a Temporal Dead Zone error (const is not
  // hoisted) which crashed the IIFE on load — silently breaking
  // loadLibrary() and every other init step.

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
        // Don't clobber a higher farthestStep (was a hard `= 4`, which
        // re-locked Study when re-selecting a framework). Unlock through
        // Study (5) so it's reachable; it shows empty until chapters render.
        farthestStep = Math.max(farthestStep, 5);
        // Restore the synth view for this slug — recovers an in-flight
        // study's strip OR rebuilds it from durable render status so the
        // chapter box survives a refresh on this path too.
        _tryResumeActiveSynth(slug).catch(() => {});
        showStep(4);   // jump to Synth stage
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
        // user's current step. All 5 steps stay reachable for the
        // newly-selected slug; Study (5) shows its own empty-state until
        // that framework has rendered chapters.
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

  // Synth-cards click-to-expand — mirrors the planner handler at line
  // 3113. Without this, .fw-planner-card-body stays display:none and
  // the live progress text (written by _renderSynthLiveProgress into
  // .fw-planner-card-live inside the card body) is invisible during a
  // run. Registered here (right after synthCardsEl is declared) so the
  // reference doesn't hit a Temporal Dead Zone — an earlier draft put
  // it next to the planner handler at line 3113, which crashed the
  // whole IIFE on load and silently broke loadLibrary() + every other
  // init step.
  if (synthCardsEl) {
    synthCardsEl.addEventListener('click', ev => {
      const head = ev.target.closest('.fw-planner-card-head');
      if (!head) return;
      head.parentElement.classList.toggle('expanded');
    });
  }

  // Substep order MUST match `NODE_ORDER` in
  // services/docs_distiller/synth/graph.py (when that ships) AND the
  // field each node writes (`state.<field>`).
  // Field names are TENTATIVE — they're placeholders that match the
  // SOTA architecture doc. Update when the real graph.py lands.
  // `corpus_normalize` + `vault_sentinelize` removed from this canvas
  // 2026-05-19 — both ARE shipped but execute at INGESTION time, NOT
  // synth-time. The canvas's mental model is "what runs when Start
  // Synth is clicked"; ingestion-prep doesn't belong here.
  // `cache_lookup` also removed — per-stage MinIO caches + LangGraph
  // native skip-completed-nodes subsume it. See SYNTH-ARCHITECTURE-SOTA
  // doc for the full rationale.
  const SYNTH_SUBSTEP_FIELDS = [
    'outline_path',          // outline_sdp
    'digest_path',           // digest_construct
    'sawc_path',             // sawc_write
    'checklist_path',        // checklist_eval
    'mgsr_path',             // mgsr_replan
    'chapter_path',          // render_audit_write
  ];
  const SYNTH_NODE_ORDER = [
    'outline_sdp', 'digest_construct',
    'sawc_write', 'checklist_eval',
    'mgsr_replan', 'render_audit_write',
  ];
  const SYNTH_NODE_LABELS = [
    'Outline (SDP)', 'Digest',
    'SAWC write', 'Checklist eval',
    'MGSR replan', 'Render + audit',
  ];
  const SYNTH_STEP_TO_FIELD = {
    outline_sdp:        'outline_path',
    digest_construct:   'digest_path',
    sawc_write:         'sawc_path',
    checklist_eval:     'checklist_path',
    mgsr_replan:        'mgsr_path',
    render_audit_write: 'chapter_path',
  };

  // Populated from GET /synth/info. Cards whose substep isn't in this
  // set stay "⏳ future" — same pattern as plannerImplemented.
  let synthImplemented = new Set();
  let synthThreadId = null;
  let _synthLiveEventReceived = false;
  let synthPollAbort = false;

  // Study-mode state — when Start Synth is clicked without picking a
  // specific chapter, the backend spawns the orchestrator and returns a
  // study_thread_id. We subscribe to that channel for orchestrator events
  // (study_start → chapter_running → chapter_done × N → study_done) and
  // ALSO open per-chapter SSE for substep-level progress on the Cytoscape
  // canvas. The chapter progress strip above the canvas mirrors the
  // orchestrator's view of where the run is.
  let studyThreadId = null;
  let studyChapterIds = [];        // ordered chapter ids for current study
  let studyChapterStatus = new Map();  // id → pending|running|done|failed|cancelled
  let studyCurrentChapterId = null;
  let studyCurrentChapterThreadId = null;  // chapter_thread_id of last chapter_running
  // chapter_id → chapter_thread_id (populated lazily as chapter_running events
  // arrive, both live and via snapshot replay). Used by the strip-click handler
  // to swap the canvas to a user-selected chapter.
  let studyChapterThreads = new Map();
  // When non-null, the user has clicked a specific chapter cell — the canvas
  // is "pinned" to that chapter and should NOT auto-swap when the orchestrator
  // advances to the next one. Click the currently-running cell to unpin.
  let studyPinnedChapterId = null;

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
      case 'outline_sdp':        {
        const s = stats('outline_stats');
        if (!s) return '';
        const parts = [];
        if (s.n_sections   !== undefined) parts.push(`sec=${s.n_sections}`);
        if (s.max_stage    !== undefined) parts.push(`depth=${s.max_stage}`);
        if (s.n_violations !== undefined) parts.push(`viol=${s.n_violations}`);
        return parts.join(' · ');
      }
      case 'digest_construct':   {
        const s = stats('digest_stats');
        if (!s) return '';
        const parts = [];
        if (s.n_sources !== undefined) parts.push(`src=${s.n_sources}`);
        if (s.n_sections !== undefined &&
            s.n_sections_covered !== undefined) {
          parts.push(`cov=${s.n_sections_covered}/${s.n_sections}`);
        }
        if (s.n_orphan_code_refs !== undefined) {
          parts.push(`orph=${s.n_orphan_code_refs}`);
        }
        if (s.n_empty_sections) parts.push(`empty=${s.n_empty_sections}`);
        return parts.join(' · ');
      }
      case 'sawc_write':         {
        const s = stats('sawc_stats');
        if (!s) return '';
        const parts = [];
        if (s.n_sections !== undefined && s.n_completed !== undefined) {
          parts.push(`sec=${s.n_completed}/${s.n_sections}`);
        }
        if (s.n_fallback) parts.push(`fb=${s.n_fallback}`);
        if (s.n_repairs) parts.push(`rep=${s.n_repairs}`);
        if (s.n_picker_fallbacks) {
          parts.push(`pfb=${s.n_picker_fallbacks}`);
        }
        return parts.join(' · ');
      }
      case 'checklist_eval':     {
        const s = stats('checklist_stats');
        if (!s) return '';
        const parts = [];
        if (s.n_total !== undefined && s.n_passed !== undefined) {
          parts.push(`pass=${s.n_passed}/${s.n_total}`);
        }
        if (s.pass_rate !== undefined) {
          parts.push(`rate=${(s.pass_rate * 100).toFixed(0)}%`);
        }
        if (s.chapter_passed === true)  parts.push('✓');
        if (s.chapter_passed === false) parts.push('✗');
        if (s.n_failed_feedback) parts.push(`fb=${s.n_failed_feedback}`);
        return parts.join(' · ');
      }
      case 'mgsr_replan':        {
        const s = stats('mgsr_stats');
        if (!s) return '';
        const parts = [];
        if (s.halt !== undefined) {
          parts.push(s.halt ? '✓halt' : '↻loop');
        }
        if (s.halt_reason) parts.push(s.halt_reason);
        if (s.n_actions !== undefined) parts.push(`act=${s.n_actions}`);
        if (s.confidence !== undefined) {
          parts.push(`conf=${(s.confidence * 100).toFixed(0)}%`);
        }
        return parts.join(' · ');
      }
      case 'render_audit_write': {
        const s = stats('chapter_stats');
        if (!s) return '';
        const parts = [];
        if (s.audit_passed === true)  parts.push('audit=✓');
        if (s.audit_passed === false) parts.push('audit=✗');
        if (s.n_artifacts !== undefined) parts.push(`arts=${s.n_artifacts}`);
        if (s.n_code_refs !== undefined && s.n_resolved !== undefined &&
            s.n_code_refs > 0) {
          parts.push(`refs=${s.n_resolved}/${s.n_code_refs}`);
        }
        if (s.n_missing) parts.push(`miss=${s.n_missing}`);
        if (s.n_byte_drift) parts.push(`drift=${s.n_byte_drift}`);
        if (s.rendered_chars) {
          parts.push(`${(s.rendered_chars / 1000).toFixed(1)}k`);
        }
        return parts.join(' · ');
      }
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
      // KPI text format is `k1=v1 · k2=v2 · k3=v3` (space-dot-space
      // separator). Older code only grabbed the first `k=v` because it
      // split on the FIRST `=` for the whole string, dropping multi-key
      // KPIs. Split on the separator first, then on `=` per pair.
      kpiText.split(' · ').forEach(pair => {
        const eqIdx = pair.indexOf('=');
        if (eqIdx > 0) {
          kpis[pair.slice(0, eqIdx).trim()] = pair.slice(eqIdx + 1).trim();
        }
      });
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

  // In-memory event buffer keyed by step name. The SSE handler in
  // pollSynthState pushes every event here AS IT ARRIVES, regardless of
  // whether the drawer is currently open. When the user opens the
  // drawer for `outline_sdp` mid-run (or after the run finishes), we
  // replay the buffered events into the drawer log so they see the
  // full activity history — not just events that fire AFTER the drawer
  // open. Without this, the long silent windows between SDP events
  // (~28s while 3 LLM samples generate concurrently) made the drawer
  // look empty even though the run was making progress.
  // Capped per-step to avoid unbounded growth on very long runs.
  const _synthEventBuffer = new Map();   // step → Array<event>
  const _SYNTH_EVENT_BUFFER_PER_STEP = 200;

  function _bufferSynthEvent(ev) {
    if (!ev || !ev.step) return;
    let list = _synthEventBuffer.get(ev.step);
    if (!list) { list = []; _synthEventBuffer.set(ev.step, list); }
    list.push(ev);
    if (list.length > _SYNTH_EVENT_BUFFER_PER_STEP) {
      list.splice(0, list.length - _SYNTH_EVENT_BUFFER_PER_STEP);
    }
  }

  function _resetSynthEventBuffer() {
    _synthEventBuffer.clear();
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
    // Replay buffered events for this node so a late-open drawer sees
    // the full event history, not just future events.
    const buffered = _synthEventBuffer.get(nodeId) || [];
    if (buffered.length) {
      for (const ev of buffered) NodeDrawer.appendEvent(ev);
    }
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
        console.warn(
          '[synthGraph] Cytoscape failed to load within 5s — ' +
          'canvas unavailable. Reload the page to retry.',
        );
        // No cards fallback anymore (removed 2026-05-19). Same in-place
        // error shape as the planner-side handler above.
        const synthCanvasEl = document.getElementById('fw-synth-canvas');
        if (synthCanvasEl) {
          synthCanvasEl.innerHTML =
            '<div class="fw-empty">Cytoscape failed to load. ' +
            'Reload the page; if it persists, check the network panel ' +
            'for blocked /static/vendor/cytoscape.min.js.</div>';
        }
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
    // minimum.
    if (ev.kind === 'start')      text = '· starting ' + stepName + '…';
    else if (ev.kind === 'done')  text = '✓ done (' + (ev.wall_ms || 0) + ' ms)';
    else if (ev.kind === 'error') text = '✕ ' + (ev.error || 'failed');
    // outline_sdp — SurveyGen-I SDP per-event progress
    if (stepName === 'outline_sdp') {
      if (ev.kind === 'start') {
        text = '· loading sources for ' + (ev.chapter_title || ev.chapter_id || 'chapter') +
               ' (' + (ev.n_sources || 0) + ' sources)';
      } else if (ev.kind === 'sources_loaded') {
        text = '· sources loaded: ' + (ev.n_bodies || 0) + '/' + (ev.n_sources || 0) +
               ' bodies, ' + ((ev.bytes || 0) / 1000).toFixed(1) + 'k chars, ' +
               (ev.n_vault_hashes || 0) + ' code refs' +
               (ev.truncated ? ' (truncated)' : '');
      } else if (ev.kind === 'sample_done') {
        // Per-sample event (one per concurrent LLM draft). `sample_idx`
        // is 0-based; show 1-based for the user.
        const idx = (ev.sample_idx ?? 0) + 1;
        const tot = ev.n_total || 0;
        const dep = ev.deployment ? ' [' + ev.deployment + ']' : '';
        if (ev.ok) {
          text = '· sample ' + idx + '/' + tot + ' done (' +
                 (ev.n_sections || '?') + ' sections, ' +
                 (ev.wall_ms || 0) + ' ms)' + dep;
        } else {
          text = '· sample ' + idx + '/' + tot + ' FAILED: ' +
                 (ev.error || 'unknown');
        }
      } else if (ev.kind === 'samples_drafted') {
        text = '· drafted ' + (ev.n_samples || 0) + '/' +
               (ev.n_requested || 0) + ' candidate outlines';
      } else if (ev.kind === 'samples_validated') {
        text = '· validated ' + (ev.n_candidates || 0) + ' candidate(s)' +
               (ev.n_pydantic_fail ? ', ' + ev.n_pydantic_fail + ' pydantic-rejected' : '');
      } else if (ev.kind === 'usc_voted') {
        text = '· USC picked candidate #' + (ev.chosen_index || 0) +
               ' (' + (ev.n_initial_violations || 0) + ' initial violations)';
      } else if (ev.kind === 'repair_attempt') {
        text = '· repair attempt ' + (ev.attempt || 0) +
               ' (' + (ev.n_violations || 0) + ' violations)';
      } else if (ev.kind === 'done') {
        text = '✓ done — ' + (ev.n_sections || 0) + ' sections, ' +
               'depth=' + (ev.max_stage || 0) + ', ' +
               'repairs=' + (ev.n_repairs || 0) + ', ' +
               'violations=' + (ev.n_violations || 0) +
               ' (' + (ev.wall_ms || 0) + ' ms)';
      }
    }
    // digest_construct — per-source LLM-assigned routing (LLMxMapReduce-V3
    // pattern). N parallel source digests with one `source_done` event per
    // completion, plus lifecycle events.
    if (stepName === 'digest_construct') {
      if (ev.kind === 'start') {
        text = '· starting digests for ' + (ev.chapter_title || ev.chapter_id || 'chapter') +
               ' (' + (ev.n_sources || 0) + ' sources × ' +
               (ev.n_sections || 0) + ' sections)';
      } else if (ev.kind === 'outline_loaded') {
        text = '· outline loaded: ' + (ev.n_sources || 0) + ' source(s), ' +
               (ev.n_total_vault_hashes || 0) + ' code refs, ' +
               (((ev.total_bytes || 0) / 1000).toFixed(1)) + 'k chars';
      } else if (ev.kind === 'source_done') {
        const idx = (ev.sample_idx ?? 0) + 1;
        const tot = ev.n_total || 0;
        const dep = ev.deployment ? ' [' + ev.deployment + ']' : '';
        const src = (ev.source_key || '').split('/').pop();
        if (ev.ok) {
          text = '· source ' + idx + '/' + tot + ' done · ' + src + ' · ' +
                 (ev.n_contributions || 0) + ' contribs, ' +
                 (ev.wall_ms || 0) + ' ms' + dep;
        } else {
          text = '· source ' + idx + '/' + tot + ' FAILED · ' + src +
                 ': ' + (ev.error || 'unknown');
        }
      } else if (ev.kind === 'digests_aggregated') {
        text = '· aggregated ' + (ev.n_digests_ok || 0) + '/' +
               (ev.n_total || 0) + ' digests' +
               (ev.n_pydantic_fail
                  ? ', ' + ev.n_pydantic_fail + ' pydantic-rejected'
                  : '');
      } else if (ev.kind === 'done') {
        text = '✓ done — ' + (ev.n_sources || 0) + ' sources, ' +
               'cov=' + (ev.n_sections_covered || 0) + '/' +
               (ev.n_sections || 0) + ', ' +
               'empty=' + (ev.n_empty_sections || 0) + ', ' +
               'orph=' + (ev.n_orphan_code_refs || 0) +
               ' (' + (ev.wall_ms || 0) + ' ms)';
      }
    }
    // sawc_write — Structure-Aware Writing Controller (SurveyGen-I §3.2
    // + MAMM-Refine). Stage-parallel; N=3 best-of-N per section; per-
    // section critic-pick. Emits 6 event kinds so the live progress
    // stream has steady cadence across the stage loop.
    if (stepName === 'sawc_write') {
      if (ev.kind === 'start') {
        text = '· starting writes for ' + (ev.chapter_title || ev.chapter_id || 'chapter') +
               ' (' + (ev.n_sections || 0) + ' sections × 3 drafts = ' +
               (ev.n_total_drafts || 0) + ' draft calls + critic picks across ' +
               (ev.n_stages || 0) + ' stages)';
      } else if (ev.kind === 'stage_start') {
        const sids = (ev.section_ids || []).join(', ');
        text = '· stage ' + (ev.stage_idx ?? '?') + ' starting (' +
               (ev.n_sections_in_stage || 0) + ' sections in parallel: ' +
               sids + ')';
      } else if (ev.kind === 'section_draft_done') {
        const di = (ev.draft_idx ?? 0) + 1;
        const tot = ev.n_total || 3;
        const sid = ev.section_id || '?';
        const dep = ev.deployment ? ' [' + ev.deployment + ']' : '';
        if (ev.ok) {
          text = '· ' + sid + ' draft ' + di + '/' + tot + ' done · ' +
                 (ev.n_paragraphs || 0) + ' paras, ' +
                 (ev.n_citations || 0) + ' cites, ' +
                 (ev.wall_ms || 0) + ' ms' +
                 (ev.n_violations ? ', ' + ev.n_violations + ' viol' : '') + dep;
        } else {
          text = '· ' + sid + ' draft ' + di + '/' + tot + ' FAILED: ' +
                 (ev.error || 'unknown');
        }
      } else if (ev.kind === 'section_picked') {
        const sid = ev.section_id || '?';
        const fb = ev.fallback ? ' [fallback=' + ev.fallback + ']' : '';
        const dep = ev.deployment_critic ? ' [' + ev.deployment_critic + ']' : '';
        if (ev.chosen_idx === -1) {
          text = '· ' + sid + ' all 3 drafts failed → placeholder';
        } else {
          text = '· ' + sid + ' picked draft ' + ev.chosen_idx +
                 ' (score=' + (ev.structural_score || 0).toFixed(2) +
                 (ev.n_violations ? ', ' + ev.n_violations + ' viol' : '') +
                 ')' + fb + dep;
        }
      } else if (ev.kind === 'section_done') {
        const sid = ev.section_id || '?';
        const fb = ev.fallback ? ' [' + ev.fallback + ']' : '';
        text = '· ' + sid + ' written — ' + (ev.n_paragraphs || 0) + ' paras, ' +
               (ev.n_code_refs || 0) + ' refs, ' +
               (ev.n_citations || 0) + ' cites, ' +
               ((ev.total_chars || 0) / 1000).toFixed(1) + 'k chars, ' +
               (ev.wall_ms || 0) + ' ms' + fb;
      } else if (ev.kind === 'stage_done') {
        text = '✓ stage ' + (ev.stage_idx ?? '?') + ' complete: ' +
               (ev.n_completed || 0) + ' sections written, ' +
               (ev.n_failed || 0) + ' failed (' +
               (ev.wall_ms || 0) + ' ms)';
      } else if (ev.kind === 'done') {
        text = '✓ done — ' + (ev.n_completed || 0) + '/' +
               (ev.n_sections || 0) + ' sections, ' +
               (ev.n_fallback || 0) + ' fallbacks, ' +
               (ev.n_repairs || 0) + ' repairs, ' +
               (ev.total_drafts_fired || 0) + ' drafts fired' +
               ' (' + (ev.wall_ms || 0) + ' ms)';
      }
    }
    // checklist_eval — 12 binary criteria (7 deterministic pre-gates +
    // 5 LLM-judge). Fast node (1 LLM call total). Emits 4 event kinds.
    if (stepName === 'checklist_eval') {
      if (ev.kind === 'start') {
        text = '· starting checklist for ' + (ev.chapter_title || ev.chapter_id || 'chapter') +
               ' (' + (ev.n_total_criteria || 0) + ' criteria, threshold ' +
               ((ev.pass_threshold || 0.8) * 100).toFixed(0) + '%)';
      } else if (ev.kind === 'pregates_done') {
        const failed = ev.names_failed || [];
        text = '· pre-gates: ' + (ev.n_passed || 0) + '/' +
               (ev.n_pregate || 0) + ' passed' +
               (failed.length
                  ? ' · failed: ' + failed.slice(0, 3).join(', ') +
                    (failed.length > 3 ? ` (+${failed.length - 3})` : '')
                  : '');
      } else if (ev.kind === 'judge_request') {
        text = '· LLM judge: dispatching (' +
               ((ev.chapter_chars || 0) / 1000).toFixed(1) + 'k chars chapter' +
               (ev.truncated ? ', truncated' : '') + ')…';
      } else if (ev.kind === 'judge_done') {
        const failed = ev.names_failed || [];
        const dep = ev.deployment ? ' [' + ev.deployment + ']' : '';
        const rep = ev.repaired ? ' (repaired)' : '';
        text = '· LLM judge done: ' + (ev.n_passed || 0) + '/' +
               (ev.n_llm || 0) + ' passed' + rep +
               (failed.length
                  ? ' · failed: ' + failed.slice(0, 3).join(', ') +
                    (failed.length > 3 ? ` (+${failed.length - 3})` : '')
                  : '') +
               ' (' + (ev.wall_ms || 0) + ' ms)' + dep;
      } else if (ev.kind === 'done') {
        const passMark = ev.chapter_passed ? '✓ PASSED' : '✗ FAILED';
        text = '✓ done — ' + passMark + ' — ' +
               (ev.n_passed || 0) + '/' + (ev.n_total || 0) +
               ' criteria (' + ((ev.pass_rate || 0) * 100).toFixed(0) + '%), ' +
               (ev.n_failed_feedback || 0) + ' feedback notes' +
               ' (' + (ev.wall_ms || 0) + ' ms)';
      }
    }
    // render_audit_write — Final node. Zero LLM calls. Renders 3
    // artifacts (README.md, challenges.md, flashcards.json) via Jinja2
    // + runs SHA-256 round-trip audit on code refs. 5 event kinds.
    if (stepName === 'render_audit_write') {
      if (ev.kind === 'start') {
        text = '· starting render for ' + (ev.chapter_title || ev.chapter_id || 'chapter') +
               ' (' + (ev.n_sections || 0) + ' sections, ' +
               (ev.n_challenges || 0) + ' challenges, ' +
               (ev.n_flashcards || 0) + ' flashcards · mgsr ' +
               (ev.mgsr_halt_reason || '?') + ')';
      } else if (ev.kind === 'inputs_loaded') {
        text = '· vaults loaded: ' + (ev.n_vault_files_loaded || 0) + '/' +
               (ev.n_sources || 0) + ' source vaults' +
               (ev.n_vault_files_skipped
                 ? ', ' + ev.n_vault_files_skipped + ' skipped'
                 : '') +
               ' · ' + (ev.n_vault_entries || 0) + ' total vault entries';
      } else if (ev.kind === 'rendered') {
        const auditMark = ev.audit_passed ? '✓' : '✗';
        text = '· rendered chapter (' +
               ((ev.chapter_chars || 0) / 1000).toFixed(1) + 'k chars, ' +
               (ev.n_sections_rendered || 0) + ' sections) · ' +
               'audit=' + auditMark + ' refs=' +
               (ev.n_code_refs_resolved || 0) + '/' +
               ((ev.n_code_refs_resolved || 0) +
                (ev.n_code_refs_missing || 0)) +
               (ev.n_code_refs_missing
                 ? ' · miss=' + ev.n_code_refs_missing : '') +
               (ev.n_code_refs_drift
                 ? ' · drift=' + ev.n_code_refs_drift : '') +
               (ev.sentinels_in_output
                 ? ' · sent=' + ev.sentinels_in_output : '');
      } else if (ev.kind === 'artifacts_written') {
        const names = (ev.artifact_names || []).join(', ');
        text = '· wrote ' + (ev.n_artifacts || 0) + ' artifacts (' +
               ((ev.total_bytes || 0) / 1000).toFixed(1) + 'k bytes total) — ' +
               names;
      } else if (ev.kind === 'done') {
        const mark = ev.audit_passed ? '✓ AUDIT PASSED' : '✗ AUDIT FAILED';
        text = '✓ done — ' + mark + ' · ' +
               (ev.n_artifacts || 0) + ' artifacts, ' +
               ((ev.rendered_chars || 0) / 1000).toFixed(1) + 'k chars rendered' +
               (ev.n_missing ? ' · ' + ev.n_missing + ' missing refs' : '') +
               (ev.n_byte_drift ? ' · ' + ev.n_byte_drift + ' drift' : '') +
               (ev.sentinels_in_output
                 ? ' · ' + ev.sentinels_in_output + ' unresolved sentinels'
                 : '') +
               ' (' + (ev.wall_ms || 0) + ' ms)';
      }
    }
    // mgsr_replan — Memory-Guided Structure Replanner (SurveyGen-I +
    // CoRefine). Fast path = trivial_pass (no LLM call) when chapter
    // already passed checklist. Slow path = 1 LLM call emitting typed
    // replan actions on the outline DAG. 5 event kinds.
    if (stepName === 'mgsr_replan') {
      if (ev.kind === 'start') {
        const fmtRate = ((ev.pass_rate || 0) * 100).toFixed(0);
        text = '· starting replan for ' + (ev.chapter_title || ev.chapter_id || 'chapter') +
               ' (pass=' + fmtRate + '%, ' +
               (ev.n_failed_criteria || 0) + ' failed criteria)';
      } else if (ev.kind === 'trivial_pass') {
        text = '· chapter already passed (' +
               ((ev.pass_rate || 0) * 100).toFixed(0) +
               '%) — halting trivially, no LLM call';
      } else if (ev.kind === 'llm_request') {
        text = '· LLM replan: dispatching (' +
               (ev.n_failed_criteria || 0) + ' failed criteria)…';
      } else if (ev.kind === 'llm_done') {
        const dep = ev.deployment ? ' [' + ev.deployment + ']' : '';
        const rep = ev.repaired ? ' (repaired)' : '';
        const halt = ev.halt ? 'halt' : 'continue';
        if (ev.error) {
          text = '· LLM replan FAILED — fallback halt (' +
                 (ev.wall_ms || 0) + ' ms)';
        } else {
          text = '· LLM replan done: ' + halt + ', ' +
                 (ev.n_actions || 0) + ' actions, conf=' +
                 ((ev.confidence || 0) * 100).toFixed(0) + '%' +
                 rep + ' (' + (ev.wall_ms || 0) + ' ms)' + dep;
        }
      } else if (ev.kind === 'done') {
        const mark = ev.halt ? '✓ HALT' : '↻ LOOP';
        text = '✓ done — ' + mark + ' (' + (ev.halt_reason || '?') + '), ' +
               (ev.n_actions || 0) + ' actions, conf=' +
               ((ev.confidence || 0) * 100).toFixed(0) + '%' +
               ' (' + (ev.wall_ms || 0) + ' ms)';
      }
    }
    if (text) el.textContent = text;
  }

  function renderSynthCards(values) {
    // Cards DOM was removed 2026-05-19 — synthCardsEl is null. The
    // per-card loop below now early-skips at `if (!c) continue;` but
    // `_renderSynthGraph` + `_refreshOpenSynthDrawer` at the tail MUST
    // still fire (they own the graph-canvas + drawer state). Previous
    // `if (!synthCardsEl) return;` short-circuit silently broke them.
    let doneCount = 0;
    for (let i = 0; i < SYNTH_SUBSTEP_FIELDS.length; i++) {
      const field = SYNTH_SUBSTEP_FIELDS[i];
      const c = synthCardEl(i);
      if (!c) {
        // Without cards we can't count done state from the DOM, so
        // derive it from values directly to keep the "first not-done
        // → running" canvas logic intact.
        if (_synthFieldPresent(values, field)) doneCount++;
        continue;
      }
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
    const running = synthThreadId !== null || studyThreadId !== null;
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

  // ──────────────────────────────────────────────────────────────────
  // Chapter progress strip — visible only during STUDY-mode runs.
  // ──────────────────────────────────────────────────────────────────
  const chstripEl       = document.querySelector('#fw-chstrip');
  const chstripCellsEl  = document.querySelector('#fw-chstrip-cells');
  const chstripCounterEl = document.querySelector('#fw-chstrip-counter');

  function _showChStrip(visible) {
    if (!chstripEl) return;
    chstripEl.classList.toggle('visible', !!visible);
  }
  function _renderChStrip(chapterIds) {
    if (!chstripCellsEl) return;
    studyChapterIds = chapterIds.slice();
    studyChapterStatus = new Map(chapterIds.map(id => [id, 'pending']));
    studyCurrentChapterId = null;
    chstripCellsEl.innerHTML = chapterIds.map(id => (
      '<div class="fw-chstrip-cell" data-status="pending" ' +
      'data-chapter-id="' + id.replace(/"/g, '&quot;') + '">' +
      '  <span class="icon"></span>' +
      '  <span class="label">' + id + '</span>' +
      '</div>'
    )).join('');
    _updateChStripCounter();
  }
  function _markChStripCell(chapterId, status) {
    if (!chstripCellsEl) return;
    studyChapterStatus.set(chapterId, status);
    const cell = chstripCellsEl.querySelector(
      '.fw-chstrip-cell[data-chapter-id="' + chapterId.replace(/"/g, '\\"') + '"]'
    );
    if (cell) cell.dataset.status = status;
    _updateChStripCounter();
  }
  function _updateChStripCounter() {
    if (!chstripCounterEl) return;
    let done = 0, failed = 0, total = studyChapterIds.length;
    for (const s of studyChapterStatus.values()) {
      if (s === 'done') done++;
      else if (s === 'failed' || s === 'cancelled') failed++;
    }
    const txt = failed
      ? (done + ' done, ' + failed + ' failed / ' + total)
      : (done + ' / ' + total);
    chstripCounterEl.textContent = txt;
  }
  function _resetStudyState() {
    studyThreadId = null;
    studyChapterIds = [];
    studyChapterStatus = new Map();
    studyCurrentChapterId = null;
    studyCurrentChapterThreadId = null;
    studyChapterThreads = new Map();
    studyPinnedChapterId = null;
    if (chstripCellsEl) chstripCellsEl.innerHTML = '';
    if (chstripCounterEl) chstripCounterEl.textContent = '';
    _showChStrip(false);
  }

  // Durable strip reconstruction — rebuilds the chapter progress strip from
  // MinIO-backed render status (GET /synth/{slug}/study/chapters) instead of
  // the ephemeral SSE snapshot. THIS is what makes the strip survive a page
  // refresh after a study run finishes: the SSE-replay recovery only works
  // while a run is in flight (and only within the 24h snapshot TTL), whereas
  // this reads the actual rendered artifacts and works indefinitely. Each
  // already-rendered chapter is marked 'done'; the rest stay 'pending'.
  // Only shown for multi-chapter plans (a single chapter is already fully
  // represented by the canvas).
  async function _hydrateChStripFromChapters(slug) {
    if (!slug || !chstripCellsEl) return false;
    try {
      const r = await fetch(API + '/synth/' + slug + '/study/chapters');
      if (!r.ok) return false;
      const data = await r.json();
      const chapters = (data.chapters || []).slice()
        .sort((a, b) => (a.order || 0) - (b.order || 0));
      if (chapters.length < 2) { _showChStrip(false); return false; }
      _renderChStrip(chapters.map(c => c.id));
      chapters.forEach(c => {
        if (!c) return;
        if (c.rendered) _markChStripCell(c.id, 'done');
        // Persist the durable thread_id (from render-latest.json) so a
        // post-refresh click can re-open the chapter's graph canvas.
        if (c.thread_id) {
          studyChapterThreads.set(c.id, c.thread_id);
          const cell = chstripCellsEl.querySelector(
            '.fw-chstrip-cell[data-chapter-id="' + c.id.replace(/"/g, '\\"') + '"]'
          );
          if (cell) cell.dataset.chapterThreadId = c.thread_id;
        }
      });
      _showChStrip(true);
      return true;
    } catch (e) {
      return false;
    }
  }

  // Visual: highlight the strip cell whose chapter the canvas is currently
  // showing. Mutually exclusive — clears any prior selection. Used both
  // when the orchestrator advances (auto-highlight current chapter) and
  // when the user clicks a cell.
  function _highlightStripCell(chapterId) {
    if (!chstripCellsEl) return;
    chstripCellsEl.querySelectorAll('.fw-chstrip-cell.selected')
      .forEach(c => c.classList.remove('selected'));
    if (!chapterId) return;
    const cell = chstripCellsEl.querySelector(
      '.fw-chstrip-cell[data-chapter-id="' + chapterId.replace(/"/g, '\\"') + '"]'
    );
    if (cell) cell.classList.add('selected');
  }

  // Strip-cell click handler — wires the "switch canvas to this chapter"
  // behavior. Behavior depends on the cell's status:
  //   - pending  → no chapter_thread_id known yet, clear canvas to "no
  //                state" view; the user is told nothing has run for
  //                this chapter (visual: pinned, empty graph).
  //   - running  → switch canvas to that chapter's live SSE; if the
  //                user clicked the orchestrator's CURRENT chapter,
  //                this also unpins (return to follow mode).
  //   - done / failed / cancelled → fetch terminal Postgres checkpoint
  //                state, render canvas with all node statuses + KPIs.
  //                SSE opens momentarily, snapshot replays the chapter's
  //                full history, then closes on terminal.
  function _onStripCellClick(cellEl) {
    if (!cellEl) return;
    const cid = cellEl.dataset.chapterId;
    if (!cid) return;
    const status = cellEl.dataset.status || 'pending';
    const chTid = cellEl.dataset.chapterThreadId
                || studyChapterThreads.get(cid)
                || null;

    // Unpin if user clicks the currently-running cell while pinned to it.
    if (cid === studyCurrentChapterId && studyPinnedChapterId === cid) {
      studyPinnedChapterId = null;
      _highlightStripCell(cid);   // stays highlighted as the running one
      return;
    }
    // Already showing this chapter's canvas — just pin/highlight, don't
    // reopen SSE (which would duplicate live event streams).
    if (chTid && synthThreadId === chTid) {
      studyPinnedChapterId = cid;
      _highlightStripCell(cid);
      return;
    }
    studyPinnedChapterId = cid;
    _highlightStripCell(cid);

    // No thread for this chapter. After a refresh the durable thread_id
    // comes from render-latest.json (see _hydrateChStripFromChapters), so
    // a rendered chapter normally HAS one. We only land here when the
    // chapter never ran (pending) OR it's a legacy render written before
    // the thread_id field shipped. Either way, clear the canvas to its
    // empty/pending state — and for a rendered-but-thread-less chapter,
    // hint that re-running synth will restore its inspectable graph.
    if (!chTid) {
      synthThreadId = null;
      resetSynthCards();
      _resetSynthEventBuffer();
      if (typeof NodeDrawer !== 'undefined' && NodeDrawer.reset) {
        NodeDrawer.reset();
      }
      try { renderSynthCards({}); } catch (_) {}
      if (status === 'done') {
        showToast('This chapter was rendered before graph-history tracking ' +
                  'was added. Re-run Synth to inspect its node graph.');
      }
      return;
    }

    // Switch the canvas to the clicked chapter's thread. Reset any prior
    // chapter state, fetch the latest checkpoint, then open SSE so live
    // updates flow if this chapter is still running.
    synthThreadId = chTid;
    resetSynthCards();
    _resetSynthEventBuffer();
    if (typeof NodeDrawer !== 'undefined' && NodeDrawer.reset) {
      NodeDrawer.reset();
    }
    // Initial paint from checkpoint state (handles done/failed/cancelled
    // and gives running chapters their accumulated state before live SSE
    // events resume).
    (async () => {
      try {
        const r = await fetch(API + '/synth/debug/graph/' + chTid + '/state');
        if (r.ok) {
          const data = await r.json();
          renderSynthCards(data.values || {});
        }
      } catch (_) {}
      _synthLiveEventReceived = false;
      pollSynthState(chTid);
    })();
  }

  if (chstripCellsEl) {
    chstripCellsEl.addEventListener('click', ev => {
      const cell = ev.target.closest('.fw-chstrip-cell');
      if (cell) _onStripCellClick(cell);
    });
  }

  // SSE consumer for the STUDY-LEVEL channel — receives orchestrator
  // events (study_start, chapter_running, chapter_done, study_done).
  // For each chapter, opens the per-chapter SSE channel on chapter_running
  // so the Cytoscape canvas lights up substeps in real time.
  async function pollStudyState(sid) {
    const url = API + '/synth/' + sid + '/events';
    let es;
    try {
      es = new EventSource(url);
    } catch (e) {
      markSynthFailed('Study EventSource open failed: ' + String(e));
      _resetStudyState();
      refreshSynthStartState();
      return;
    }
    // Helper: open per-chapter SSE for the currently-active chapter if
    // we haven't already. Debounced (120 ms) so during page-refresh
    // replay — which fires chapter_running N times back-to-back — we
    // only open ONE SSE for the latest chapter rather than spawning
    // N zombie connections.
    let _studyAttachTimer = null;
    const _maybeAttachCurrentChapterSSE = () => {
      // If the user pinned to a specific chapter (clicked its strip cell),
      // do NOT yank the canvas back to the orchestrator's current chapter.
      // The user can unpin by clicking the running chapter's cell.
      if (studyPinnedChapterId &&
          studyPinnedChapterId !== studyCurrentChapterId) return;
      const chTid = studyCurrentChapterThreadId;
      if (!chTid) return;
      if (synthThreadId === chTid) return;
      resetSynthCards();
      _resetSynthEventBuffer();
      if (typeof NodeDrawer !== 'undefined' && NodeDrawer.reset) {
        NodeDrawer.reset();
      }
      synthThreadId = chTid;
      _synthLiveEventReceived = false;
      pollSynthState(chTid);
      _highlightStripCell(studyCurrentChapterId);
    };
    const _scheduleAttachCurrent = () => {
      if (_studyAttachTimer) clearTimeout(_studyAttachTimer);
      _studyAttachTimer = setTimeout(() => {
        _studyAttachTimer = null;
        _maybeAttachCurrentChapterSSE();
      }, 120);
    };

    es.onmessage = (msg) => {
      if (studyThreadId !== sid) {
        try { es.close(); } catch (_) {}
        return;
      }
      let ev;
      try { ev = JSON.parse(msg.data); } catch (_) { return; }

      if (ev.step === 'study' && ev.kind === 'study_start') {
        const ids = ev.chapter_ids || [];
        _renderChStrip(ids);
        _showChStrip(true);
        _setSynthStagePill('working', 'Study running (0 / ' + ids.length + ')');
        return;
      }
      if (ev.step === 'study' && ev.kind === 'chapter_running') {
        const cid = ev.chapter_id;
        const chTid = ev.chapter_thread_id;
        studyCurrentChapterId = cid;
        studyCurrentChapterThreadId = chTid || null;
        if (cid && chTid) {
          studyChapterThreads.set(cid, chTid);
          // Stash on the cell dataset too — survives across re-render
          // and lets the click handler resolve thread_id without the Map.
          const cell = chstripCellsEl && chstripCellsEl.querySelector(
            '.fw-chstrip-cell[data-chapter-id="' + cid.replace(/"/g, '\\"') + '"]'
          );
          if (cell) cell.dataset.chapterThreadId = chTid;
        }
        _markChStripCell(cid, 'running');
        _setSynthStagePill('working',
          'Chapter ' + (ev.position || '?') + ' / ' +
          (ev.n_total || studyChapterIds.length) + ' — ' + cid);
        // Schedule the per-chapter SSE attach. The 120 ms debounce
        // collapses replay bursts so only the LATEST chapter's SSE
        // ever actually opens. Drawer reset moves into the attach
        // helper so it fires exactly once per chapter swap.
        _scheduleAttachCurrent();
        return;
      }
      if (ev.step === 'study' && ev.kind === 'chapter_done') {
        const cid = ev.chapter_id;
        const status = ev.status || 'done';
        _markChStripCell(cid, status);
        if (status === 'failed') {
          showToast('Chapter ' + cid + ' failed: ' +
            (ev.error || 'unknown error') + ' — continuing.');
        }
        if (cid === studyCurrentChapterId) {
          studyCurrentChapterId = null;
          studyCurrentChapterThreadId = null;
        }
        // The per-chapter SSE handler will receive its own terminal
        // and close itself; we just clear our reference.
        synthThreadId = null;
        if (status === 'done') {
          // Step 5 auto-refresh — if the Study panel is currently visible,
          // refetch the chapter list so the new artifact appears in the
          // sidebar without manual navigation. (Study is already unlocked
          // via syncStepLocks once the library exists.)
          try {
            const studyPanel = document.querySelector('#fw-step-5-panel');
            if (studyPanel && studyPanel.classList.contains('active') &&
                typeof loadStudyChapters === 'function' && activeSlug) {
              loadStudyChapters(activeSlug).catch(() => {});
            }
          } catch (_) {}
        }
        return;
      }
      if (ev.step === 'study' && ev.kind === 'study_done') {
        if (_studyAttachTimer) {
          clearTimeout(_studyAttachTimer);
          _studyAttachTimer = null;
        }
        studyCurrentChapterId = null;
        studyCurrentChapterThreadId = null;
        const ok = ev.n_completed || 0;
        const tot = ev.n_total || studyChapterIds.length;
        const fail = ev.n_failed || 0;
        const final = ev.final_status || 'done';
        if (final === 'cancelled') {
          showToast('Study cancelled: ' + ok + '/' + tot + ' chapters done.');
          _setSynthStagePill('cancelled');
        } else if (fail > 0) {
          showToast('Study finished with ' + fail + ' failed chapter(s); ' +
            ok + '/' + tot + ' succeeded.');
          _setSynthStagePill('done', 'Done (' + ok + '/' + tot + ')');
        } else {
          showToast('All ' + tot + ' chapters synthesized. ' +
            'Open Step 5 to study.');
          _setSynthStagePill('done', 'Done (' + ok + '/' + tot + ')');
        }
        // Keep the strip visible briefly so the user sees final state;
        // tear down on next Start Synth (or step navigation).
        return;
      }
      if (ev.step === 'synth' && ev.kind === 'terminal') {
        // Orchestrator emits a final terminal on the study channel
        // after study_done so any generic listener closes cleanly.
        try { es.close(); } catch (_) {}
        if (activeSlug) _forgetActiveStudy(activeSlug);
        studyThreadId = null;
        refreshSynthStartState();
        return;
      }
    };
    es.onerror = () => {
      if (studyThreadId !== sid) {
        try { es.close(); } catch (_) {}
      }
    };
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
          // In study mode, per-chapter failure is reported via the
          // strip; don't tear down the whole stage pill — orchestrator
          // continues with the next chapter.
          if (!studyThreadId) markSynthFailed(ev.error || 'Synth failed.');
        } else if (status === 'cancelled') {
          if (!studyThreadId) {
            showToast('Synth cancelled. Checkpoints up to the cancel point are preserved.');
            _setSynthStagePill('cancelled');
          }
        } else if (status === 'not_implemented') {
          // Router stub — no run happened. Don't toast; the cards stay
          // in their "future" state which already communicates the gap.
        } else {
          if (!studyThreadId) _setSynthStagePill('done');
        }
        try { es.close(); } catch (_) {}
        // In STUDY mode the orchestrator drives the button + pill — only
        // reset state if this was a standalone single-chapter run.
        if (!studyThreadId) {
          synthThreadId = null;
          refreshSynthStartState();
        }
        return;
      }
      if (ev.step) {
        // Buffer every step event so a late-open drawer can replay them.
        _bufferSynthEvent(ev);
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

  // STUDY-mode persistence — separate namespace so it doesn't collide
  // with per-chapter resume. Reused on page-load to resubscribe to the
  // study SSE channel; the server-side snapshot (last 200 events,
  // dd:synth:{tid}:events:snapshot, TTL 1h) replays study_start +
  // chapter_running/done events so the strip rebuilds itself.
  function _studyStorageKey(slug) { return 'dd:study:active:' + slug; }
  function _rememberActiveStudy(slug, sid) {
    try {
      localStorage.setItem(_studyStorageKey(slug), sid);
      localStorage.setItem(_LAST_SYNTH_SLUG_KEY, slug);
    } catch (e) {}
  }
  function _forgetActiveStudy(slug) {
    try { localStorage.removeItem(_studyStorageKey(slug)); } catch (e) {}
  }
  function _getActiveStudy(slug) {
    try { return localStorage.getItem(_studyStorageKey(slug)); }
    catch (e) { return null; }
  }
  function _genSynthThreadId(slug) {
    // Canonical synth thread_id format — MUST match server-side
    // _make_thread_id in routers/v1/docs_distiller/synth.py. The
    // `docs-distiller/synth/` prefix is also what /synth/recent SQL +
    // /synth/{slug}/wipe SQL pattern-match against; an earlier draft
    // used `docs-distiller-synth/` (hyphen) which silently broke both
    // recovery + wipe (the SSE channel still worked because both ends
    // used the same string, masking the bug).
    const uuid = (typeof crypto !== 'undefined' && crypto.randomUUID)
      ? crypto.randomUUID()
      : 'xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx'.replace(/[xy]/g, c => {
          const r = Math.random() * 16 | 0;
          return (c === 'x' ? r : (r & 0x3 | 0x8)).toString(16);
        });
    return 'docs-distiller/synth/' + slug + '/' + uuid;
  }

  // Page-refresh recovery for synth — symmetric with the planner
  // counterpart. Critical: navigating between slugs only PAINTS cached
  // state; it does NOT trigger compute. Only explicit Start Synth click
  // and recoverActiveSynth (page-load, single slug) call /resume.
  async function _tryResumeActiveSynth(slug) {
    // Previously had `if (!synthCardsEl) return false;` which became
    // an unconditional short-circuit when the cards DOM was removed
    // (2026-05-19). The function still drives the GRAPH canvas via
    // renderSynthCards's tail-end `_renderSynthGraph(values)`, so we
    // must always run regardless of cards-DOM presence.
    synthThreadId = null;
    resetSynthCards();
    refreshSynthStartState();

    // STUDY mode recovery — prefer this over per-chapter resume because
    // a study orchestrator owns the run lifecycle. Subscribing to the
    // study channel triggers a server-side snapshot replay that rebuilds
    // the chapter strip + reattaches per-chapter SSE for whichever
    // chapter is currently running.
    const sid = _getActiveStudy(slug);
    if (sid) {
      _resetStudyState();
      studyThreadId = sid;
      // Strip starts empty — study_start in the snapshot replay will
      // populate chapter_ids. _showChStrip(true) so the user immediately
      // sees the panel structure while events stream in.
      _showChStrip(true);
      _setSynthStagePill('working', 'Resuming study…');
      refreshSynthStartState();
      pollStudyState(sid);
      // Snapshot TTL is 24 hours (services/docs_distiller/synth/progress.py).
      // If the user reloads after the snapshot has expired, pollStudyState
      // would wait silently forever. 5 s grace: if we haven't received a
      // single study event by then, forget the session and reset.
      setTimeout(() => {
        if (studyThreadId === sid && studyChapterIds.length === 0) {
          console.log('[study-recover] no replay events in 5s; forgetting',
                      sid);
          _forgetActiveStudy(slug);
          studyThreadId = null;
          _resetStudyState();
          refreshSynthStartState();
        }
      }, 5000);
      return true;
    }

    // No in-flight study for this slug → rebuild the chapter strip from
    // durable MinIO render status so it SURVIVES a refresh after the run
    // completed (the SSE-replay path above only fires while a study is
    // live). Fire-and-forget; independent of the per-chapter canvas resume
    // below (canvas = one chapter, strip = all chapters).
    _resetStudyState();
    _hydrateChStripFromChapters(slug).catch(() => {});

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
    if (!activeSlug || synthThreadId || studyThreadId) return;
    // Until any node ships, the POST returns 503 — surface it cleanly
    // as a toast rather than a failed card.
    if (!synthImplemented || !synthImplemented.size) {
      showToast('Synth pipeline not yet implemented. UI is ready; ' +
                'substeps light up as nodes ship.');
      return;
    }
    resetSynthCards();
    _resetSynthEventBuffer();   // fresh run = fresh event history
    _resetStudyState();          // clear any prior strip state

    // STUDY MODE — Start Synth always fans out across ALL chapters via
    // the orchestrator. The backend mints the study_thread_id and
    // returns it along with chapter_ids; we subscribe to the study
    // channel for orchestrator events and let each chapter_running open
    // its own per-chapter SSE for substep-level updates.
    try {
      const budget = (synthBudgetSel && synthBudgetSel.value) || '5';
      const url = API + '/synth/' + activeSlug +
        '?mode=quality' +
        '&budget=' + encodeURIComponent(budget);
      const r = await fetch(url, {method: 'POST'});
      if (!r.ok) {
        const txt = await r.text();
        markSynthFailed('HTTP ' + r.status + ': ' + txt.slice(0, 400));
        return;
      }
      const data = await r.json();
      const sid = data.study_thread_id;
      const chapterIds = data.chapter_ids || [];
      if (!sid) {
        markSynthFailed('Server did not return a study_thread_id.');
        return;
      }
      studyThreadId = sid;
      _rememberActiveStudy(activeSlug, sid);
      // Pre-render the strip immediately from the response so the user
      // sees structure before the first SSE event lands.
      _renderChStrip(chapterIds);
      _showChStrip(true);
      _setSynthStagePill('working',
        'Study running (0 / ' + chapterIds.length + ')');
      refreshSynthStartState();
      pollStudyState(sid);
    } catch (e) {
      markSynthFailed('Request failed: ' + String(e));
    }
  }

  async function cancelSynth() {
    // In study mode, cancel the study thread — the orchestrator checks
    // its own cancel flag between chapters AND lets the current chapter
    // complete naturally (its per-chapter thread isn't cancelled). For
    // single-chapter runs, cancel that chapter's thread directly.
    const tid = studyThreadId || synthThreadId;
    if (!tid) return;
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
      _resetStudyState();
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

  // ============================================================
  // Step 5 — Study chapter viewer
  //
  // 3-column reader (sidebar / tabs+content) for the synthesized
  // chapters that render_audit_write produces. Per chapter, 3
  // artifact tabs match the render output:
  //   README.md       → marked.js + highlight.js
  //   challenges.md   → marked.js, rendered as collapsible Q's
  //   flashcards.json → flip-card interactive study mode
  //
  // Endpoints consumed:
  //   GET /synth/{slug}/study/chapters             (per-slug chapter list)
  //   GET /synth/{slug}/study/{cid}/artifact/{n}   (READMEs etc bytes)
  //
  // ============================================================
  const studyPillText      = document.querySelector('#fw-study-pill-text');
  const studyPill          = document.querySelector('#fw-study-pill');
  const studyFwName        = document.querySelector('#fw-study-fw-name');
  const studyFwLogos       = document.querySelector('#fw-study-fw-logos');
  const studyEmptyEl       = document.querySelector('#fw-study-empty');
  const studyGridEl        = document.querySelector('#fw-study-grid');
  const studyChapterListEl = document.querySelector('#fw-study-chapter-list');
  const studyChapterHeadEl = document.querySelector('#fw-study-chapter-head');
  const studyReadmeEl      = document.querySelector('#fw-study-readme');
  const studyChallengesEl  = document.querySelector('#fw-study-challenges');
  const studyFlashcardsEl  = document.querySelector('#fw-study-flashcards');
  const studyTabBtns       = document.querySelectorAll('.fw-study-tab');
  // Slide-out chapter side window
  const studySideEl        = document.querySelector('#fw-study-side');
  const studySideBackdrop  = document.querySelector('#fw-study-side-backdrop');
  const studySideClose     = document.querySelector('#fw-study-side-close');
  const studyTocToggle     = document.querySelector('#fw-study-toc-toggle');

  function _setStudySideOpen(open) {
    if (studySideEl) studySideEl.classList.toggle('open', open);
    if (studySideBackdrop) studySideBackdrop.classList.toggle('open', open);
    if (studyTocToggle) studyTocToggle.setAttribute('aria-expanded', String(!!open));
  }
  function openStudySide()  { _setStudySideOpen(true); }
  function closeStudySide() { _setStudySideOpen(false); }
  function toggleStudySide() {
    _setStudySideOpen(!(studySideEl && studySideEl.classList.contains('open')));
  }
  if (studyTocToggle) studyTocToggle.addEventListener('click', toggleStudySide);
  if (studySideClose) studySideClose.addEventListener('click', closeStudySide);
  if (studySideBackdrop) studySideBackdrop.addEventListener('click', closeStudySide);
  document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape' && studySideEl &&
        studySideEl.classList.contains('open')) {
      closeStudySide();
    }
  });

  // Per-framework state
  let studyChapters    = [];     // [{id, title, rendered, audit_passed, ...}]
  let studyActiveChapter = null; // current selected chapter id
  let studyActiveTab   = 'readme';
  let studyCards       = [];     // [{q, a}, ...]
  let studyCardIdx     = 0;
  let studyLoadedSlug  = null;   // last slug we loaded chapters for
  let studyLoadedCid   = null;   // last chapter we loaded artifacts for

  function _setStudyStagePill(status, label) {
    if (!studyPill || !studyPillText) return;
    const map = {
      idle:    'Idle',
      working: 'Loading',
      done:    'Ready',
      failed:  'Failed',
      cancelled: 'Cancelled',
    };
    studyPill.dataset.status = status;
    studyPillText.textContent = label || map[status] || status;
  }

  function setStudyFramework(slug) {
    if (!studyFwName || !studyFwLogos) return;
    if (!slug) {
      studyFwName.textContent = 'Pick a framework with synthesized chapters.';
      studyFwName.classList.add('fw-planner-fw-name-empty');
      studyFwLogos.innerHTML = '';
      studyFwLogos.style.display = 'none';
      return;
    }
    const info = frameworkInfo[slug] || {name: slug, logos: []};
    studyFwName.textContent = info.name || slug;
    studyFwName.classList.remove('fw-planner-fw-name-empty');
    if (info.logos && info.logos.length) {
      studyFwLogos.innerHTML = info.logos.map(u =>
        '<img class="fw-planner-fw-logo" src="' + u + '" alt="">'
      ).join('');
      studyFwLogos.style.display = '';
    } else {
      studyFwLogos.innerHTML = '';
      studyFwLogos.style.display = 'none';
    }
  }

  function _renderStudySidebar() {
    if (!studyChapterListEl) return;
    if (!studyChapters.length) {
      studyChapterListEl.innerHTML =
        '<div class="fw-empty" style="font-size:0.8rem;padding:8px 4px">' +
        'No chapters in this framework\'s plan. Run Planner first.' +
        '</div>';
      return;
    }
    studyChapterListEl.innerHTML = studyChapters.map(ch => {
      const status = !ch.rendered
        ? 'not-rendered'
        : (ch.audit_passed ? 'rendered' : 'audit-failed');
      const icon = !ch.rendered
        ? '○'
        : (ch.audit_passed ? '●' : '✕');
      const cls = [
        'fw-study-chapter',
        ch.id === studyActiveChapter ? 'active' : '',
      ].filter(Boolean).join(' ');
      const title = ch.title || ch.id;
      return (
        '<button type="button" class="' + cls + '" ' +
        'data-chapter-id="' + escapeHtml(ch.id) + '" ' +
        'data-rendered="' + ch.rendered + '">' +
          '<span class="fw-study-chapter-icon" data-status="' + status + '">' +
            icon + '</span>' +
          '<span class="fw-study-chapter-title">' +
            escapeHtml(title) + '</span>' +
        '</button>'
      );
    }).join('');
  }

  function _renderStudyChapterHead(ch) {
    if (!studyChapterHeadEl) return;
    if (!ch) {
      studyChapterHeadEl.classList.remove('visible');
      studyChapterHeadEl.innerHTML = '';
      return;
    }
    const auditBadge = ch.rendered
      ? (ch.audit_passed
          ? '<span class="badge pass">Audit ✓</span>'
          : '<span class="badge fail">Audit ✗</span>')
      : '<span class="badge">Not rendered</span>';
    studyChapterHeadEl.innerHTML =
      '<div class="fw-study-chapter-head-title">' +
        escapeHtml(ch.title || ch.id) + '</div>' +
      '<div class="fw-study-chapter-head-meta">' +
        auditBadge +
        '<span>' + (ch.n_sections || 0) + ' sections</span>' +
        '<span>' + (ch.n_sources || 0) + ' sources</span>' +
        ((ch.rendered_chars || 0)
          ? '<span>' + ((ch.rendered_chars / 1000).toFixed(1)) + 'k chars</span>'
          : '') +
      '</div>';
    studyChapterHeadEl.classList.add('visible');
  }

  function _switchStudyTab(tab) {
    studyActiveTab = tab;
    studyTabBtns.forEach(btn => {
      btn.classList.toggle('active', btn.dataset.tab === tab);
    });
    document.querySelectorAll('.fw-study-pane').forEach(pane => {
      pane.classList.toggle('active', pane.dataset.tab === tab);
    });
  }

  async function _loadStudyArtifact(slug, cid, name) {
    const url = API + '/synth/' + slug + '/study/' + cid + '/artifact/' + name;
    const r = await fetch(url);
    if (!r.ok) throw new Error('HTTP ' + r.status);
    return await r.text();
  }

  async function _loadStudyReadme(slug, cid) {
    if (!studyReadmeEl) return;
    studyReadmeEl.innerHTML =
      '<div class="fw-empty">Loading chapter…</div>';
    try {
      const raw = await _loadStudyArtifact(slug, cid, 'README.md');
      const md = (typeof marked !== 'undefined')
        ? marked.parse(raw)
        : ('<pre>' + escapeHtml(raw) + '</pre>');
      studyReadmeEl.innerHTML = md;
      // Apply syntax highlighting if highlight.js is loaded.
      if (typeof hljs !== 'undefined') {
        studyReadmeEl.querySelectorAll('pre code').forEach(block => {
          try { hljs.highlightElement(block); } catch (_) {}
        });
      }
    } catch (e) {
      studyReadmeEl.innerHTML =
        '<div class="fw-empty">Failed to load README.md: ' +
        escapeHtml(String(e)) + '</div>';
    }
  }

  async function _loadStudyChallenges(slug, cid) {
    if (!studyChallengesEl) return;
    studyChallengesEl.innerHTML =
      '<div class="fw-empty">Loading challenges…</div>';
    try {
      const raw = await _loadStudyArtifact(slug, cid, 'challenges.md');
      // Parse the numbered list manually so we can render each item
      // as a collapsible <details> for active-recall UX.
      const lines = raw.split('\n');
      let title = '';
      const items = [];
      for (const line of lines) {
        const headerMatch = line.match(/^#\s+(.+)$/);
        if (headerMatch) { title = headerMatch[1].trim(); continue; }
        const numMatch = line.match(/^\s*(\d+)\.\s+(.+)$/);
        if (numMatch) {
          items.push({ num: numMatch[1], text: numMatch[2].trim() });
        }
      }
      const headerHtml = title
        ? '<h1>' + escapeHtml(title) + '</h1>'
        : '';
      const itemsHtml = items.map(it => (
        '<details class="fw-study-challenge">' +
          '<summary>' +
            '<span class="fw-study-challenge-num">' + it.num + '.</span>' +
            '<span class="fw-study-challenge-text">' + escapeHtml(it.text) + '</span>' +
          '</summary>' +
          '<div class="fw-study-challenge-hint">' +
            'Pause and think before checking your answer against the chapter. ' +
            'The README explains each concept with the same vocabulary used here.' +
          '</div>' +
        '</details>'
      )).join('');
      studyChallengesEl.innerHTML = headerHtml + itemsHtml;
    } catch (e) {
      studyChallengesEl.innerHTML =
        '<div class="fw-empty">Failed to load challenges.md: ' +
        escapeHtml(String(e)) + '</div>';
    }
  }

  function _renderFlashcard() {
    if (!studyFlashcardsEl) return;
    if (!studyCards.length) {
      studyFlashcardsEl.innerHTML =
        '<div class="fw-empty">No flashcards for this chapter.</div>';
      return;
    }
    const card = studyCards[studyCardIdx];
    const total = studyCards.length;
    studyFlashcardsEl.innerHTML =
      '<div class="fw-study-cards-progress">' +
        'Card ' + (studyCardIdx + 1) + ' of ' + total +
      '</div>' +
      '<div class="fw-study-card-wrap">' +
        '<div class="fw-study-card" id="fw-study-card">' +
          '<div class="fw-study-card-face front">' +
            '<span class="label">Question</span>' +
            '<div class="body">' + _mdInline(card.q) + '</div>' +
          '</div>' +
          '<div class="fw-study-card-face back">' +
            '<span class="label">Answer</span>' +
            '<div class="body">' + _mdInline(card.a) + '</div>' +
          '</div>' +
        '</div>' +
      '</div>' +
      '<div class="fw-study-cards-actions">' +
        '<button type="button" id="fw-study-card-prev"' +
          (studyCardIdx === 0 ? ' disabled' : '') + '>← Prev</button>' +
        '<button type="button" id="fw-study-card-flip">Flip</button>' +
        '<button type="button" id="fw-study-card-next"' +
          (studyCardIdx === total - 1 ? ' disabled' : '') + '>Next →</button>' +
      '</div>' +
      '<div class="fw-study-cards-hint">' +
        'Click the card or hit Flip to reveal the answer.' +
      '</div>';
    // Bind handlers
    const cardEl = document.querySelector('#fw-study-card');
    const prevBtn = document.querySelector('#fw-study-card-prev');
    const flipBtn = document.querySelector('#fw-study-card-flip');
    const nextBtn = document.querySelector('#fw-study-card-next');
    if (cardEl) cardEl.addEventListener('click', () => {
      cardEl.classList.toggle('flipped');
    });
    if (flipBtn) flipBtn.addEventListener('click', () => {
      if (cardEl) cardEl.classList.toggle('flipped');
    });
    if (prevBtn) prevBtn.addEventListener('click', () => {
      if (studyCardIdx > 0) { studyCardIdx--; _renderFlashcard(); }
    });
    if (nextBtn) nextBtn.addEventListener('click', () => {
      if (studyCardIdx < studyCards.length - 1) {
        studyCardIdx++; _renderFlashcard();
      }
    });
  }

  // Tiny inline-markdown helper for flashcard faces — just handles
  // `code` spans + **bold** + line breaks. marked.parse() would wrap
  // everything in <p> which fights the flex-center layout.
  function _mdInline(text) {
    let s = escapeHtml(text || '');
    s = s.replace(/`([^`]+)`/g, '<code>$1</code>');
    s = s.replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>');
    s = s.replace(/\n/g, '<br>');
    return s;
  }

  async function _loadStudyFlashcards(slug, cid) {
    if (!studyFlashcardsEl) return;
    studyFlashcardsEl.innerHTML =
      '<div class="fw-empty">Loading flashcards…</div>';
    try {
      const raw = await _loadStudyArtifact(slug, cid, 'flashcards.json');
      studyCards = JSON.parse(raw) || [];
      studyCardIdx = 0;
      _renderFlashcard();
    } catch (e) {
      studyFlashcardsEl.innerHTML =
        '<div class="fw-empty">Failed to load flashcards.json: ' +
        escapeHtml(String(e)) + '</div>';
    }
  }

  async function openStudyChapter(cid) {
    if (!activeSlug || !cid) return;
    const ch = studyChapters.find(c => c.id === cid);
    if (!ch) return;
    if (!ch.rendered) {
      _renderStudyChapterHead(ch);
      studyReadmeEl.innerHTML =
        '<div class="fw-empty">This chapter has not been synthesized yet. ' +
        'Run Synth (Step 4) on this chapter first.</div>';
      studyChallengesEl.innerHTML =
        '<div class="fw-empty">No challenges available — chapter not synthesized.</div>';
      studyFlashcardsEl.innerHTML =
        '<div class="fw-empty">No flashcards available — chapter not synthesized.</div>';
      return;
    }
    studyActiveChapter = cid;
    studyLoadedCid = cid;
    _renderStudySidebar();   // re-render to update active highlight
    _renderStudyChapterHead(ch);
    _setStudyStagePill('working', 'Loading…');
    // Fire all three loads in parallel
    await Promise.all([
      _loadStudyReadme(activeSlug, cid),
      _loadStudyChallenges(activeSlug, cid),
      _loadStudyFlashcards(activeSlug, cid),
    ]);
    _setStudyStagePill('done', 'Reading · ' + (ch.title || cid));
  }

  async function loadStudyChapters(slug) {
    if (!studyChapterListEl) return;
    studyChapters = [];
    studyActiveChapter = null;
    studyLoadedCid = null;
    _setStudyStagePill('working', 'Loading chapters…');
    studyChapterListEl.innerHTML =
      '<div class="fw-empty" style="font-size:0.8rem;padding:8px 4px">' +
      'Loading chapters…</div>';
    try {
      const r = await fetch(API + '/synth/' + slug + '/study/chapters');
      if (!r.ok) {
        studyChapterListEl.innerHTML =
          '<div class="fw-empty" style="font-size:0.8rem;padding:8px 4px">' +
          'Failed to load chapters (HTTP ' + r.status + ').</div>';
        _setStudyStagePill('failed', 'Failed');
        return;
      }
      const data = await r.json();
      studyChapters = (data.chapters || []).sort(
        (a, b) => (a.order || 0) - (b.order || 0)
      );
      studyLoadedSlug = slug;
      _renderStudySidebar();
      // Auto-open the first rendered chapter (if any) so the user
      // immediately sees content instead of an empty pane.
      const firstReady = studyChapters.find(c => c.rendered);
      if (firstReady) {
        await openStudyChapter(firstReady.id);
      } else {
        _setStudyStagePill('idle',
          'No rendered chapters yet — run Synth first.');
        studyReadmeEl.innerHTML =
          '<div class="fw-empty">No chapters have been synthesized for ' +
          'this framework yet. Run Synth (Step 4) to generate content.</div>';
      }
    } catch (e) {
      studyChapterListEl.innerHTML =
        '<div class="fw-empty" style="font-size:0.8rem;padding:8px 4px">' +
        'Network error loading chapters.</div>';
      _setStudyStagePill('failed', 'Failed');
    }
  }

  // Tab buttons: simple click delegation
  studyTabBtns.forEach(btn => {
    btn.addEventListener('click', () => {
      _switchStudyTab(btn.dataset.tab || 'readme');
    });
  });

  // Chapter sidebar: event delegation for chapter clicks. Picking a
  // chapter closes the side window so the materials get the full width.
  if (studyChapterListEl) {
    studyChapterListEl.addEventListener('click', ev => {
      const btn = ev.target.closest('.fw-study-chapter');
      if (!btn) return;
      const cid = btn.dataset.chapterId;
      if (!cid) return;
      openStudyChapter(cid);
      closeStudySide();
    });
  }

  // Visibility toggle — show empty-state when no slug active. Also
  // exposed as a function so other code paths (slug click, step nav)
  // can re-trigger after activeSlug changes.
  function refreshStudyVisibility() {
    if (!studyEmptyEl || !studyGridEl) return;
    if (!activeSlug) {
      studyEmptyEl.style.display = '';
      studyGridEl.style.display = 'none';
      return;
    }
    studyEmptyEl.style.display = 'none';
    studyGridEl.style.display = '';
  }

  // Hook into showStep so navigating to Step 5 triggers the load. If
  // the framework changed since last load, refresh. If the same, no-op.
  const _origShowStep = showStep;
  // eslint-disable-next-line no-func-assign
  showStep = function(n) {
    _origShowStep(n);
    // The chapter side window is position:fixed, so it would bleed over
    // other steps if left open — always close it when not on Step 5,
    // and start Step 5 content-first (closed) too.
    closeStudySide();
    if (n === 5) {
      refreshStudyVisibility();
      setStudyFramework(activeSlug);
      if (activeSlug && activeSlug !== studyLoadedSlug) {
        loadStudyChapters(activeSlug);
      }
    }
  };
})();
