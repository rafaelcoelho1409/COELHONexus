// study/flashcards_deps.js — DI dependency registry for flashcards.js.
//
// flashcards has 10 cross-references to functions defined later in
// study.js (the chapter-loading + sidebar + tab-switching + status-pill
// functions). A direct import back to study.js would cycle. The DI
// pattern: flashcards.js reads from `deps`, which study.js mutates
// in-place via `registerFlashcardsDeps(...)` at module init.
//
// CONTRACT: study.js MUST call registerFlashcardsDeps() before any user
// action can trigger the flashcards code. Module-eval ordering
// guarantees this: study.js's top-level body runs (including the
// register call) before any async event handler can fire.

export const deps = {
  _setStudyStagePill:      null,
  _renderStudySidebar:     null,
  _renderStudyChapterHead: null,
  _switchStudyTab:         null,
  _loadStudyReadme:        null,
  _loadStudyChallenges:    null,
  openStudyChapter:        null,
  loadStudyChapters:       null,
  refreshStudyVisibility:  null,
  _scrollReaderTop:        null,
};

export function registerFlashcardsDeps(obj) {
  Object.assign(deps, obj);
}
