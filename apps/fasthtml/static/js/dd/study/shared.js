// study/shared.js — pure / I/O helpers shared between study.js and its
// extracted siblings (readme.js etc.). Created Step 8 (2026-06-05)
// using the same DI pattern as synth/shared.js — breaks the circular
// dep that would otherwise tie readme.js back to study.js.

import * as Sa from '@dd/shared/state/api.js';


// Generic artifact fetch (README, challenges, flashcards). Used by every
// chapter-content loader and by the global flashcard review path.
export async function _loadStudyArtifact(slug, cid, name) {
  const url = Sa.API + '/synth/' + slug + '/study/' + cid + '/artifact/' + name;
  const r = await fetch(url);
  if (!r.ok) throw new Error('HTTP ' + r.status);
  return await r.text();
}

// Persisted study total wall-clock (ms) from GET /synth/{slug}/study/chapters
// (`study_total_wall_ms`). Set by chapters.js after the fetch; read by the
// sidebar header + navbar total renderer. Lives here (not in study.js) so
// both siblings can share the value without re-introducing a study.js↔
// chapters.js cycle.
let _studyTotalWallMs = 0;
export function setStudyTotalWallMs(ms) { _studyTotalWallMs = Number(ms) || 0; }
export function getStudyTotalWallMs() { return _studyTotalWallMs; }
