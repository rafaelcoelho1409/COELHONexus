// study/study_deps.js — DI dependency registry for sidebar.js + chapters.js.
//
// Both extracted modules need cross-refs back to functions defined later
// in study.js (closeStudySide, _setStudyStagePill, etc.). A direct
// import would cycle. The DI registration pattern: study.js calls
// registerStudyDeps({...}) at module init, after the dependent
// functions are defined.

export const deps = {
  closeStudySide:          null,
  openStudyChapter:        null,
  _setStudyStagePill:      null,
  _renderStudySidebar:     null,
  _renderStudyChapterHead: null,
  setStudyFramework:       null,
  _setStudySideOpen:       null,
};

export function registerStudyDeps(obj) {
  Object.assign(deps, obj);
}
