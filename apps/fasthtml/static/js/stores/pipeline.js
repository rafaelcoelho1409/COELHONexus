// stores/pipeline.js — cross-stage pipeline lock atom.
//
// Planner and Synth are mutually exclusive — they share LLM resources via
// the rotator. The backend enforces this with Redis locks
// (`dd:planner:lock:{slug}`, `dd:synth:lock:{slug}` — see memory
// `todo_planner_synth_global_locks_2026_06_01`). The frontend polls
// `GET /pipeline/active` and writes the result here so any consumer can
// `subscribe()` instead of re-polling.
//
// Values: null | { stage: 'planner'|'synth', slug, run_id }
import { atom } from 'nanostores';

export const $activePipeline = atom(null);
