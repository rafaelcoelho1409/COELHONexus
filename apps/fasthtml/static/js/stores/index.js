// stores/index.js — re-export every store as a flat surface for callers
// that want the whole bag (analogous to `import * as S` for state).
// Specific imports remain preferable for clarity.
export { $activePipeline } from './pipeline.js';
export { $activeStudy }    from './study.js';
export { $sseStreams }     from './sse.js';
