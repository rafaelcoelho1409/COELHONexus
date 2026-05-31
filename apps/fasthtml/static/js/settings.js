// Global Settings — BYOK provider keys + free-model selection.
// Talks to FastAPI through the /api reverse proxy. Raw keys are write-only:
// they leave the browser on save and never come back (status is masked).

const API = "/api/v1/llm/settings";

// ---- tiny DOM helper ------------------------------------------------------
function el(tag, props = {}, children = []) {
  const node = document.createElement(tag);
  for (const [k, v] of Object.entries(props)) {
    if (k === "class") node.className = v;
    else if (k === "dataset") Object.assign(node.dataset, v);
    else if (k.startsWith("on") && typeof v === "function")
      node.addEventListener(k.slice(2), v);
    else if (v === true) node.setAttribute(k, "");
    else if (v !== false && v != null) node.setAttribute(k, v);
  }
  for (const c of [].concat(children)) {
    if (c == null || c === false) continue;
    node.append(c.nodeType ? c : document.createTextNode(String(c)));
  }
  return node;
}

let _toastTimer = null;
function toast(msg, kind = "ok") {
  const t = document.getElementById("set-toast");
  if (!t) return;
  t.textContent = msg;
  t.className = `set-toast show set-toast-${kind}`;
  clearTimeout(_toastTimer);
  _toastTimer = setTimeout(() => (t.className = "set-toast"), 3200);
}

async function api(method, path, body) {
  const opts = { method, headers: {} };
  if (body !== undefined) {
    opts.headers["Content-Type"] = "application/json";
    opts.body = JSON.stringify(body);
  }
  const r = await fetch(API + path, opts);
  let data = null;
  try { data = await r.json(); } catch (_) { /* empty body */ }
  if (!r.ok) {
    const detail = data && (data.detail ?? data.message);
    const err = new Error(typeof detail === "string" ? detail : (r.statusText || "request failed"));
    err.status = r.status;
    err.detail = detail;
    throw err;
  }
  return data;
}

// ---- status pill text -----------------------------------------------------
function keyStatusPill(p) {
  if (p.has_key && p.source === "user")
    return el("span", { class: "set-pill set-pill-ok" }, `Custom key ••••${p.last4}`);
  if (p.has_key && p.source === "env")
    return el("span", { class: "set-pill set-pill-env" }, `Env key ••••${p.last4}`);
  return el("span", { class: "set-pill set-pill-none" }, "No key");
}

function probeBadge(probe) {
  const map = {
    reachable: ["ok", `Reachable · ${probe.n_free_models} free model(s)`],
    rate_limited: ["warn", "Valid (rate-limited)"],
    invalid_key: ["err", "Invalid key (401/403)"],
    missing_key: ["none", "No key set"],
    unreachable: ["err", "Unreachable"],
    unknown_provider: ["err", "Unknown provider"],
  };
  const [k, label] = map[probe.status] || ["none", probe.status || "unknown"];
  return el("span", { class: `set-pill set-pill-${k}` }, label);
}

// ---- per-provider card ----------------------------------------------------
function providerCard(p) {
  const card = el("div", { class: "prov-card", dataset: { id: p.id } });

  // header: name + badge + enable toggle
  const badge = el("span", { class: `set-kind set-kind-${p.kind}` }, p.kind === "paid" ? "Paid" : "Free");
  const name = el("div", { class: "prov-name" }, [p.name, badge]);
  if (p.required)
    name.append(el("span", {
      class: "set-kind set-kind-required",
      title: "Mandatory — powers the embedding + reranking models every Docs Distiller run needs",
    }, "Required"));
  if (!p.registry_enabled)
    name.append(el("span", { class: "set-kind set-kind-off", title: "Disabled by default (often paywalled)" }, "off by default"));

  const toggle = el("label", { class: "set-switch", title: "Use this provider in the rotator" }, [
    el("input", {
      type: "checkbox", ...(p.enabled ? { checked: true } : {}),
      onchange: (e) => onToggle(p, e.target.checked),
    }),
    el("span", { class: "set-switch-track" }),
  ]);

  const status = el("div", { class: "prov-status" }, [keyStatusPill(p), toggle]);
  card.append(el("div", { class: "prov-head" }, [name, status]));

  // key row
  const input = el("input", {
    type: "password", class: "prov-key-input", autocomplete: "off",
    spellcheck: "false", placeholder: `Paste ${p.key_env}`,
  });
  const saveBtn = el("button", { type: "button", class: "set-btn set-btn-primary", onclick: () => onSaveKey(p, input) }, "Save");
  const testBtn = el("button", { type: "button", class: "set-btn set-btn-ghost", onclick: () => onTest(p, card) }, "Test");
  const delBtn = el("button", {
    type: "button", class: "set-btn set-btn-danger",
    ...(p.source === "user" ? {} : { disabled: true }),
    title: p.source === "user" ? "Remove the stored key (revert to env)" : "No stored key to remove",
    onclick: () => onRemoveKey(p),
  }, "Remove");
  input.addEventListener("keydown", (e) => { if (e.key === "Enter") onSaveKey(p, input); });
  card.append(el("div", { class: "prov-key-row" }, [input, saveBtn, testBtn, delBtn]));

  const result = el("div", { class: "prov-result", dataset: { role: "result" } });
  card.append(result);

  // models expander
  const modelsWrap = el("div", { class: "prov-models", dataset: { role: "models" } });
  const expander = el("button", {
    type: "button", class: "prov-models-toggle",
    onclick: () => onToggleModels(p, modelsWrap, expander),
  }, [el("span", { class: "prov-models-chevron" }, "▸"), "Models"]);
  card.append(expander, modelsWrap);

  return card;
}

// ---- model panel ----------------------------------------------------------
function modelPanel(p, data) {
  const wrap = el("div", { class: "prov-models-inner" });
  const isCustom = data.mode === "custom";
  const selected = new Set(data.selected || []);

  const allRadio = el("label", { class: "set-radio" }, [
    el("input", { type: "radio", name: `mode-${p.id}`, value: "all", ...(isCustom ? {} : { checked: true }) }),
    el("span", {}, "All free models"),
    el("span", { class: "set-radio-hint" }, "auto-includes newly released models"),
  ]);
  const customRadio = el("label", { class: "set-radio" }, [
    el("input", { type: "radio", name: `mode-${p.id}`, value: "custom", ...(isCustom ? { checked: true } : {}) }),
    el("span", {}, "Choose specific models"),
  ]);

  const list = el("div", { class: "prov-model-list" });
  const checks = [];
  if (!data.available || !data.available.length) {
    list.append(el("div", { class: "prov-model-empty" },
      data.has_key ? "No free models discovered for this provider." :
        "Add a valid key first, then reopen to list free models."));
  } else {
    for (const mid of data.available) {
      const cb = el("input", { type: "checkbox", value: mid, ...(selected.has(mid) ? { checked: true } : {}) });
      checks.push(cb);
      list.append(el("label", { class: "prov-model-item" }, [cb, el("span", {}, mid)]));
    }
  }

  const selAll = el("button", { type: "button", class: "set-link", onclick: () => { checks.forEach(c => c.checked = true); } }, "Select all models");
  const selNone = el("button", { type: "button", class: "set-link", onclick: () => { checks.forEach(c => c.checked = false); } }, "Deselect all");
  const bulk = el("div", { class: "prov-model-bulk" }, [selAll, selNone]);

  const sync = () => {
    const custom = customRadio.querySelector("input").checked;
    list.style.display = custom ? "" : "none";
    bulk.style.display = custom && checks.length ? "" : "none";
  };
  allRadio.querySelector("input").addEventListener("change", sync);
  customRadio.querySelector("input").addEventListener("change", sync);

  const saveBtn = el("button", {
    type: "button", class: "set-btn set-btn-primary",
    onclick: () => onSaveModels(p, wrap),
  }, "Save selection");

  wrap.append(
    el("div", { class: "prov-model-modes" }, [allRadio, customRadio]),
    bulk, list,
    el("div", { class: "prov-model-foot" }, [saveBtn]),
  );
  sync();
  return wrap;
}

// ---- handlers -------------------------------------------------------------
async function onSaveKey(p, input) {
  const key = (input.value || "").trim();
  if (!key) { toast("Paste a key first", "warn"); return; }
  try {
    const res = await api("POST", `/providers/${p.id}/key`, { api_key: key });
    input.value = "";
    toast(`${p.name}: key saved (${res.probe.status})`, "ok");
    await reload();
  } catch (e) {
    const probe = e.detail && e.detail.probe;
    toast(`${p.name}: ${probe ? probe.status : e.message}`, "err");
  }
}

async function onRemoveKey(p) {
  try {
    await api("DELETE", `/providers/${p.id}/key`);
    toast(`${p.name}: key removed (reverted to env)`, "ok");
    await reload();
  } catch (e) { toast(`${p.name}: ${e.message}`, "err"); }
}

async function onTest(p, card) {
  const result = card.querySelector('[data-role="result"]');
  result.replaceChildren(el("span", { class: "prov-testing" }, "Testing…"));
  try {
    const probe = await api("POST", `/providers/${p.id}/test`);
    result.replaceChildren(probeBadge(probe), probe.error ? el("span", { class: "prov-err-msg" }, probe.error) : null);
  } catch (e) {
    result.replaceChildren(el("span", { class: "set-pill set-pill-err" }, e.message));
  }
}

async function onToggle(p, enabled) {
  try {
    await api("PATCH", `/providers/${p.id}`, { enabled });
    toast(`${p.name}: ${enabled ? "enabled" : "disabled"}`, "ok");
  } catch (e) { toast(`${p.name}: ${e.message}`, "err"); await reload(); }
}

async function onToggleModels(p, wrap, expander) {
  const open = wrap.classList.toggle("open");
  expander.classList.toggle("open", open);
  if (!open) { wrap.replaceChildren(); return; }
  wrap.replaceChildren(el("div", { class: "prov-model-loading" }, "Discovering free models…"));
  try {
    const data = await api("GET", `/providers/${p.id}/models`);
    wrap.replaceChildren(modelPanel(p, data));
  } catch (e) {
    wrap.replaceChildren(el("div", { class: "prov-model-empty" }, `Failed to load models: ${e.message}`));
  }
}

async function onSaveModels(p, wrap) {
  const mode = wrap.querySelector(`input[name="mode-${p.id}"]:checked`)?.value || "all";
  const selected = mode === "custom"
    ? [...wrap.querySelectorAll(".prov-model-list input:checked")].map((c) => c.value)
    : [];
  if (mode === "custom" && !selected.length) {
    toast(`${p.name}: pick ≥1 model or choose "All free"`, "warn");
    return;
  }
  try {
    await api("POST", `/providers/${p.id}/models`, { mode, selected });
    toast(`${p.name}: ${mode === "all" ? "using all free models" : `${selected.length} model(s) selected`}`, "ok");
  } catch (e) { toast(`${p.name}: ${e.message}`, "err"); }
}

async function onHealthAll() {
  const btn = document.getElementById("set-test-all");
  if (btn) { btn.disabled = true; btn.textContent = "Testing…"; }
  try {
    const data = await api("GET", "/providers/health");
    const results = data.results || [];
    if (!results.length) { toast("No keyed providers to test", "warn"); return; }
    const byId = new Map(results.map((r) => [r.id, r]));
    for (const card of document.querySelectorAll(".prov-card")) {
      const r = byId.get(card.dataset.id);
      if (!r) continue;
      const result = card.querySelector('[data-role="result"]');
      result.replaceChildren(probeBadge(r), r.error ? el("span", { class: "prov-err-msg" }, r.error) : null);
    }
    const okN = results.filter((r) => r.ok).length;
    toast(`${okN}/${results.length} provider(s) reachable`, okN === results.length ? "ok" : "warn");
  } catch (e) {
    toast(`Test all failed: ${e.message}`, "err");
  } finally {
    if (btn) { btn.disabled = false; btn.textContent = "Test all"; }
  }
}

async function onEnableAll() {
  const cards = [...document.querySelectorAll(".prov-card")];
  const keyed = providersCache.filter((p) => p.has_key);
  if (!keyed.length) { toast("Add at least one key first", "warn"); return; }
  await Promise.allSettled(keyed.map((p) => api("PATCH", `/providers/${p.id}`, { enabled: true })));
  toast(`Enabled ${keyed.length} keyed provider(s)`, "ok");
  await reload();
}

// ---- load / render --------------------------------------------------------
let providersCache = [];

function renderGlobalNote() {
  const note = document.getElementById("set-global-note");
  if (!note) return;
  const keyed = providersCache.filter((p) => p.has_key).length;
  const enabled = providersCache.filter((p) => p.enabled && p.has_key).length;
  note.textContent = `${keyed} provider(s) keyed · ${enabled} active in rotator`;
}

// Readiness banner — required keys (NVIDIA NIM: embeddings + reranking) must be
// present or Docs Distiller can't run. Red until satisfied, then a quiet ✓.
function renderReadiness(data) {
  const box = document.getElementById("set-readiness");
  if (!box) return;
  const missing = data.missing_required || [];
  if (!missing.length) {
    box.className = "set-readiness ready";
    box.replaceChildren(el("span", {}, "✓ Ready — required NVIDIA NIM key is set."));
    return;
  }
  const names = missing.map((m) => {
    const p = providersCache.find((x) => x.id === m.id);
    return (p ? p.name : m.id) + ` (${m.key_env})`;
  }).join(", ");
  box.className = "set-readiness missing";
  box.replaceChildren(
    el("strong", {}, "Action required: "),
    el("span", {}, `add the ${names} key below. NVIDIA NIM powers the embedding `
      + `+ reranking models EVERY Docs Distiller run needs — Planner and Synth `
      + `will refuse to start until it's set.`),
  );
}

async function reload() {
  const host = document.getElementById("settings-providers");
  try {
    const data = await api("GET", "/providers");
    providersCache = data.providers || [];
    host.replaceChildren(...providersCache.map(providerCard));
    renderGlobalNote();
    renderReadiness(data);
  } catch (e) {
    host.replaceChildren(el("div", { class: "set-loading set-error" },
      `Couldn't load providers: ${e.message}`));
  }
}

function init() {
  const enableBtn = document.getElementById("set-enable-all");
  if (enableBtn) enableBtn.addEventListener("click", onEnableAll);
  const testBtn = document.getElementById("set-test-all");
  if (testBtn) testBtn.addEventListener("click", onHealthAll);
  reload();
}

if (document.readyState === "loading")
  document.addEventListener("DOMContentLoaded", init);
else init();
