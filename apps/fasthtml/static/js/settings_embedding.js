// Embedding Endpoint card — the OpenAI-compatible URL YCS calls for
// embeddings. Independent connection from the LLM Endpoint card (own
// URL/key/model) — mirrors settings_endpoint.js exactly. The API key is
// write-only: it leaves the browser on Save and never comes back (GET
// returns masked status).

const API = "/api/v1/settings";
// Migration status/trigger + task polling live under the YCS content
// router (`domains.ycs.embedding_migration`), not the settings router —
// reused as-is rather than duplicated, since it's also what the
// Ingestion-page dispatch gate calls server-side.
const YCS_API = "/api/v1/ycs/content";
const ADMIN_API = "/api/v1/ycs/admin";

const $ = (id) => document.getElementById(id);

function setStatus(msg, kind = "") {
  const s = $("set-emb-status");
  if (!s) return;
  s.textContent = msg || "";
  s.className = "set-ep-status" + (kind ? ` set-ep-status-${kind}` : "");
}

function toast(msg, kind = "ok") {
  const t = $("set-toast");
  if (!t) return;
  t.textContent = msg;
  t.className = `set-toast show set-toast-${kind}`;
  setTimeout(() => (t.className = "set-toast"), 3200);
}

async function api(method, path, body) {
  const opts = { method, headers: {} };
  if (body !== undefined) {
    opts.headers["Content-Type"] = "application/json";
    opts.body = JSON.stringify(body);
  }
  const res = await fetch(API + path, opts);
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    const detail = data && (data.detail || data.error);
    throw new Error(typeof detail === "string" ? detail : `HTTP ${res.status}`);
  }
  return data;
}

// Raw keys are write-only (never returned by the backend), so the field
// itself always stays empty — this pill is what "shows up" for a
// previously-saved key.
function renderKeyStatus(v) {
  const pill = $("set-emb-key-status");
  if (!pill) return;
  if (v.has_key && v.source === "user") {
    pill.className = "set-pill set-pill-ok";
    pill.textContent = `Custom key ••••${v.last4 || ""}`;
  } else if (v.has_key && v.source === "env") {
    pill.className = "set-pill set-pill-env";
    pill.textContent = `Env key ••••${v.last4 || ""}`;
  } else {
    pill.className = "set-pill set-pill-none";
    pill.textContent = "No key";
  }
}

function render(v) {
  $("set-emb-url").value = v.url || "";
  $("set-emb-model").value = v.model || "auto";
  $("set-emb-key").value = "";
  renderKeyStatus(v);
}

async function load() {
  try {
    render(await api("GET", "/embedding"));
  } catch (e) {
    setStatus(`Couldn't load: ${e.message}`, "err");
  }
}

async function save() {
  const url = $("set-emb-url").value.trim();
  if (!url) {
    setStatus("Base URL is required.", "err");
    return;
  }
  const keyField = $("set-emb-key").value;
  const body = {
    url,
    model: $("set-emb-model").value.trim() || "auto",
    // undefined → key unchanged; "" would clear it (not exposed here yet)
    ...(keyField ? { api_key: keyField } : {}),
  };
  const btn = $("set-emb-save");
  btn.disabled = true;
  setStatus("Saving…");
  try {
    render(await api("PUT", "/embedding", body));
    setStatus("Saved.", "ok");
    toast("Embedding endpoint updated");
    loadMigrationStatus();
  } catch (e) {
    setStatus(`Save failed: ${e.message}`, "err");
  } finally {
    btn.disabled = false;
  }
}

async function test() {
  const btn = $("set-emb-test");
  btn.disabled = true;
  setStatus("Testing…");
  try {
    const r = await api("POST", "/embedding/test");
    if (r.ok) {
      setStatus(
        `OK — ${r.latency_ms} ms · ${r.dimensions}d` +
          ((r.model || r.deployment) ? ` · ${r.model || r.deployment}` : ""),
        "ok",
      );
    } else {
      setStatus(`Failed: ${r.error || "unknown"}`, "err");
    }
  } catch (e) {
    setStatus(`Test failed: ${e.message}`, "err");
  } finally {
    btn.disabled = false;
  }
}

// ---- Browse available embedding models — RETIRED --------------------------
// Live discovery used to live behind `/embedding/candidates` +
// `/embedding/recommend` (a gateway-specific advisory surface, removed with
// it). The Model field is now a plain text input: paste any model id the
// configured endpoint serves. The popover + button below are hidden;
// kept (not deleted) so the server-rendered skeleton has no dangling ids.
function htmlEscape(s) {
  return String(s ?? "").replace(/[&<>"']/g, (c) => (
    { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]
  ));
}

async function loadBrowseModels() {
  const list = $("set-emb-browse-list");
  if (!list) return;
  list.innerHTML =
    '<div class="set-emb-browse-empty">Model catalog browsing was retired ' +
    "with the gateway — type the model id directly.</div>";
}

function openBrowseModels() {
  $("set-emb-browse-popover")?.classList.add("visible");
  loadBrowseModels();
}

function closeBrowseModels() {
  $("set-emb-browse-popover")?.classList.remove("visible");
}

function bindBrowseModels() {
  // Hide the Browse button — no catalog backend exists anymore.
  const btn = $("set-emb-browse");
  if (btn) btn.style.display = "none";
  $("set-emb-browse-close")?.addEventListener("click", closeBrowseModels);
  $("set-emb-browse-list")?.addEventListener("click", (ev) => {
    const row = ev.target.closest?.(".set-emb-browse-row");
    if (!row) return;
    const modelField = $("set-emb-model");
    if (modelField) modelField.value = row.dataset.pinnedId;
    closeBrowseModels();
  });
}

// ---- Embedding migration — status banner + trigger + progress ------------
// Gate lives server-side (every ingestion-dispatch endpoint 423s while a
// migration is needed/running — see `api/v1/ycs/content/router.py
// ::_raise_if_embedding_migration_needed`); this is the UI half: show the
// read-only active-collection name always, and a banner + "Migrate now"
// button only when `needed` or a migration is already in flight.
let _migrationPollTimer = null;

function stopMigrationPoll() {
  if (_migrationPollTimer) {
    clearTimeout(_migrationPollTimer);
    _migrationPollTimer = null;
  }
}

function renderMigrationBanner(status) {
  const banner = $("set-emb-migration-banner");
  const collectionLine = $("set-emb-migration-collection");
  if (!banner || !collectionLine) return;
  collectionLine.textContent = status.active_collection
    ? `Active Qdrant collection: ${status.active_collection}`
    : "";

  const running = status.state && status.state.status === "running";
  if (!status.needed && !running) {
    banner.innerHTML = "";
    banner.className = "set-emb-migration-banner";
    return;
  }
  banner.className = "set-emb-migration-banner set-emb-migration-banner-visible";
  if (running) {
    banner.innerHTML = `
      <div class="set-emb-migration-text">
        Migrating ${htmlEscape(status.state.from_model)} → ${htmlEscape(status.state.to_model)}…
      </div>
      <div class="set-emb-migration-progress" id="set-emb-migration-progress">Starting…</div>`;
    pollMigrationTask(status.state.task_id);
    return;
  }
  const m = status.mismatch || {};
  banner.innerHTML = `
    <div class="set-emb-migration-text">
      Embedding model changed from <b>${htmlEscape(m.from_model)}</b> to
      <b>${htmlEscape(m.to_model)}</b>. Previously-ingested videos won't be
      searchable under the new model until you migrate them.
    </div>
    <button type="button" class="set-btn set-btn-primary" id="set-emb-migration-start">Migrate now</button>`;
  $("set-emb-migration-start")?.addEventListener("click", startMigration);
}

async function loadMigrationStatus() {
  try {
    const res = await fetch(`${YCS_API}/embedding-migration/status`);
    const status = await res.json();
    renderMigrationBanner(status);
  } catch (e) {
    // Best-effort — a failed status check shouldn't block the rest of
    // the Settings page from working.
    console.warn("[settings:embedding] migration status check failed", e);
  }
}

async function startMigration() {
  const btn = $("set-emb-migration-start");
  if (btn) {
    btn.disabled = true;
    btn.textContent = "Starting…";
  }
  try {
    const res = await fetch(`${YCS_API}/embedding-migration/start`, { method: "POST" });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) {
      throw new Error((data && (data.detail?.message || data.detail || data.error)) || `HTTP ${res.status}`);
    }
    toast("Migration started");
    await loadMigrationStatus();
  } catch (e) {
    toast(`Migration failed to start: ${e.message}`, "err");
    if (btn) {
      btn.disabled = false;
      btn.textContent = "Migrate now";
    }
  }
}

async function pollMigrationTask(taskId) {
  stopMigrationPoll();
  if (!taskId) return;
  const tick = async () => {
    let data;
    try {
      const res = await fetch(`${ADMIN_API}/task/${encodeURIComponent(taskId)}`);
      data = await res.json();
    } catch {
      _migrationPollTimer = setTimeout(tick, 3000);
      return;
    }
    const progressEl = $("set-emb-migration-progress");
    if (progressEl) {
      const meta = data.meta || {};
      if (data.state === "PROGRESS" && meta.total) {
        progressEl.textContent = `${meta.current ?? 0}/${meta.total} transcripts re-embedded`;
      } else {
        progressEl.textContent = data.state || "Running…";
      }
    }
    if (["SUCCESS", "FAILURE", "REVOKED"].includes(data.state)) {
      stopMigrationPoll();
      if (data.state === "SUCCESS") {
        toast("Embedding migration complete");
      } else {
        toast("Embedding migration failed — check Flower/Celery logs", "err");
      }
      // Re-check: on SUCCESS the finalize-cutover task (linked, runs
      // right after) needs a moment to land — the status check below
      // naturally reflects whichever is true by the time it lands.
      setTimeout(loadMigrationStatus, 2000);
      return;
    }
    _migrationPollTimer = setTimeout(tick, 3000);
  };
  tick();
}

function init() {
  const save_ = $("set-emb-save");
  const test_ = $("set-emb-test");
  if (!save_ || !test_) return;
  save_.addEventListener("click", save);
  test_.addEventListener("click", test);
  bindBrowseModels();
  load();
  loadMigrationStatus();
}

if (document.readyState === "loading") {
  document.addEventListener("DOMContentLoaded", init);
} else {
  init();
}
