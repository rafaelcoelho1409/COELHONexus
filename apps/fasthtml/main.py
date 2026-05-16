"""COELHO Nexus — FastHTML base shell.

Visual style: Plotly dash-financial-report (Raleway + burgundy on
off-white). Docs Distiller wires the 3-step wizard:

  Step 1  Pick      — framework picker (search + chips + tile grid)
  Step 2  Generate  — live progress + cancel button
  Step 3  Study     — page grid backed by the persistent MinIO manifest

A library sidebar appears on Steps 2 + 3 listing every framework already
finalized in MinIO; each row has a refresh button (forces re-ingest). The
sidebar is the entry point for re-visiting cached studies without going
back through the picker.

Behavior contracts with the backend:
  POST /runs                      → {status: cached|queued|locked, run_id?, manifest?}
  POST /runs/{id}/cancel          → cooperative cancel; tier raises, dispatcher cleans up MinIO
  GET  /runs/{id}                 → live progress + manifest snapshot (Redis)
  GET  /ingestion                 → sidebar data source (every finalized framework)
  GET  /ingestion/{slug}/manifest → canonical manifest from MinIO
  GET  /ingestion/{slug}/pages/{i}→ page body from MinIO
"""
import os
from typing import Optional

import httpx
from fasthtml.common import (
    H1, A, Button, Div, Img, Input, Link, Meta, P, Script, Span, Style, Title,
    fast_app, serve,
)
from starlette.requests import Request
from starlette.responses import PlainTextResponse, StreamingResponse
from starlette.routing import Route


FEATURES = [
    ("docs-distiller", "Docs Distiller", "/docs-distiller"),
    ("youtube-content-search", "YouTube Content Search", "/youtube-content-search"),
    ("coming-soon", "Coming Soon", "/coming-soon"),
]

FASTAPI_URL = os.environ.get(
    "FASTAPI_URL", "http://coelhonexus-fastapi:8000"
).rstrip("/")


HEAD = (
    Meta(charset="UTF-8"),
    Meta(name="viewport", content="width=device-width, initial-scale=1.0"),
    Link(rel="preconnect", href="https://fonts.googleapis.com"),
    Link(rel="preconnect", href="https://fonts.gstatic.com", crossorigin=""),
    Link(
        rel="stylesheet",
        href=(
            "https://fonts.googleapis.com/css2?"
            "family=Raleway:wght@300;400;500;600;700&display=swap"
        ),
    ),
    # Client-side markdown renderer for the file-content drawer. Pinned
    # major version — zero deps, ~50 KB gzip over the jsDelivr CDN.
    Script(src="https://cdn.jsdelivr.net/npm/marked@12/marked.min.js"),
    Style("""
        :root {
          --bg: #fafafa;
          --card: #ffffff;
          --primary: #c41230;
          --primary-dark: #65201f;
          --text: #2a2a2a;
          --text-muted: #8b8b8b;
          --border: #e5e5e5;
          --notice-bg: #fef7e0;
          --notice-border: #f5c560;
          --notice-text: #6b4d10;
          --error-bg: #fde7e9;
          --error-border: #e8a3aa;
          --error-text: #7a2228;
        }
        * { box-sizing: border-box; }
        html, body {
          margin: 0;
          padding: 0;
          background: var(--bg);
          color: var(--text);
          font-family: 'Raleway', 'HelveticaNeue', 'Helvetica Neue',
                       Helvetica, Arial, sans-serif;
          -webkit-font-smoothing: antialiased;
          font-weight: 400;
          line-height: 1.5;
        }
        .page { padding: 32px 40px 96px 40px; }
        .card {
          background: var(--card);
          width: 100%;
          border: 1px solid var(--border);
          border-radius: 4px;
          padding: 32px 48px 56px 48px;
        }
        .topbar {
          display: flex;
          align-items: center;
          justify-content: space-between;
          gap: 32px;
          margin-bottom: 36px;
        }
        .brand {
          display: flex;
          align-items: center;
          gap: 16px;
          color: var(--primary);
          font-weight: 600;
          font-size: 1.55rem;
          letter-spacing: 0.01em;
        }
        .brand-flag {
          width: 0;
          height: 0;
          border-style: solid;
          border-width: 0 0 28px 28px;
          border-color: transparent transparent var(--primary) transparent;
          display: inline-block;
        }
        .nav { display: flex; gap: 6px; flex: 1; justify-content: center; }
        .nav-item {
          padding: 9px 16px;
          font-size: 0.82rem;
          color: var(--text-muted);
          text-decoration: none;
          border-radius: 3px;
          font-weight: 500;
          letter-spacing: 0.02em;
          cursor: pointer;
          transition: color 0.15s, background 0.15s;
        }
        .nav-item:hover { color: var(--text); background: rgba(0,0,0,0.04); }
        .nav-item.active { color: var(--primary); font-weight: 600; }
        .btn-primary {
          background: var(--primary);
          color: #ffffff;
          border: 0;
          padding: 9px 22px;
          font-size: 0.78rem;
          font-family: inherit;
          border-radius: 3px;
          cursor: pointer;
          font-weight: 600;
          letter-spacing: 0.02em;
          white-space: nowrap;
        }
        .btn-primary:hover { background: var(--primary-dark); }
        .btn-primary:disabled,
        .btn-primary[disabled] {
          background: var(--text-muted);
          cursor: not-allowed;
          opacity: 0.55;
        }
        .btn-primary:disabled:hover { background: var(--text-muted); }
        .btn-outline {
          background: transparent;
          color: var(--text);
          border: 1px solid var(--border);
          padding: 8px 18px;
          font-size: 0.78rem;
          font-family: inherit;
          border-radius: 3px;
          cursor: pointer;
          font-weight: 500;
          letter-spacing: 0.02em;
          white-space: nowrap;
        }
        .btn-outline:hover { border-color: var(--text-muted); }
        .title-row {
          display: flex;
          align-items: flex-start;
          justify-content: space-between;
          gap: 24px;
          padding-left: 16px;
          border-left: 6px solid var(--primary);
          margin-bottom: 32px;
        }
        .title {
          font-size: 1.55rem;
          font-weight: 400;
          color: var(--text);
          line-height: 1.25;
          margin: 0;
        }
        .panel { min-height: 360px; }

        /* ===== Framework picker (Step 1) ===== */
        .fw-picker { display: flex; flex-direction: column; gap: 18px; }
        .fw-search-row { display: flex; align-items: center; gap: 16px; }
        .fw-search {
          flex: 1;
          padding: 12px 16px;
          font-size: 0.95rem;
          font-family: inherit;
          border: 1px solid var(--border);
          border-radius: 3px;
          background: var(--card);
          color: var(--text);
          outline: none;
          transition: border-color 0.15s;
        }
        .fw-search:focus { border-color: var(--primary); }
        .fw-count {
          color: var(--text-muted);
          font-size: 0.78rem;
          white-space: nowrap;
          letter-spacing: 0.02em;
        }
        .fw-chips { display: flex; flex-wrap: wrap; gap: 8px; }
        .fw-chip {
          padding: 6px 14px;
          border-radius: 999px;
          border: 1px solid var(--border);
          font-size: 0.78rem;
          color: var(--text-muted);
          cursor: pointer;
          user-select: none;
          background: var(--card);
          transition: color 0.15s, background 0.15s, border-color 0.15s;
        }
        .fw-chip:hover { color: var(--text); border-color: var(--text-muted); }
        .fw-chip.active {
          background: var(--primary); color: #fff; border-color: var(--primary);
        }
        .fw-grid {
          display: grid;
          grid-template-columns: repeat(auto-fill, minmax(180px, 1fr));
          gap: 10px;
        }
        .fw-grid.fw-grid-empty {
          display: block;
          padding: 24px 0;
          text-align: center;
          color: var(--text-muted);
          font-size: 0.85rem;
        }
        .fw-grid.fw-grid-empty::after {
          content: 'No frameworks match this filter.';
        }
        .fw-tile {
          padding: 14px;
          border: 1px solid var(--border);
          border-radius: 4px;
          cursor: pointer;
          background: var(--card);
          transition: border-color 0.15s, background 0.15s, box-shadow 0.15s;
        }
        .fw-tile:hover { border-color: var(--text-muted); }
        .fw-tile.selected {
          border-color: var(--primary);
          background: rgba(196, 18, 48, 0.04);
          box-shadow: inset 0 0 0 1px var(--primary);
        }
        .fw-tile-logo {
          height: 28px; width: auto; max-width: 100%;
          display: block; margin-bottom: 10px; object-fit: contain;
        }
        .fw-tile-name {
          font-size: 0.92rem; font-weight: 500;
          color: var(--text); word-break: break-word;
        }
        .fw-tile-cat {
          font-size: 0.68rem; color: var(--text-muted);
          margin-top: 6px; text-transform: uppercase; letter-spacing: 0.05em;
        }
        .fw-sticky-bar {
          position: fixed;
          bottom: 0; left: 0; right: 0;
          background: var(--card);
          border-top: 1px solid var(--border);
          box-shadow: 0 -2px 12px rgba(0, 0, 0, 0.05);
          padding: 14px 40px;
          display: flex;
          align-items: center;
          gap: 16px;
          z-index: 50;
          transform: translateY(100%);
          transition: transform 0.25s ease;
        }
        .fw-sticky-bar.visible { transform: translateY(0); }
        .fw-selected-label {
          flex: 1;
          color: var(--text-muted);
          font-size: 0.88rem;
        }
        .fw-selected-name {
          color: var(--primary); font-weight: 600;
        }
        .fw-empty {
          color: var(--text-muted);
          padding: 32px;
          text-align: center;
          font-size: 0.9rem;
        }

        /* ===== Stepper ===== */
        .fw-stepper-row {
          display: flex;
          align-items: center;
          gap: 16px;
          margin-bottom: 32px;
        }
        .fw-stepper { flex: 1; display: flex; align-items: center; }
        .fw-step {
          display: flex; align-items: center; gap: 10px;
          flex-shrink: 0; cursor: not-allowed; user-select: none;
          opacity: 0.5; transition: opacity 0.2s;
        }
        .fw-step.completed,
        .fw-step.active { cursor: pointer; opacity: 1; }
        .fw-step-circle {
          width: 28px; height: 28px; border-radius: 50%;
          border: 2px solid var(--border); background: var(--card);
          display: flex; align-items: center; justify-content: center;
          font-size: 0.78rem; font-weight: 600;
          color: var(--text-muted); transition: all 0.2s;
        }
        .fw-step.active .fw-step-circle {
          border-color: var(--primary); color: var(--primary);
        }
        .fw-step.completed .fw-step-circle {
          background: var(--primary); border-color: var(--primary);
          color: #fff;
        }
        .fw-step-label {
          font-size: 0.85rem; font-weight: 500;
          color: var(--text-muted); letter-spacing: 0.02em; white-space: nowrap;
        }
        .fw-step.active .fw-step-label {
          color: var(--primary); font-weight: 600;
        }
        .fw-step.completed .fw-step-label { color: var(--text); }
        /* Hover affordance on clickable steps — underline label + ring the circle */
        .fw-step.active:hover .fw-step-label,
        .fw-step.completed:hover .fw-step-label {
          text-decoration: underline;
          text-underline-offset: 4px;
        }
        .fw-step.active:hover .fw-step-circle,
        .fw-step.completed:hover .fw-step-circle {
          box-shadow: 0 0 0 3px rgba(196, 18, 48, 0.12);
        }
        .fw-step-connector {
          flex: 1; height: 2px; background: var(--border);
          margin: 0 14px; min-width: 24px; transition: background 0.2s;
        }
        .fw-step-connector.complete { background: var(--primary); }
        .fw-new-study {
          color: var(--primary);
          font-size: 0.82rem; font-weight: 500;
          cursor: pointer; background: none; border: 0;
          padding: 6px 10px; font-family: inherit;
          letter-spacing: 0.02em; visibility: hidden; white-space: nowrap;
        }
        .fw-new-study.visible { visibility: visible; }
        .fw-new-study:hover { color: var(--primary-dark); }

        .fw-step-panel { display: none; }
        .fw-step-panel.active { display: block; }

        #fw-step-1-edit {
          display: flex; flex-direction: column; gap: 18px;
        }

        .fw-readonly {
          padding: 18px 20px;
          background: rgba(0, 0, 0, 0.02);
          border: 1px solid var(--border);
          border-radius: 4px;
          color: var(--text);
          font-size: 0.92rem;
          line-height: 1.5;
        }
        .fw-readonly .fw-readonly-name {
          color: var(--primary); font-weight: 600;
        }
        .fw-readonly-hint {
          display: block; margin-top: 10px;
          font-size: 0.78rem; color: var(--text-muted);
        }
        .fw-step-placeholder {
          padding: 64px 24px; text-align: center;
          color: var(--text-muted); font-size: 0.92rem;
          border: 2px dashed var(--border); border-radius: 4px;
          line-height: 1.6;
        }
        .fw-step-placeholder-title {
          font-size: 1.1rem; font-weight: 500;
          color: var(--text); margin-bottom: 10px;
        }

        /* ===== Layout: sidebar + main panel on Step 2 / Step 3 ===== */
        .fw-layout { display: flex; gap: 24px; align-items: flex-start; }
        .fw-sidebar {
          width: 260px;
          flex-shrink: 0;
          border-right: 1px solid var(--border);
          padding-right: 20px;
          max-height: 70vh;
          overflow-y: auto;
        }
        .fw-sidebar-title {
          font-size: 0.7rem;
          color: var(--text-muted);
          text-transform: uppercase;
          letter-spacing: 0.08em;
          font-weight: 600;
          margin: 0 0 12px 0;
        }
        .fw-sidebar-empty {
          padding: 20px 0;
          color: var(--text-muted);
          font-size: 0.82rem;
          line-height: 1.5;
        }
        .fw-lib-item {
          display: flex;
          align-items: center;
          gap: 10px;
          padding: 8px 10px;
          border-radius: 4px;
          cursor: pointer;
          margin-bottom: 4px;
          border: 1px solid transparent;
          transition: background 0.15s, border-color 0.15s;
        }
        .fw-lib-item:hover {
          background: rgba(0, 0, 0, 0.03);
          border-color: var(--border);
        }
        .fw-lib-item.active {
          background: rgba(196, 18, 48, 0.06);
          border-color: var(--primary);
        }
        .fw-lib-name {
          flex: 1;
          min-width: 0;
          font-size: 0.85rem;
          font-weight: 500;
          color: var(--text);
          overflow: hidden;
          text-overflow: ellipsis;
          white-space: nowrap;
        }
        .fw-lib-meta {
          font-size: 0.68rem;
          color: var(--text-muted);
          margin-top: 2px;
          letter-spacing: 0.02em;
        }
        .fw-lib-logo {
          flex-shrink: 0;
          width: 22px;
          height: 22px;
          object-fit: contain;
        }
        .fw-lib-refresh,
        .fw-lib-delete {
          flex-shrink: 0;
          background: transparent;
          color: var(--text-muted);
          border: 1px solid var(--border);
          border-radius: 3px;
          width: 26px;
          height: 26px;
          padding: 0;
          cursor: pointer;
          font-family: inherit;
          font-size: 0.85rem;
          line-height: 1;
          display: flex;
          align-items: center;
          justify-content: center;
        }
        .fw-lib-refresh:hover {
          color: var(--primary);
          border-color: var(--primary);
        }
        .fw-lib-refresh:disabled,
        .fw-lib-refresh[disabled] {
          cursor: not-allowed;
          opacity: 0.4;
        }
        .fw-lib-refresh:disabled:hover {
          color: var(--text-muted);
          border-color: var(--border);
        }
        .fw-lib-delete:hover {
          color: #fff;
          background: var(--primary);
          border-color: var(--primary);
        }
        .fw-lib-delete:disabled,
        .fw-lib-delete[disabled] {
          cursor: not-allowed;
          opacity: 0.4;
        }
        .fw-lib-delete:disabled:hover {
          color: var(--text-muted);
          background: transparent;
          border-color: var(--border);
        }

        /* ===== Spinner (used inside the delete button while deleting) ===== */
        .fw-spinner {
          width: 13px;
          height: 13px;
          border: 2px solid var(--border);
          border-top-color: var(--primary);
          border-radius: 50%;
          animation: fw-spin 0.8s linear infinite;
        }
        @keyframes fw-spin { to { transform: rotate(360deg); } }

        /* ===== Confirm modal (generic, reusable) ===== */
        .fw-modal-backdrop {
          position: fixed;
          inset: 0;
          background: rgba(0, 0, 0, 0.42);
          display: none;
          align-items: center;
          justify-content: center;
          z-index: 100;
          animation: fw-fade-in 0.15s ease;
        }
        .fw-modal-backdrop.visible { display: flex; }
        @keyframes fw-fade-in {
          from { opacity: 0; }
          to   { opacity: 1; }
        }
        .fw-modal {
          background: var(--card);
          border-radius: 6px;
          padding: 24px 28px 20px;
          max-width: 460px;
          width: 90%;
          box-shadow: 0 8px 32px rgba(0, 0, 0, 0.18);
        }
        .fw-modal-title {
          margin: 0 0 10px;
          font-size: 1.05rem;
          font-weight: 600;
          color: var(--text);
        }
        .fw-modal-message {
          margin: 0 0 22px;
          font-size: 0.88rem;
          color: var(--text-muted);
          line-height: 1.55;
        }
        .fw-modal-actions {
          display: flex;
          justify-content: flex-end;
          gap: 10px;
        }
        .fw-main { flex: 1; min-width: 0; }

        /* ===== Notices (cache notice + denied toast) ===== */
        .fw-notice,
        .fw-toast {
          display: flex;
          align-items: center;
          gap: 12px;
          padding: 12px 16px;
          border-radius: 4px;
          margin-bottom: 20px;
          font-size: 0.85rem;
          line-height: 1.4;
        }
        .fw-notice {
          background: var(--notice-bg);
          border: 1px solid var(--notice-border);
          color: var(--notice-text);
        }
        .fw-toast {
          background: var(--error-bg);
          border: 1px solid var(--error-border);
          color: var(--error-text);
        }
        .fw-notice-text, .fw-toast-text { flex: 1; }
        .fw-toast-close {
          background: transparent;
          border: 0;
          color: var(--error-text);
          font-size: 1.1rem;
          cursor: pointer;
          padding: 0 6px;
          font-family: inherit;
          line-height: 1;
        }

        /* ===== Step 2 progress display ===== */
        .fw-progress {
          padding: 24px;
          border: 1px solid var(--border);
          border-radius: 4px;
          background: var(--card);
        }
        .fw-progress-head {
          display: flex; align-items: center; justify-content: space-between;
          margin-bottom: 14px;
        }
        .fw-progress-tier {
          font-size: 0.95rem; font-weight: 600; color: var(--text);
        }
        .fw-progress-status {
          font-size: 0.78rem; color: var(--text-muted);
          letter-spacing: 0.02em; text-transform: uppercase;
        }
        .fw-progress-bar {
          height: 8px;
          background: var(--border);
          border-radius: 4px;
          overflow: hidden;
          margin-bottom: 10px;
          position: relative;
        }
        .fw-progress-fill {
          height: 100%;
          background: var(--primary);
          width: 0%;
          transition: width 0.3s ease;
        }
        .fw-progress-bar.indeterminate .fw-progress-fill {
          width: 35%;
          animation: fw-indet 1.2s ease-in-out infinite;
        }
        @keyframes fw-indet {
          0%   { transform: translateX(-100%); }
          100% { transform: translateX(310%); }
        }
        .fw-progress-meta {
          display: flex; justify-content: space-between;
          font-size: 0.78rem; color: var(--text-muted);
          margin-bottom: 10px;
        }
        .fw-progress-url {
          font-family: 'JetBrains Mono', ui-monospace, monospace;
          font-size: 0.72rem; color: var(--text-muted);
          background: rgba(0, 0, 0, 0.03);
          padding: 6px 10px; border-radius: 3px;
          overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
        }
        .fw-progress-actions {
          margin-top: 16px;
          display: flex; justify-content: flex-end; gap: 12px;
        }

        /* ===== Step 3 page list ===== */
        .fw-page-grid {
          display: flex;
          flex-direction: column;
          border: 1px solid var(--border);
          border-radius: 4px;
          overflow: hidden;
        }
        .fw-page-card {
          display: flex;
          align-items: center;
          justify-content: space-between;
          gap: 16px;
          padding: 10px 14px;
          background: var(--card);
          cursor: pointer;
          border-bottom: 1px solid var(--border);
          transition: background 0.15s;
        }
        .fw-page-card:last-child { border-bottom: 0; }
        .fw-page-card:hover { background: rgba(0, 0, 0, 0.03); }
        .fw-page-title {
          flex: 1;
          min-width: 0;
          font-size: 0.85rem;
          font-weight: 500;
          color: var(--text);
          overflow: hidden;
          text-overflow: ellipsis;
          white-space: nowrap;
        }
        .fw-page-meta {
          flex-shrink: 0;
          font-size: 0.7rem;
          color: var(--text-muted);
          letter-spacing: 0.02em;
          font-family: 'JetBrains Mono', ui-monospace, monospace;
        }
        .fw-pages-summary {
          display: flex;
          justify-content: space-between;
          align-items: baseline;
          margin-bottom: 14px;
          color: var(--text-muted);
          font-size: 0.82rem;
        }
        .fw-pages-summary strong { color: var(--text); font-weight: 600; }

        /* ===== File-content slide-out drawer (right-anchored) ===== */
        .fw-drawer {
          position: fixed;
          top: 0;
          right: 0;
          height: 100vh;
          width: 60vw;
          min-width: 480px;
          max-width: 1000px;
          background: var(--card);
          border-left: 1px solid var(--border);
          box-shadow: -8px 0 32px rgba(0, 0, 0, 0.08);
          transform: translateX(100%);
          transition: transform 0.25s ease;
          z-index: 90;
          display: flex;
          flex-direction: column;
        }
        .fw-drawer.visible { transform: translateX(0); }
        .fw-drawer-header {
          flex-shrink: 0;
          display: flex;
          align-items: center;
          gap: 16px;
          padding: 14px 20px;
          border-bottom: 1px solid var(--border);
          background: var(--card);
        }
        .fw-drawer-title { flex: 1; min-width: 0; }
        .fw-drawer-name {
          font-size: 0.95rem;
          font-weight: 600;
          color: var(--text);
          overflow: hidden;
          text-overflow: ellipsis;
          white-space: nowrap;
        }
        .fw-drawer-meta {
          font-size: 0.72rem;
          color: var(--text-muted);
          margin-top: 2px;
          letter-spacing: 0.02em;
          font-family: 'JetBrains Mono', ui-monospace, monospace;
        }
        .fw-drawer-controls {
          flex-shrink: 0;
          display: flex;
          gap: 6px;
        }
        .fw-drawer-btn {
          background: transparent;
          color: var(--text-muted);
          border: 1px solid var(--border);
          border-radius: 3px;
          width: 32px;
          height: 32px;
          padding: 0;
          cursor: pointer;
          font-family: inherit;
          font-size: 0.95rem;
          line-height: 1;
          display: flex;
          align-items: center;
          justify-content: center;
        }
        .fw-drawer-btn:hover {
          color: var(--primary);
          border-color: var(--primary);
        }
        .fw-drawer-btn:disabled,
        .fw-drawer-btn[disabled] {
          cursor: not-allowed;
          opacity: 0.35;
        }
        .fw-drawer-btn:disabled:hover {
          color: var(--text-muted);
          border-color: var(--border);
        }
        .fw-drawer-body {
          flex: 1;
          overflow-y: auto;
          padding: 24px 32px 60px 32px;
        }
        /* Markdown prose inside the drawer */
        .fw-markdown {
          font-size: 0.92rem;
          color: var(--text);
          line-height: 1.65;
        }
        .fw-markdown h1, .fw-markdown h2, .fw-markdown h3,
        .fw-markdown h4, .fw-markdown h5, .fw-markdown h6 {
          color: var(--text);
          font-weight: 600;
          line-height: 1.3;
          margin: 1.4em 0 0.5em;
        }
        .fw-markdown h1 { font-size: 1.5rem; }
        .fw-markdown h2 { font-size: 1.25rem; }
        .fw-markdown h3 { font-size: 1.1rem; }
        .fw-markdown h4 { font-size: 0.98rem; }
        .fw-markdown h5, .fw-markdown h6 { font-size: 0.92rem; }
        .fw-markdown p { margin: 0.7em 0; }
        .fw-markdown ul, .fw-markdown ol {
          margin: 0.5em 0;
          padding-left: 1.6em;
        }
        .fw-markdown li { margin: 0.2em 0; }
        .fw-markdown a {
          color: var(--primary);
          text-decoration: none;
          border-bottom: 1px dotted var(--primary);
        }
        .fw-markdown a:hover { border-bottom-style: solid; }
        .fw-markdown code {
          font-family: 'JetBrains Mono', ui-monospace, monospace;
          font-size: 0.85em;
          padding: 0.12em 0.4em;
          background: rgba(0, 0, 0, 0.05);
          border-radius: 3px;
        }
        .fw-markdown pre {
          background: #1f2429;
          color: #e6e9ef;
          padding: 14px 16px;
          border-radius: 4px;
          overflow-x: auto;
          font-size: 0.82rem;
          line-height: 1.55;
          margin: 1em 0;
        }
        .fw-markdown pre code {
          background: transparent;
          color: inherit;
          padding: 0;
          font-size: inherit;
        }
        .fw-markdown blockquote {
          margin: 1em 0;
          padding: 0.3em 0 0.3em 14px;
          border-left: 3px solid var(--primary);
          color: var(--text-muted);
        }
        .fw-markdown table {
          border-collapse: collapse;
          margin: 1em 0;
          font-size: 0.85rem;
        }
        .fw-markdown th, .fw-markdown td {
          border: 1px solid var(--border);
          padding: 6px 10px;
          text-align: left;
        }
        .fw-markdown th { background: rgba(0, 0, 0, 0.03); font-weight: 600; }
        .fw-markdown hr {
          border: 0;
          border-top: 1px solid var(--border);
          margin: 1.4em 0;
        }
        .fw-markdown img { max-width: 100%; height: auto; }
        .fw-page-card.viewing {
          background: rgba(196, 18, 48, 0.06);
          border-left: 3px solid var(--primary);
          padding-left: 11px;
        }
    """),
)


app, rt = fast_app(
    pico=False,
    htmx=False,
    default_hdrs=False,
    live=False,
    hdrs=HEAD,
)


# /api/{path:path} → reverse-proxy to FastAPI. Registered via the FastHTML
# `@rt` decorator with explicit methods so browser fetches reach FastAPI
# without needing a separate origin / CORS dance.
@rt(
    "/api/{path:path}",
    methods=["GET", "POST", "PUT", "DELETE", "PATCH", "HEAD", "OPTIONS"],
)
async def api_proxy(req: Request, path: str):
    return await _api_proxy(req)


# =============================================================================
# Reverse proxy: /api/* → FastAPI
# =============================================================================
# Browsers can't reach the in-cluster FastAPI service directly. Without this
# proxy, every `fetch('/api/...')` from our inline JS would hit FastHTML's
# port (23023) which has no /api routes, silently 404'ing with HTML and
# breaking the whole wizard.

_HOP_BY_HOP_REQ = frozenset({
    "host", "connection", "content-length", "transfer-encoding",
    "keep-alive", "te", "trailers", "upgrade",
    "proxy-authorization", "proxy-authenticate",
})
_HOP_BY_HOP_RESP = frozenset({
    "connection", "transfer-encoding", "keep-alive", "te", "trailers",
    "upgrade", "proxy-authenticate", "proxy-authorization",
})

_proxy_client: Optional[httpx.AsyncClient] = None


def _proxy_get_client() -> httpx.AsyncClient:
    """Lazy singleton — one connection pool reused across requests. Builds
    only when the first /api request comes in so cold-start stays cheap."""
    global _proxy_client
    if _proxy_client is None:
        _proxy_client = httpx.AsyncClient(
            base_url=FASTAPI_URL,
            timeout=httpx.Timeout(connect=10.0, read=120.0, write=10.0, pool=10.0),
            limits=httpx.Limits(max_connections=50, max_keepalive_connections=10),
            transport=httpx.AsyncHTTPTransport(retries=3),
        )
    return _proxy_client


async def _api_proxy(request: Request) -> StreamingResponse:
    """Forward `request` to FastAPI at its same path. Preserves method,
    headers (minus hop-by-hop), body, and query string. Streams the
    response back so large payloads don't balloon memory."""
    upstream_path = request.url.path
    if request.url.query:
        upstream_path = f"{upstream_path}?{request.url.query}"

    headers = {
        k: v for k, v in request.headers.items()
        if k.lower() not in _HOP_BY_HOP_REQ
    }
    body = await request.body()

    client = _proxy_get_client()
    upstream_req = client.build_request(
        method=request.method,
        url=upstream_path,
        headers=headers,
        content=body,
    )
    upstream_resp = await client.send(upstream_req, stream=True)

    response_headers = {
        k: v for k, v in upstream_resp.headers.items()
        if k.lower() not in _HOP_BY_HOP_RESP
    }

    async def _body_iter():
        try:
            async for chunk in upstream_resp.aiter_bytes():
                yield chunk
        finally:
            await upstream_resp.aclose()

    return StreamingResponse(
        _body_iter(),
        status_code=upstream_resp.status_code,
        headers=response_headers,
        media_type=upstream_resp.headers.get("content-type"),
    )


def _Shell(active_key: str, title_text: str, body=None):
    nav_links = [
        A(
            label,
            href=href,
            cls="nav-item active" if key == active_key else "nav-item",
        )
        for key, label, href in FEATURES
    ]
    return (
        Title("COELHO Nexus"),
        Div(
            Div(
                Div(
                    Div(
                        Span(cls="brand-flag"),
                        Span("COELHO Nexus"),
                        cls="brand",
                    ),
                    Div(*nav_links, cls="nav"),
                    cls="topbar",
                ),
                Div(
                    H1(title_text, cls="title"),
                    cls="title-row",
                ),
                Div(body if body is not None else "", cls="panel"),
                cls="card",
            ),
            cls="page",
        ),
    )


def _fetch_catalog() -> list[dict]:
    try:
        r = httpx.get(f"{FASTAPI_URL}/api/v1/docs-distiller/resolver", timeout=5.0)
        r.raise_for_status()
        return r.json()
    except Exception:
        return []


_PICKER_JS = """
(() => {
  const API = '/api/v1/docs-distiller';

  // -------- picker controls (Step 1) --------
  const search = document.querySelector('#fw-search');
  const chips = document.querySelectorAll('.fw-chip');
  const tiles = document.querySelectorAll('.fw-tile');
  const grid = document.querySelector('#fw-grid');
  const countEl = document.querySelector('#fw-count');
  const total = tiles.length;
  // -------- sticky bar --------
  const generate = document.querySelector('#fw-generate');
  const selectedName = document.querySelector('#fw-selected-name');
  const stickyBar = document.querySelector('#fw-sticky-bar');
  // -------- stepper --------
  const steps = document.querySelectorAll('.fw-step');
  const connectors = document.querySelectorAll('.fw-step-connector');
  const panels = document.querySelectorAll('.fw-step-panel');
  // -------- step 2 progress + file list --------
  const progressBox = document.querySelector('#fw-progress-box');
  const progressTier = document.querySelector('#fw-progress-tier');
  const progressStatus = document.querySelector('#fw-progress-status');
  const progressBar = document.querySelector('#fw-progress-bar');
  const progressFill = document.querySelector('#fw-progress-fill');
  const progressCounter = document.querySelector('#fw-progress-counter');
  const progressUrl = document.querySelector('#fw-progress-url');
  const cancelBtn = document.querySelector('#fw-cancel');
  const step2Summary = document.querySelector('#fw-step2-summary');
  const step2Grid = document.querySelector('#fw-step2-grid');
  // -------- step 3 manifest (mirror — also rendered for the future synth view) --------
  const pagesSummary = document.querySelector('#fw-pages-summary');
  const pageGrid = document.querySelector('#fw-page-grid');
  // -------- sidebar (library) --------
  const sidebar = document.querySelector('#fw-sidebar');
  const sidebarList = document.querySelector('#fw-sidebar-list');
  // -------- notice + toast --------
  const noticeEl = document.querySelector('#fw-cache-notice');
  const noticeText = document.querySelector('#fw-cache-notice-text');
  const toastEl = document.querySelector('#fw-denied-toast');
  const toastText = document.querySelector('#fw-denied-toast-text');
  const toastClose = document.querySelector('#fw-denied-toast-close');
  // -------- confirm modal --------
  const modalEl = document.querySelector('#fw-modal');
  const modalTitleEl = document.querySelector('#fw-modal-title');
  const modalMessageEl = document.querySelector('#fw-modal-message');
  const modalConfirmBtn = document.querySelector('#fw-modal-confirm');
  const modalCancelBtn = document.querySelector('#fw-modal-cancel');
  // -------- file-content drawer --------
  const drawerEl = document.querySelector('#fw-drawer');
  const drawerName = document.querySelector('#fw-drawer-name');
  const drawerMeta = document.querySelector('#fw-drawer-meta');
  const drawerBody = document.querySelector('#fw-drawer-body');
  const drawerPrev = document.querySelector('#fw-drawer-prev');
  const drawerNext = document.querySelector('#fw-drawer-next');
  const drawerClose = document.querySelector('#fw-drawer-close');

  // State
  let activeChip = 'All';
  let query = '';
  let selected = null;            // slug picked in Step 1
  let activeSlug = null;          // slug currently shown in Step 3
  let activeRunId = null;         // run currently being polled
  let pollAbort = false;
  let currentStep = 1;
  let farthestStep = 1;

  // ============================================================
  // Utility
  // ============================================================
  function sleep(ms) { return new Promise(r => setTimeout(r, ms)); }
  function fmtBytes(n) {
    if (!n) return '0 B';
    if (n < 1024) return n + ' B';
    if (n < 1024 * 1024) return (n / 1024).toFixed(1) + ' KB';
    return (n / (1024 * 1024)).toFixed(1) + ' MB';
  }
  function fmtAge(ts) {
    if (!ts) return '';
    const s = Math.max(1, Math.floor(Date.now() / 1000 - ts));
    if (s < 60) return s + 's ago';
    if (s < 3600) return Math.floor(s / 60) + 'm ago';
    if (s < 86400) return Math.floor(s / 3600) + 'h ago';
    return Math.floor(s / 86400) + 'd ago';
  }

  function showNotice(text) {
    noticeText.textContent = text;
    noticeEl.style.display = '';
    setTimeout(() => { noticeEl.style.display = 'none'; }, 8000);
  }
  function hideNotice() { noticeEl.style.display = 'none'; }
  function showToast(text) {
    toastText.textContent = text;
    toastEl.style.display = '';
  }
  function hideToast() { toastEl.style.display = 'none'; }
  toastClose.addEventListener('click', hideToast);

  // ---- in-page confirm modal (replacement for browser confirm()) ----
  let _modalResolver = null;
  function showConfirm(title, message, confirmLabel) {
    modalTitleEl.textContent = title;
    modalMessageEl.textContent = message;
    modalConfirmBtn.textContent = confirmLabel || 'Confirm';
    modalEl.classList.add('visible');
    return new Promise(resolve => { _modalResolver = resolve; });
  }
  function closeModal(result) {
    modalEl.classList.remove('visible');
    const r = _modalResolver;
    _modalResolver = null;
    if (r) r(result);
  }
  modalConfirmBtn.addEventListener('click', () => closeModal(true));
  modalCancelBtn.addEventListener('click', () => closeModal(false));
  modalEl.addEventListener('click', (e) => {
    // Click on the backdrop (outside the box) cancels.
    if (e.target === modalEl) closeModal(false);
  });
  document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape' && modalEl.classList.contains('visible')) {
      closeModal(false);
    }
  });

  // ---- file-content drawer (slide-out, right-anchored) ----
  let currentManifestEntries = [];
  let drawerIdx = -1;

  function openDrawer(idx) {
    if (!currentManifestEntries || currentManifestEntries.length === 0) return;
    if (idx < 0 || idx >= currentManifestEntries.length) return;
    drawerIdx = idx;
    drawerEl.classList.add('visible');
    renderDrawerContent();
  }
  function closeDrawer() {
    drawerEl.classList.remove('visible');
    document.querySelectorAll('.fw-page-card.viewing').forEach(
      c => c.classList.remove('viewing')
    );
  }
  function drawerStep(delta) {
    const next = drawerIdx + delta;
    if (next < 0 || next >= currentManifestEntries.length) return;
    drawerIdx = next;
    renderDrawerContent();
  }
  async function renderDrawerContent() {
    const e = currentManifestEntries[drawerIdx];
    if (!e || !activeSlug) { closeDrawer(); return; }
    drawerName.textContent = e.title || e.slug;
    drawerMeta.textContent =
      (e.tier || '') + ' · ' + fmtBytes(e.bytes) + ' · ' +
      (drawerIdx + 1) + ' of ' + currentManifestEntries.length;
    if (drawerIdx === 0) drawerPrev.setAttribute('disabled', 'disabled');
    else drawerPrev.removeAttribute('disabled');
    if (drawerIdx >= currentManifestEntries.length - 1) drawerNext.setAttribute('disabled', 'disabled');
    else drawerNext.removeAttribute('disabled');
    // Highlight the currently-viewing card across both step grids
    document.querySelectorAll('.fw-page-card.viewing').forEach(
      c => c.classList.remove('viewing')
    );
    document.querySelectorAll(
      '.fw-page-card[data-idx="' + e.idx + '"]'
    ).forEach(c => c.classList.add('viewing'));
    drawerBody.innerHTML = '<div class="fw-empty">Loading…</div>';
    try {
      const r = await fetch(API + '/ingestion/' + activeSlug +
                             '/pages/' + e.idx);
      if (!r.ok) {
        drawerBody.innerHTML =
          '<div class="fw-empty">Failed to load (HTTP ' + r.status + ')</div>';
        return;
      }
      const data = await r.json();
      const raw = data.body || '';
      const md = (typeof marked !== 'undefined')
        ? marked.parse(raw)
        : '<pre>' + raw.replace(/&/g, '&amp;').replace(/</g, '&lt;') + '</pre>';
      drawerBody.innerHTML = '<article class="fw-markdown">' + md + '</article>';
      drawerBody.scrollTop = 0;
    } catch (err) {
      drawerBody.innerHTML = '<div class="fw-empty">' + String(err) + '</div>';
    }
  }
  drawerPrev.addEventListener('click', () => drawerStep(-1));
  drawerNext.addEventListener('click', () => drawerStep(1));
  drawerClose.addEventListener('click', closeDrawer);
  document.addEventListener('keydown', (e) => {
    if (!drawerEl.classList.contains('visible')) return;
    // Don't hijack arrows when the user is typing in an input/textarea
    const tag = (document.activeElement?.tagName || '').toLowerCase();
    if (tag === 'input' || tag === 'textarea') return;
    if (e.key === 'Escape') closeDrawer();
    else if (e.key === 'ArrowLeft') drawerStep(-1);
    else if (e.key === 'ArrowRight') drawerStep(1);
  });
  // Click delegation — opens the drawer from any .fw-page-card in any grid
  document.addEventListener('click', (e) => {
    const card = e.target.closest('.fw-page-card');
    if (!card) return;
    const idx = parseInt(card.dataset.idx, 10);
    if (Number.isFinite(idx)) openDrawer(idx);
  });

  // ============================================================
  // Step 1: picker filtering + selection
  // ============================================================
  function applyFilter() {
    let visible = 0;
    tiles.forEach(t => {
      const name = t.dataset.name.toLowerCase();
      const cat = t.dataset.category;
      const matchQ = !query || name.includes(query);
      const matchC = activeChip === 'All' || cat === activeChip;
      const show = matchQ && matchC;
      t.style.display = show ? '' : 'none';
      if (show) visible++;
    });
    grid.classList.toggle('fw-grid-empty', visible === 0);
    countEl.textContent = visible + ' of ' + total;
  }
  search.addEventListener('input', e => {
    query = e.target.value.toLowerCase().trim();
    applyFilter();
  });
  chips.forEach(c => c.addEventListener('click', () => {
    chips.forEach(x => x.classList.remove('active'));
    c.classList.add('active');
    activeChip = c.dataset.chip;
    applyFilter();
  }));
  tiles.forEach(t => t.addEventListener('click', () => {
    // Tile selection always works (catalog stays interactive). Whether
    // the Generate button is clickable is governed by activeRunId.
    if (currentStep !== 1) return;
    tiles.forEach(x => x.classList.remove('selected'));
    t.classList.add('selected');
    selected = t.dataset.slug;
    selectedName.textContent = t.dataset.name;
    stickyBar.classList.add('visible');
    refreshGenerateState();
  }));

  // ============================================================
  // Stepper navigation
  // ============================================================
  function renderStepper() {
    steps.forEach((s, i) => {
      const n = i + 1;
      s.classList.remove('active', 'completed');
      if (n === currentStep) s.classList.add('active');
      else if (n <= farthestStep) s.classList.add('completed');
    });
    connectors.forEach((c, i) => {
      c.classList.toggle('complete', i + 1 < farthestStep);
    });
  }
  function showStep(n) {
    if (n > farthestStep) return;
    currentStep = n;
    panels.forEach((p, i) => p.classList.toggle('active', i + 1 === n));
    // Sticky bar appears on Step 1 whenever a tile is selected; Generate
    // enablement is controlled by `refreshGenerateState()`.
    stickyBar.classList.toggle('visible', n === 1 && selected !== null);
    // Step 2 — only show the live progress box during an active run;
    // pull the canonical manifest into the file list otherwise. While a
    // run is in flight the manifest doesn't exist yet (finalize happens
    // at the very end), so skip the fetch and show an "in progress"
    // placeholder — pollRun will paint the real file list on done.
    if (n === 2) {
      if (activeRunId !== null) {
        progressBox.style.display = '';
        step2Summary.innerHTML = '';
        step2Grid.innerHTML =
          '<div class="fw-empty">Ingestion in progress — materials will ' +
          'appear here when it completes.</div>';
      } else {
        progressBox.style.display = 'none';
        if (activeSlug) loadManifestForSlug(activeSlug);
      }
    }
    renderStepper();
  }

  function syncStepLocks() {
    // Step 2/3 unlock when EITHER an ingestion is running OR the library
    // has at least one finalized framework. Otherwise lock back to Step 1.
    const hasLibrary =
      sidebarList.querySelectorAll('.fw-lib-item').length > 0;
    const ingestActive = activeRunId !== null;
    if (hasLibrary || ingestActive) {
      farthestStep = Math.max(farthestStep, 3);
    } else {
      farthestStep = 1;
      if (currentStep !== 1) {
        currentStep = 1;
        panels.forEach((p, i) => p.classList.toggle('active', i + 1 === 1));
        stickyBar.classList.toggle('visible', selected !== null);
      }
    }
    renderStepper();
  }

  function refreshGenerateState() {
    // Disable Start Ingestion + every sidebar Refresh button while an
    // ingestion is in flight — prevents parallel POST /runs that would
    // queue + immediately be denied by the single-flight lock anyway.
    const ingestActive = activeRunId !== null;
    if (!selected || ingestActive) {
      generate.setAttribute('disabled', 'disabled');
    } else {
      generate.removeAttribute('disabled');
    }
    document.querySelectorAll('.fw-lib-refresh, .fw-lib-delete').forEach(b => {
      if (ingestActive) {
        b.setAttribute('disabled', 'disabled');
      } else {
        b.removeAttribute('disabled');
      }
    });
  }
  function advance() {
    if (currentStep >= 3) return;
    farthestStep = Math.max(farthestStep, currentStep + 1);
    showStep(currentStep + 1);
  }
  function jumpTo(step) {
    farthestStep = Math.max(farthestStep, step);
    showStep(step);
  }
  steps.forEach((s, i) => s.addEventListener('click', () => {
    const target = i + 1;
    if (target <= farthestStep) showStep(target);
  }));

  // ============================================================
  // Step 3: render manifest entries into the page grid
  // ============================================================
  function renderManifestTo(summaryEl, gridEl, m) {
    if (!m || !m.entries) {
      gridEl.innerHTML = '<div class="fw-empty">Manifest unavailable.</div>';
      if (summaryEl) summaryEl.innerHTML = '';
      return;
    }
    // Track the current entry list so the drawer's prev/next + click
    // delegation walk the same list the user is looking at.
    currentManifestEntries = m.entries;
    if (summaryEl) {
      summaryEl.innerHTML =
        '<span><strong>' + (m.framework_name || activeSlug) + '</strong> · ' +
        (m.entries.length) + ' pages · ' + fmtBytes(m.total_bytes || 0) + '</span>' +
        '<span>' + (m.tier_kind || '') + ' · ' + fmtAge(m.ingested_at) + '</span>';
    }
    gridEl.innerHTML = m.entries.map(e =>
      '<div class="fw-page-card" data-idx="' + e.idx + '">' +
      '<div class="fw-page-title">' + (e.title || e.slug) + '</div>' +
      '<div class="fw-page-meta">' + (e.tier || '') + ' · ' + fmtBytes(e.bytes) + '</div>' +
      '</div>'
    ).join('');
  }

  // Backward-compat wrapper — historical callers target Step 3.
  function renderManifest(m) {
    renderManifestTo(pagesSummary, pageGrid, m);
    renderManifestTo(step2Summary, step2Grid, m);
  }

  async function loadManifestForSlug(slug) {
    activeSlug = slug;
    try {
      const r = await fetch(API + '/ingestion/' + slug + '/manifest');
      if (!r.ok) {
        const msg = '<div class="fw-empty">Manifest fetch failed (HTTP ' +
          r.status + ').</div>';
        pageGrid.innerHTML = msg;
        step2Grid.innerHTML = msg;
        return;
      }
      renderManifest(await r.json());
    } catch (e) {
      const msg = '<div class="fw-empty">' + String(e) + '</div>';
      pageGrid.innerHTML = msg;
      step2Grid.innerHTML = msg;
    }
  }

  // ============================================================
  // Step 2: progress display + polling
  // ============================================================
  function renderProgress(p) {
    if (!p) return;
    progressTier.textContent = p.tier || '—';
    progressStatus.textContent = p.status || '—';
    progressUrl.textContent = p.last_url || '';
    if (p.total && p.total > 0) {
      progressBar.classList.remove('indeterminate');
      const pct = Math.min(100, Math.round((p.current / p.total) * 100));
      progressFill.style.width = pct + '%';
      progressCounter.textContent =
        (p.current || 0) + ' / ' + p.total + ' (' + pct + '%)';
    } else {
      progressBar.classList.add('indeterminate');
      progressFill.style.width = '35%';
      progressCounter.textContent = (p.current || 0) + ' so far…';
    }
  }

  async function pollRun(runId) {
    pollAbort = false;
    activeRunId = runId;
    refreshGenerateState();   // disable Generate while this run is in flight
    progressBox.style.display = '';   // reveal the live progress display
    while (!pollAbort && activeRunId === runId) {
      try {
        const r = await fetch(API + '/runs/' + runId);
        if (r.status === 404) { await sleep(800); continue; }
        const data = await r.json();
        renderProgress(data.progress);
        const st = data.progress?.status;
        if (st === 'done') {
          activeRunId = null;
          refreshGenerateState();
          await loadManifestForSlug(activeSlug);
          await loadLibrary();
          jumpTo(3);
          return;
        }
        if (st === 'failed' || st === 'cancelled') {
          activeRunId = null;
          refreshGenerateState();
          await loadLibrary();
          showToast('Ingestion ' + st + '. ' +
            (st === 'cancelled' ? 'Partial pages cleared from storage.' : ''));
          return;
        }
      } catch (e) {
        // transient — retry
      }
      await sleep(1500);
    }
  }

  cancelBtn.addEventListener('click', async () => {
    if (!activeRunId) return;
    cancelBtn.disabled = true;
    try {
      await fetch(API + '/runs/' + activeRunId + '/cancel', {method: 'POST'});
    } finally {
      // Poll loop will pick up the cancelled status and surface a toast.
      cancelBtn.disabled = false;
    }
  });

  // ============================================================
  // POST /runs — Generate / Refresh
  // ============================================================
  async function triggerIngest(slug, refresh) {
    hideToast(); hideNotice();
    activeSlug = slug;
    try {
      const r = await fetch(API + '/runs', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({slug: slug, refresh: !!refresh}),
      });
      const data = await r.json();
      if (data.status === 'cached') {
        renderManifest(data.manifest);
        showNotice('Loaded from cache · ingested ' +
          fmtAge(data.manifest?.ingested_at) +
          '. Click ↻ in the sidebar to refresh.');
        farthestStep = 3;
        showStep(3);
        return;
      }
      if (data.status === 'queued') {
        // Claim activeRunId synchronously so showStep(2) doesn't race
        // pollRun and try to fetch the (not-yet-finalized) manifest.
        activeRunId = data.run_id;
        refreshGenerateState();
        jumpTo(2);
        pollRun(data.run_id);
        return;
      }
      if (data.status === 'locked') {
        showToast(data.message || 'Another ingestion is already running for this framework.');
        return;
      }
      showToast('Unexpected response: ' + JSON.stringify(data));
    } catch (e) {
      showToast('Request failed: ' + String(e));
    }
  }

  generate.addEventListener('click', () => {
    if (!selected) return;
    triggerIngest(selected, false);
  });

  // ============================================================
  // Sidebar — library list
  // ============================================================
  function renderSidebar(items) {
    if (!items || items.length === 0) {
      sidebarList.innerHTML =
        '<div class="fw-sidebar-empty">' +
        'No ingested frameworks yet. Pick one in the catalog and click Start Ingestion.' +
        '</div>';
      return;
    }
    const html = items.map(it => {
      const isActive = (it.slug === activeSlug) ? ' active' : '';
      const logo = it.logo
        ? '<img class="fw-lib-logo" src="' + it.logo + '" alt="">'
        : '';
      return '<div class="fw-lib-item' + isActive + '" data-slug="' + it.slug + '">' +
        logo +
        '<div style="flex:1;min-width:0">' +
        '<div class="fw-lib-name">' + (it.framework_name || it.slug) + '</div>' +
        '<div class="fw-lib-meta">' + (it.page_count || 0) + ' pages · ' +
        fmtAge(it.ingested_at) + '</div>' +
        '</div>' +
        '<button class="fw-lib-refresh" data-slug="' + it.slug +
        '" title="Refresh (re-download)">↻</button>' +
        '<button class="fw-lib-delete" data-slug="' + it.slug +
        '" title="Delete this ingestion">🗑</button>' +
        '</div>';
    }).join('');
    sidebarList.innerHTML = html;
    sidebarList.querySelectorAll('.fw-lib-item').forEach(el => {
      el.addEventListener('click', async ev => {
        if (ev.target.closest('.fw-lib-refresh, .fw-lib-delete')) return;
        const slug = el.dataset.slug;
        sidebarList.querySelectorAll('.fw-lib-item').forEach(
          x => x.classList.remove('active'));
        el.classList.add('active');
        await loadManifestForSlug(slug);
        farthestStep = Math.max(farthestStep, 3);
        showStep(3);
      });
    });
    sidebarList.querySelectorAll('.fw-lib-refresh').forEach(b => {
      b.addEventListener('click', ev => {
        ev.stopPropagation();
        triggerIngest(b.dataset.slug, true);
      });
    });
    // Newly-rendered refresh buttons must pick up the current ingest state
    // (a re-render from loadLibrary() during an active run would otherwise
    // give them a fresh enabled state).
    refreshGenerateState();
    sidebarList.querySelectorAll('.fw-lib-delete').forEach(b => {
      b.addEventListener('click', async ev => {
        ev.stopPropagation();
        const slug = b.dataset.slug;
        const row = b.closest('.fw-lib-item');
        const displayName = row.querySelector('.fw-lib-name')?.textContent || slug;

        const ok = await showConfirm(
          'Delete ingestion',
          'Permanently delete "' + displayName + '"? ' +
          'Wipes the manifest + every page body from MinIO. ' +
          'This cannot be undone.',
          'Delete'
        );
        if (!ok) return;

        // Replace 🗑 with spinner + lock the row so a stray click can't
        // re-fire delete or jump to another framework mid-DELETE.
        const refresh = row.querySelector('.fw-lib-refresh');
        const originalLabel = b.innerHTML;
        b.innerHTML = '<div class="fw-spinner"></div>';
        b.setAttribute('disabled', 'disabled');
        if (refresh) refresh.setAttribute('disabled', 'disabled');
        row.style.pointerEvents = 'none';
        row.style.opacity = '0.7';

        try {
          const r = await fetch(API + '/ingestion/' + slug, {method: 'DELETE'});
          if (!r.ok) throw new Error('HTTP ' + r.status);

          // Clear Step 3 if the deleted framework was the one being viewed.
          if (activeSlug === slug) {
            activeSlug = null;
            pageGrid.innerHTML =
              '<div class="fw-empty">Pick an item from the sidebar or ' +
              'generate a new study.</div>';
            pagesSummary.innerHTML = '';
          }
          // Remove the row in place — snappier than a full library reload.
          row.remove();
          if (sidebarList.querySelectorAll('.fw-lib-item').length === 0) {
            sidebarList.innerHTML =
              '<div class="fw-sidebar-empty">' +
              'No ingested frameworks yet. Pick one in the catalog and ' +
              'click Start Ingestion.' +
              '</div>';
          }
          syncStepLocks();   // library may now be empty → lock Steps 2+3
        } catch (e) {
          // Restore on failure so the user can try again.
          b.innerHTML = originalLabel;
          b.removeAttribute('disabled');
          if (refresh) refresh.removeAttribute('disabled');
          row.style.pointerEvents = '';
          row.style.opacity = '';
          showToast('Delete failed: ' + String(e));
        }
      });
    });
  }

  async function loadLibrary() {
    try {
      const r = await fetch(API + '/ingestion');
      if (!r.ok) { renderSidebar([]); syncStepLocks(); return; }
      renderSidebar(await r.json());
    } catch (e) {
      renderSidebar([]);
    }
    syncStepLocks();   // unlock/lock Steps 2+3 based on library presence
  }

  // ============================================================
  // Init
  // ============================================================
  countEl.textContent = total + ' of ' + total;
  renderStepper();
  refreshGenerateState();   // initial pass — disabled until a tile is picked
  loadLibrary();
})();
"""


def _Step(n: int, label: str, active: bool = False):
    cls = "fw-step active" if active else "fw-step"
    return Div(
        Span(str(n), cls="fw-step-circle"),
        Span(label, cls="fw-step-label"),
        cls=cls,
        id=f"fw-step-{n}",
        data_step=str(n),
    )


def _Picker():
    catalog = _fetch_catalog()
    if not catalog:
        return Div(
            P(
                "Could not load the framework catalog. "
                "Make sure FastAPI is reachable at /api/v1/docs-distiller/resolver.",
                cls="fw-empty",
            ),
            cls="fw-picker",
        )

    cats = sorted({(f.get("category") or "Other") for f in catalog})
    chips = [Span("All", cls="fw-chip active", data_chip="All")] + [
        Span(c, cls="fw-chip", data_chip=c) for c in cats
    ]

    def _tile(f):
        children = []
        if f.get("logo"):
            children.append(Img(src=f["logo"], alt="", cls="fw-tile-logo"))
        children.append(Div(f["name"], cls="fw-tile-name"))
        children.append(Div(f.get("category") or "—", cls="fw-tile-cat"))
        return Div(
            *children,
            cls="fw-tile",
            data_name=f["name"],
            data_slug=f["slug"],
            data_category=(f.get("category") or "Other"),
        )

    tiles = [_tile(f) for f in catalog]

    # Step 1 — catalog picker (always visible, never locked)
    step1_edit = Div(
        Div(
            Input(
                type="search", id="fw-search",
                placeholder=f"Search {len(catalog)} frameworks…",
                autocomplete="off", autofocus=True,
                cls="fw-search",
            ),
            Span("", id="fw-count", cls="fw-count"),
            cls="fw-search-row",
        ),
        Div(*chips, cls="fw-chips"),
        Div(*tiles, cls="fw-grid", id="fw-grid"),
        id="fw-step-1-edit",
    )

    # Step 2 — live progress (visible only during a run) + cached file list
    step2_body = Div(
        Div(
            Span("", id="fw-cache-notice-text", cls="fw-notice-text"),
            id="fw-cache-notice", cls="fw-notice", style="display:none;",
        ),
        Div(
            Span("", id="fw-denied-toast-text", cls="fw-toast-text"),
            Button("✕", id="fw-denied-toast-close", cls="fw-toast-close"),
            id="fw-denied-toast", cls="fw-toast", style="display:none;",
        ),
        # Live progress display — JS hides it when activeRunId is null
        Div(
            Div(
                Span("—", id="fw-progress-tier", cls="fw-progress-tier"),
                Span("idle", id="fw-progress-status", cls="fw-progress-status"),
                cls="fw-progress-head",
            ),
            Div(
                Div(cls="fw-progress-fill", id="fw-progress-fill"),
                cls="fw-progress-bar indeterminate", id="fw-progress-bar",
            ),
            Div(
                Span("", id="fw-progress-counter"),
                Span(""),
                cls="fw-progress-meta",
            ),
            Div("", id="fw-progress-url", cls="fw-progress-url"),
            Div(
                Button("Cancel ingestion", id="fw-cancel", cls="btn-outline"),
                cls="fw-progress-actions",
            ),
            id="fw-progress-box", cls="fw-progress",
        ),
        # File list — populated from the canonical MinIO manifest whenever
        # the user navigates to Step 2 with an active framework selection.
        Div("", id="fw-step2-summary", cls="fw-pages-summary"),
        Div(
            Div(
                "Pick a framework in the catalog or the sidebar to see "
                "its downloaded files.",
                cls="fw-empty",
            ),
            id="fw-step2-grid", cls="fw-page-grid",
        ),
    )

    # Step 3 — page grid (rendered by JS from /ingestion/{slug}/manifest)
    step3_body = Div(
        Div(id="fw-pages-summary", cls="fw-pages-summary"),
        Div(
            Div(
                "Pick an item from the sidebar or generate a new study.",
                cls="fw-empty",
            ),
            id="fw-page-grid", cls="fw-page-grid",
        ),
    )

    return Div(
        # Stepper row + "+ New Study"
        Div(
            Div(
                _Step(1, "Catalog", active=True),
                Span(cls="fw-step-connector"),
                _Step(2, "Ingestion"),
                Span(cls="fw-step-connector"),
                _Step(3, "Study"),
                cls="fw-stepper",
            ),
            cls="fw-stepper-row",
        ),

        # Layout: sidebar + main step content
        Div(
            # Sidebar (library) — hidden visually on Step 1 by CSS would be
            # nice, but the simplest path is always-rendered + JS toggles.
            # Sidebar is harmless on Step 1 (just adds context) so leave it.
            Div(
                P("Library", cls="fw-sidebar-title"),
                Div(
                    Div("Loading…", cls="fw-sidebar-empty"),
                    id="fw-sidebar-list",
                ),
                id="fw-sidebar", cls="fw-sidebar",
            ),
            # Main panel — holds the 3 step panels
            Div(
                Div(
                    step1_edit,
                    id="fw-step-1-panel", cls="fw-step-panel active",
                ),
                Div(step2_body, id="fw-step-2-panel", cls="fw-step-panel"),
                Div(step3_body, id="fw-step-3-panel", cls="fw-step-panel"),
                cls="fw-main",
            ),
            cls="fw-layout",
        ),

        # Sticky bar (Step 1 → Generate)
        Div(
            Span(
                "Selected: ",
                Span("", id="fw-selected-name", cls="fw-selected-name"),
                id="fw-selected-label", cls="fw-selected-label",
            ),
            Button("Start Ingestion", id="fw-generate", cls="btn-primary"),
            id="fw-sticky-bar", cls="fw-sticky-bar",
        ),
        # Generic confirm modal (reused by delete + future destructive actions)
        Div(
            Div(
                Div("", id="fw-modal-title", cls="fw-modal-title"),
                P("", id="fw-modal-message", cls="fw-modal-message"),
                Div(
                    Button("Cancel", id="fw-modal-cancel", cls="btn-outline"),
                    Button("Confirm", id="fw-modal-confirm", cls="btn-primary"),
                    cls="fw-modal-actions",
                ),
                cls="fw-modal",
            ),
            id="fw-modal", cls="fw-modal-backdrop",
        ),
        # File-content drawer (right-anchored slide-out). One instance; the
        # JS pages it through the current manifest's entries via prev/next.
        Div(
            Div(
                Div(
                    Div("", id="fw-drawer-name", cls="fw-drawer-name"),
                    Div("", id="fw-drawer-meta", cls="fw-drawer-meta"),
                    cls="fw-drawer-title",
                ),
                Div(
                    Button("◀", id="fw-drawer-prev",
                           cls="fw-drawer-btn", title="Previous (←)"),
                    Button("▶", id="fw-drawer-next",
                           cls="fw-drawer-btn", title="Next (→)"),
                    Button("✕", id="fw-drawer-close",
                           cls="fw-drawer-btn", title="Close (Esc)"),
                    cls="fw-drawer-controls",
                ),
                cls="fw-drawer-header",
            ),
            Div("", id="fw-drawer-body", cls="fw-drawer-body"),
            id="fw-drawer", cls="fw-drawer",
        ),
        Script(_PICKER_JS),
        cls="fw-picker",
    )


@rt("/")
def index():
    return _Shell("docs-distiller", "Docs Distiller", body=_Picker())


@rt("/docs-distiller")
def docs_distiller():
    return _Shell("docs-distiller", "Docs Distiller", body=_Picker())


@rt("/youtube-content-search")
def youtube_search():
    return _Shell("youtube-content-search", "YouTube Content Search")


@rt("/coming-soon")
def coming_soon():
    return _Shell("coming-soon", "Coming Soon")


@rt("/health")
def health():
    return PlainTextResponse("OK")


if __name__ == "__main__":
    serve()
