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
