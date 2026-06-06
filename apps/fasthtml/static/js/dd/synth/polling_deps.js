// synth/polling_deps.js — DI registry for polling.js.

export const deps = {
  renderSynthCards:         null,
  markSynthFailed:          null,
  refreshSynthStartState:   null,
  resetSynthCards:          null,
  _markChStripCell:         null,
  _markChStripCellTime:     null,
  _highlightStripCell:      null,
};

export function registerSynthPollingDeps(obj) {
  Object.assign(deps, obj);
}
