// NVIDIA NIM API key — standalone field. Unrelated to chat routing (the
// LLM Endpoint card): this is what YCS embeddings + reranking reads
// directly (domains/llm/rotator/discovery, pid="nim"). Replaces the old
// 7-provider chat registry UI (removed 2026-09-11 — chat routing is now
// always the externally-deployed COELHO LLM Rotator via the LLM Endpoint
// field), but reuses the SAME generic /providers/nim/{key,test} endpoints
// that registry used, so no backend change was needed for this field.

const API = "/api/v1/llm/settings";
const PID = "nim";

const $ = (id) => document.getElementById(id);

function setStatus(msg, kind = "") {
  const s = $("set-nim-status");
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
    const detail = data && (data.detail?.message || data.detail || data.error);
    throw new Error(typeof detail === "string" ? detail : `HTTP ${res.status}`);
  }
  return data;
}

// Raw keys are write-only (never returned) — this pill is what "shows up"
// for a previously-saved key, same pattern as Source Tool Keys' pill.
function renderKeyStatus(p) {
  const pill = $("set-nim-key-status");
  if (!pill) return;
  if (p && p.has_key && p.source === "user") {
    pill.className = "set-pill set-pill-ok";
    pill.textContent = `Custom key ••••${p.last4 || ""}`;
  } else if (p && p.has_key && p.source === "env") {
    pill.className = "set-pill set-pill-env";
    pill.textContent = `Env key ••••${p.last4 || ""}`;
  } else {
    pill.className = "set-pill set-pill-none";
    pill.textContent = "No key";
  }
}

function render(p) {
  const input = $("set-nim-key");
  if (!input) return;
  input.value = "";
  renderKeyStatus(p);
}

async function load() {
  try {
    const data = await api("GET", "/providers");
    const p = (data.providers || []).find((x) => x.id === PID);
    render(p);
  } catch (e) {
    setStatus(`Couldn't load: ${e.message}`, "err");
  }
}

async function save() {
  const key = $("set-nim-key").value.trim();
  if (!key) { setStatus("Paste a key first.", "err"); return; }
  const btn = $("set-nim-save");
  btn.disabled = true;
  setStatus("Saving…");
  try {
    const res = await api("POST", `/providers/${PID}/key`, { api_key: key });
    render(res.key);
    setStatus(`Saved (${res.probe.status}).`, "ok");
    toast("NVIDIA API key saved");
  } catch (e) {
    const probe = e.detail && e.detail.probe;
    setStatus(`Save failed: ${probe ? probe.status : e.message}`, "err");
  } finally {
    btn.disabled = false;
  }
}

async function test() {
  const btn = $("set-nim-test");
  btn.disabled = true;
  setStatus("Testing…");
  try {
    const probe = await api("POST", `/providers/${PID}/test`);
    const map = {
      reachable: ["ok", `Reachable · ${probe.n_free_models} free model(s)`],
      rate_limited: ["ok", "Valid (rate-limited)"],
      invalid_key: ["err", "Invalid key (401/403)"],
      missing_key: ["err", "No key set"],
      unreachable: ["err", "Unreachable"],
    };
    const [kind, label] = map[probe.status] || ["", probe.status || "unknown"];
    setStatus(label, kind);
  } catch (e) {
    setStatus(`Test failed: ${e.message}`, "err");
  } finally {
    btn.disabled = false;
  }
}

async function remove() {
  const btn = $("set-nim-remove");
  btn.disabled = true;
  setStatus("Removing…");
  try {
    const res = await api("DELETE", `/providers/${PID}/key`);
    render(res);
    setStatus("Removed (reverted to env).", "ok");
    toast("NVIDIA API key removed");
  } catch (e) {
    setStatus(`Remove failed: ${e.message}`, "err");
  } finally {
    btn.disabled = false;
  }
}

function init() {
  const save_ = $("set-nim-save");
  if (!save_) return;
  save_.addEventListener("click", save);
  $("set-nim-test").addEventListener("click", test);
  $("set-nim-remove").addEventListener("click", remove);
  load();
}

if (document.readyState === "loading") {
  document.addEventListener("DOMContentLoaded", init);
} else {
  init();
}
