// shared/ui.js — entry + re-exports for the shared UI subsystem after
// Step 3 (2026-06-05 follow-up) split into three siblings:
//
//   ui/overlays.js — notice / toast / confirm modal (pure DOM)
//   ui/drawer.js   — slide-out file-content drawer (rich markdown)
//   ui/pipeline.js — fetchPipelineState + cross-stage blocker +
//                    cascadeImpactText (pipeline-aware helpers)
//
// refreshGenerateState stays here — it's catalog-stage button state
// the rest of the UI doesn't touch, and it has its own runtime import
// chain (./picker.js) that's specific to the Catalog flow.
import * as Sc from '@dd/shared/state/catalog.js';
import * as Si from '@dd/shared/state/ingestion.js';

export {
  showNotice, hideNotice,
  showToast, hideToast,
  showConfirm, closeModal,
} from './ui/overlays.js';

export {
  openDrawer, closeDrawer, drawerStep, renderDrawerContent,
} from './ui/drawer.js';

export {
  fetchPipelineState,
  fetchActivePipelineStage,
  refreshCrossStageBlocker,
  getCrossStageBlocker,
  crossStageBlockerFor,
  cascadeImpactText,
} from './ui/pipeline.js';

// NOTE: the wizard-era stepper machinery (renderStepper / showStep /
// _showStepImpl / stepFn / syncStepLocks / advance / jumpTo + the
// `.fw-step` click handler) was removed 2026-05-28 — per-stage routes
// (/docs-distiller/<stage>) replaced the single-page stepper, so stage
// navigation is now real <a href> links + main.js stage init.

export function refreshGenerateState() {
  // Disable Start Ingestion + every sidebar Refresh button while an
  // ingestion is in flight. The Start Ingestion button (#fw-generate)
  // only exists on /docs-distiller (catalog) — null-guarded so this
  // function can be safely called from any stage page (e.g. by
  // renderSidebar after the library list re-renders, which would
  // otherwise throw on non-catalog pages and bubble up to the
  // loadLibrary catch block — rendering the popover empty).
  const ingestActive = Si.activeRunId !== null;
  if (Sc.generate) {
    if (!Sc.selected || ingestActive) {
      Sc.generate.setAttribute('disabled', 'disabled');
    } else {
      Sc.generate.removeAttribute('disabled');
    }
  }
  // Bottom-bar labels — only present on the Catalog stage
  // (#fw-sticky-bar in _StickyBar). TWO parallel groups, BOTH visible
  // when a run is in flight:
  //
  //   Selected:   <tile the user clicked>      (always — that's the
  //                                              picker's source of truth)
  //   Ingesting:  <active framework>           (only when activeRunId set;
  //                                              hidden via display:none
  //                                              the rest of the time)
  //
  // Selected stays current with the user's last tile click so they can
  // queue up the NEXT ingestion mentally while the current one runs;
  // Ingesting reflects the pipeline reality. Both names hydrate async
  // via the same singleton ensureFrameworkInfo / catalog tile cache.
  const nameEl = document.querySelector('#fw-selected-name');
  if (nameEl) {
    if (!Sc.selected) nameEl.textContent = '';
    else if (Si.frameworkInfo[Sc.selected]) {
      nameEl.textContent = Si.frameworkInfo[Sc.selected].name || Sc.selected;
    }
  }
  const ingLabel = document.querySelector('#fw-ingesting-label');
  const ingNameEl = document.querySelector('#fw-ingesting-name');
  if (ingLabel && ingNameEl) {
    if (ingestActive && Si.activeSlug) {
      ingLabel.style.display = '';
      const cached = Si.frameworkInfo[Si.activeSlug];
      ingNameEl.textContent = (cached && cached.name) || Si.activeSlug;
      // Hydrate from the resolver if not cached. Same singleton fetch
      // the progress card + picker trigger use, so one round-trip
      // populates all three UI surfaces.
      const cachedSlug = Si.activeSlug;
      const cachedRunId = Si.activeRunId;
      import('../catalog/picker.js').then(({ ensureFrameworkInfo }) => {
        ensureFrameworkInfo(cachedSlug).then((info) => {
          // Re-check state — the run may have finished or switched
          // frameworks between the kick-off and the hydrate response.
          if (Si.activeRunId === cachedRunId && Si.activeSlug === cachedSlug
              && info && info.name && ingNameEl) {
            ingNameEl.textContent = info.name;
          }
        }).catch(() => {});
      }).catch(() => {});
    } else {
      ingLabel.style.display = 'none';
      ingNameEl.textContent = '';
    }
  }
  document.querySelectorAll('.fw-lib-refresh, .fw-lib-delete').forEach(b => {
    if (ingestActive) {
      b.setAttribute('disabled', 'disabled');
    } else {
      b.removeAttribute('disabled');
    }
  });
}
