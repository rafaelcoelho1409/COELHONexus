// synth/chstrip_deps.js — DI dependency registry for chstrip.js.
//
// The chstrip block has 6+ cross-references to functions / state defined
// later in synth.js (pollSynthState, refreshSynthStartState, etc.). A
// direct `import` from chstrip.js back to synth.js would cycle. The DI
// pattern: chstrip.js reads from the `deps` object, which synth.js
// mutates in-place via `registerChstripDeps(...)` at module init.
//
// CONTRACT: synth.js MUST call registerChstripDeps() before any user
// action can trigger the chstrip cell click handler. Since the click
// handler only fires after module init completes (DOM event), this is
// guaranteed by JavaScript module evaluation semantics: synth.js's
// top-level body runs to completion (including the register call)
// before any async user event fires.

export const deps = {
  _resizeSynthCanvas:       null,
  refreshSynthStartState:   null,
  resetSynthCards:          null,
  _resetSynthEventBuffer:   null,
  renderSynthCards:         null,
  pollSynthState:           null,
  // _nodeDrawerRef is module-private inside synth.js (mutated by
  // _setNodeDrawerRef). chstrip.js calls a getter so synth.js can keep
  // the variable's identity.
  _getNodeDrawerRef:        null,
};

export function registerChstripDeps(obj) {
  Object.assign(deps, obj);
}
