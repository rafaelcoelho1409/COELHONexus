// state/overlays.js — global overlays (notice + toast + confirm modal +
// file-content drawer) that float above any stage. DOM refs + their
// shared write paths.

// -------- notice + toast --------
export const noticeEl    = document.querySelector('#fw-cache-notice');
export const noticeText  = document.querySelector('#fw-cache-notice-text');
export const toastEl     = document.querySelector('#fw-denied-toast');
export const toastText   = document.querySelector('#fw-denied-toast-text');
export const toastClose  = document.querySelector('#fw-denied-toast-close');

// -------- confirm modal --------
export const modalEl         = document.querySelector('#fw-modal');
export const modalTitleEl    = document.querySelector('#fw-modal-title');
export const modalMessageEl  = document.querySelector('#fw-modal-message');
export const modalConfirmBtn = document.querySelector('#fw-modal-confirm');
export const modalCancelBtn  = document.querySelector('#fw-modal-cancel');

// -------- file-content drawer --------
export const drawerEl    = document.querySelector('#fw-drawer');
export const drawerName  = document.querySelector('#fw-drawer-name');
export const drawerMeta  = document.querySelector('#fw-drawer-meta');
export const drawerBody  = document.querySelector('#fw-drawer-body');
export const drawerPrev  = document.querySelector('#fw-drawer-prev');
export const drawerNext  = document.querySelector('#fw-drawer-next');
export const drawerClose = document.querySelector('#fw-drawer-close');

// State
export let currentManifestEntries = [];
export let drawerIdx              = -1;
export let _modalResolver         = null;

// Setters
export function setCurrentManifestEntries(v) { currentManifestEntries = v; }
export function setDrawerIdx(v)              { drawerIdx = v; }
export function set_modalResolver(v)         { _modalResolver = v; }
