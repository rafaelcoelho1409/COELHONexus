// synth/polling_deps.js — DI registry for polling.js.

export const deps = {
  renderSynthCards:         null,
  markSynthFailed:          null,
  refreshSynthStartState:   null,
  resetSynthCards:          null,
  _markChStripCell:         null,
  _markChStripCellTime:     null,
  _highlightStripCell:      null,
  // _forgetActiveStudy lives in lifecycle.js; polling.js → lifecycle.js
  // would cycle (lifecycle.js already imports from polling.js for
  // pollSynthState / pollStudyState). DI breaks the cycle.
  _forgetActiveStudy:       null,
};

export function registerSynthPollingDeps(obj) {
  Object.assign(deps, obj);
}
