// stores/study.js — active study reader atom.
//
// Reflects "which chapter is the user reading right now" so the chapter
// strip in synth, the sidebar in study, and the topbar status dot can
// stay in sync without each polling DOM-derived state. Set by
// study.js::openStudyChapter, read by chstrip.js + topbar.js.
//
// Values: null | { slug, chapter_id, thread_id }
import { atom } from 'nanostores';

export const $activeStudy = atom(null);
