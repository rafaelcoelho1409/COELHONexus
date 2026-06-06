// synth/shared.js — pure helpers shared between synth.js and its
// extracted siblings (graph.js etc.). Created Step 7 (2026-06-05) to
// break the circular-dep problem that blocked the E1 split earlier
// today: graph.js needs _synthFieldPresent, synth.js also needs it,
// importing graph.js from synth.js would cycle.


// "Is this state field set?" — a `field in values` check; even null
// counts as "node ran" because some nodes legitimately commit null.
// Used by _buildSynthNodeCtx (graph.js), renderSynthCards (synth.js),
// _refreshSynthCardsFromState (synth.js), _synthAllImplementedComplete,
// etc. Tiny enough to be reasonable to share via this module.
export function _synthFieldPresent(values, field) {
  return values && Object.prototype.hasOwnProperty.call(values, field);
}

// Run-start timestamp (ms epoch) for the live "running for Xs" ticker.
// Set by lifecycle.js (startSynth / startStudy / failure paths) and
// polling.js (study_done resets to 0); read by lifecycle.js's elapsed
// hook. Lived as a module-local in synth.js until 2026-06-05 — but
// lifecycle.js wrote it without an import (latent ReferenceError) and
// polling.js had it wrapped behind a getter/setter DI pair. Moved here
// so all three modules go through one canonical store with no cycle.
let _synthRunStartMs = 0;
export function setSynthRunStartMs(ms) { _synthRunStartMs = ms || 0; }
export function getSynthRunStartMs() { return _synthRunStartMs; }
