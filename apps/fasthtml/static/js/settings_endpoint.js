// LLM Endpoint card — the OpenAI-compatible URL the Docs Distiller calls.
// Talks to FastAPI through the /api reverse proxy. The API key is write-only:
// it leaves the browser on Save and never comes back (GET returns masked status).

const API = "/api/v1/llm/settings";

const $ = (id) => document.getElementById(id);

function setStatus(msg, kind = "") {
  const s = $("set-ep-status");
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
// previously-saved key: same has_key/source/last4 status Source Tool Keys
// shows via its own status pill.
function renderKeyStatus(v) {
  const pill = $("set-ep-key-status");
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
  $("set-ep-url").value = v.url || "";
  $("set-ep-model").value = v.model || "auto";
  $("set-ep-key").value = "";
  renderKeyStatus(v);
}

async function load() {
  try {
    render(await api("GET", "/endpoint"));
  } catch (e) {
    setStatus(`Couldn't load: ${e.message}`, "err");
  }
}

async function save() {
  const url = $("set-ep-url").value.trim();
  if (!url) {
    setStatus("Base URL is required.", "err");
    return;
  }
  const keyField = $("set-ep-key").value;
  const body = {
    url,
    model: $("set-ep-model").value.trim() || "auto",
    // undefined → key unchanged; "" would clear it (not exposed here yet)
    ...(keyField ? { api_key: keyField } : {}),
  };
  const btn = $("set-ep-save");
  btn.disabled = true;
  setStatus("Saving…");
  try {
    render(await api("PUT", "/endpoint", body));
    setStatus("Saved.", "ok");
    toast("LLM endpoint updated");
  } catch (e) {
    setStatus(`Save failed: ${e.message}`, "err");
  } finally {
    btn.disabled = false;
  }
}

async function test() {
  const btn = $("set-ep-test");
  btn.disabled = true;
  setStatus("Testing…");
  try {
    const r = await api("POST", "/endpoint/test");
    if (r.ok) {
      setStatus(
        `OK — ${r.latency_ms} ms` +
          (r.deployment ? ` · ${r.deployment}` : ""),
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

function init() {
  const save_ = $("set-ep-save");
  const test_ = $("set-ep-test");
  if (!save_ || !test_) return;
  save_.addEventListener("click", save);
  test_.addEventListener("click", test);
  load();
}

if (document.readyState === "loading") {
  document.addEventListener("DOMContentLoaded", init);
} else {
  init();
}
