// Boot entry point — imports all modules, binds top-level event listeners, kicks off recovery.
import {
  API, search, chips, generate, cancelBtn, selected, setSelected,
  activeSlug, setActiveSlug, activeRunId, setActiveRunId,
  stickyBar, selectedName, plannerStartBtn, plannerWipeBtn,
} from './state.js';
import { applyFilter } from './picker.js';
import { showStep, syncStepLocks, refreshGenerateState, advance, showConfirm } from './ui.js';
import { triggerIngest, pollRun, loadManifestForSlug } from './ingestion.js';
import { loadLibrary, recoverActiveRuns, recoverActivePlanner, loadPlannerInfo } from './library.js';
import { startPlanner, cancelPlanner, wipePlanner, refreshPlannerStartState } from './planner.js';
import { startSynth, cancelSynth, wipeSynth, refreshSynthStartState, recoverActiveSynth, loadSynthInfo } from './synth.js';
import { refreshStudyVisibility } from './study.js';

// Chip clicks
chips.forEach(c => c.addEventListener('click', () => {
  chips.forEach(x => x.classList.remove('active'));
  c.classList.add('active');
  applyFilter();
}));

// Search
if (search) search.addEventListener('input', applyFilter);

// Generate button
if (generate) generate.addEventListener('click', () => advance());

// Cancel button
if (cancelBtn) cancelBtn.addEventListener('click', async () => {
  if (!activeRunId) return;
  try {
    await fetch(`${API}/runs/${activeRunId}/cancel`, { method: 'POST' });
  } catch (e) { /* best-effort */ }
});

// Planner buttons
if (plannerStartBtn) plannerStartBtn.addEventListener('click', startPlanner);
if (plannerWipeBtn) plannerWipeBtn.addEventListener('click', async () => {
  if (!activeSlug) return;
  const ok = await showConfirm('Wipe Planner', 'Delete all planner data for this framework?', 'Wipe');
  if (ok) await wipePlanner(activeSlug);
});

// Boot
applyFilter();
loadLibrary();
recoverActiveRuns();
recoverActivePlanner();
loadPlannerInfo();
loadSynthInfo();
recoverActiveSynth();
