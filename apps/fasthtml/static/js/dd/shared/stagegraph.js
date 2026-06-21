// StageGraph — shared Cytoscape DAG canvas helper (planner + synth).
// Extracted verbatim from the original IIFE; self-contained, no app-state deps.
export const StageGraph = (function() {
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
          'font-family':      'Source Sans 3, Helvetica Neue, Arial, sans-serif',
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
          // Sky fill — bumped 2026-06-15 from #e0f2fe (sky-100) to
          // #bae6fd (sky-200) so the fill reads alongside saturated
          // borders without washing out. Text bumped to sky-900 for
          // contrast. Convention still the same across Linear / GitHub
          // Actions / Dagster / LangSmith ("actively processing").
          'background-color': '#bae6fd',
          'border-color':     '#0369a1',
          'border-width':     2,
          'color':            '#0c4a6e',
        },
      },
      {
        selector: "node[status = 'done']",
        style: {
          // Green fill — bumped 2026-06-15 from #e5f4e9 to #bbf7d0
          // (green-200). Same hue family as the done border + icon
          // (#2a8b46); the bump just removes the wash-out at small
          // sizes. Text moves to green-900.
          'background-color': '#bbf7d0',
          'border-color':     '#2a8b46',
          'border-width':     2,
          'color':            '#14532d',
        },
      },
      {
        selector: "node[status = 'failed']",
        style: {
          // Red fill — bumped 2026-06-15 from #fde7e9 to #fecaca
          // (red-200). Same hue family as --error-border / --error-text;
          // just removes wash-out at small sizes. Text moves to red-900.
          'background-color': '#fecaca',
          'border-color':     '#e8a3aa',
          'border-width':     3,
          'color':            '#7f1d1d',
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
      // ── Loopback (cycle-closing) edges ─────────────────────────────
      // Pattern 1 from the May 2026 SOTA review (CoRefine UX research):
      // Sugiyama layouts already reverse feedback edges for layering;
      // we tag the edge with kind='loopback' and style it as a distinct
      // amber arc above the row so the cycle is visible at a glance.
      // Dashed when dormant, solid + marching-ants when actively firing.
      // Mirrors Temporal Web UI's retry-edge convention + AI SDK Elements'
      // `animated` vs `temporary` edge taxonomy.
      {
        selector: "edge[kind = 'loopback']",
        style: {
          'line-color':            '#d97706',   // amber-600
          'target-arrow-color':    '#d97706',
          'curve-style':           'unbundled-bezier',
          // Source = mgsr_replan (right end of row), Target = sawc_write
          // (mid-left). Source→target vector points LEFT, so in Cytoscape's
          // perpendicular-offset convention POSITIVE values bend the arc
          // BELOW the row of nodes (toward the right/bottom of the canvas
          // when reading left-to-right). Two control points biased toward
          // the source side produce a wide arc that exits mgsr_replan
          // rightward, swings down/around, and re-enters sawc_write from
          // below — keeping the loopback OUTSIDE the row instead of
          // overlapping the node bodies it used to pass behind.
          'control-point-distances': [140, 90],
          'control-point-weights':   [0.15, 0.85],
          'width':                 2,
          'line-style':            'dashed',
          'line-dash-pattern':     [4, 4],
          'line-dash-offset':      0,
          'opacity':               0.55,
          'target-arrow-shape':    'triangle',
          'arrow-scale':           0.9,
        },
      },
      {
        selector: "edge[kind = 'loopback'][active = 'true']",
        style: {
          // Solid + brighter + thicker when the loop is actually firing
          // (mgsr just routed RETHINK). The marching-ants timer still
          // animates line-dash-offset for the "this is moving" signal.
          'line-color':            '#b45309',   // amber-700, deeper
          'target-arrow-color':    '#b45309',
          'width':                 3,
          'line-style':            'dashed',   // ants still march
          'line-dash-pattern':     [8, 4],
          'opacity':               1.0,
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
          // Optional tag — when present, the stylesheet routes the edge
          // through the `edge[kind = 'loopback']` block (amber arc).
          ...(e.kind ? { kind: e.kind } : {}),
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
      ? { name: 'dagre', rankDir: 'LR', nodeSep: 36, rankSep: 56,
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
    // Loopback-edge presence triggers a slight upward arc — Cytoscape
    // recomputes geometry when the edge is created, so we just need the
    // stylesheet (above) to do its thing. No JS work here.
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
      // Toggle the CoRefine loopback edge into its "actively firing"
      // visual. Called from the synth state poller when LangGraph's
      // snap.next includes a node that's earlier in the topological
      // order than a node whose output is already present (= a real
      // loopback re-entry, not a first-pass progression).
      setLoopActive(isActive) {
        cy.edges("[kind = 'loopback']").forEach(e => {
          e.data('active', isActive ? 'true' : 'false');
        });
      },
      reset() {
        cy.nodes().forEach(n => {
          n.data('status', _initial[n.id()] || 'pending');
          n.data('kpi', '');
        });
        cy.edges().forEach(e => e.data('active', 'false'));
        cy.edges("[kind = 'loopback']").forEach(e => {
          e.data('active', 'false');
        });
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
