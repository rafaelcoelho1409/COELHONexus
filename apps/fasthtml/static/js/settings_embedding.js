// Embedding Endpoint card — the OpenAI-compatible URL YCS calls for
// embeddings. Independent connection from the LLM Endpoint card (own
// URL/key/model) — mirrors settings_endpoint.js exactly. The API key is
// write-only: it leaves the browser on Save and never comes back (GET
// returns masked status).

const API = "/api/v1/llm/settings";

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
  const save_ = $("set-emb-save");
  const test_ = $("set-emb-test");
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
