/**
 * Source Tool Keys section on /settings.
 *
 * Mirrors the visual + interaction structure of static/js/settings.js
 * (LLM rotator providers) — same DOM helpers, same el() signature, same
 * `prov-card / prov-head / prov-name / prov-status / set-pill / prov-key-row /
 * prov-key-input / set-btn / prov-result` classes. The only additions are a
 * `tk-description` block (provider URL · summary · benefit · signup link)
 * between the head and the key row, since tool keys carry richer copy than
 * LLM providers.
 *
 * Pulls the catalog of optional FastMCP source-tool API keys from
 * /api/v1/rr/tool-credentials/keys and renders one card per key with
 * Save / Test / Remove handlers. Raw keys NEVER round-trip — they only go
 * browser → FastAPI on save; responses carry a `last4` mask.
 */
const API = "/api/v1/rr/tool-credentials";
const ROOT_ID = "settings-tool-keys-list";


// ---- tiny DOM helper (mirrors settings.js) --------------------------------
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
  // IMPORTANT: only send Content-Type when there IS a body. FastHTML's
  // proxy route triggers `req.json()` whenever Content-Type:application/json
  // is set, even on GET — and parses an empty body as JSON → JSONDecodeError
  // → 500. Mirrors the working pattern in static/js/settings.js.
  const opts = { method, headers: {} };
  if (body !== undefined) {
    opts.headers["Content-Type"] = "application/json";
    opts.body = JSON.stringify(body);
  }
  const r = await fetch(API + path, opts);
  let data = null;
  try { data = await r.json(); } catch (_) { /* empty body */ }
  if (!r.ok) {
    const detail = (data && (data.detail || data.reason)) || `HTTP ${r.status}`;
    const err = new Error(detail);
    err.body = data;
    err.status = r.status;
    throw err;
  }
  return data;
}


// ---- status pill (mirrors keyStatusPill in settings.js) -------------------
function keyStatusPill(entry) {
  if (entry.has_key && entry.source === "user")
    return el("span", { class: "set-pill set-pill-ok" }, `Custom key ••••${entry.last4}`);
  if (entry.has_key && entry.source === "env")
    return el("span", { class: "set-pill set-pill-env" }, `Env key ••••${entry.last4}`);
  return el("span", { class: "set-pill set-pill-none" }, "No key");
}


// ---- per-tool-key card (mirrors providerCard in settings.js) --------------
function toolKeyCard(entry) {
  const card = el("div", { class: "prov-card", dataset: { keyEnv: entry.key_env } });

  // header: name + optional badge + status pill
  const badge = el("span", {
    class: "set-kind set-kind-free",
    title: "Optional — the tool works without this key (just slower)",
  }, "Optional");
  const name = el("div", { class: "prov-name" }, [entry.display_name, badge]);
  const status = el("div", { class: "prov-status" }, [keyStatusPill(entry)]);
  card.append(el("div", { class: "prov-head" }, [name, status]));

  // description block: provider URL + summary + benefit + signup link
  card.append(el("div", { class: "tk-description" }, [
    el("div", { class: "tk-provider" }, [
      el("span", { class: "tk-provider-label" }, "Provider: "),
      el("code", { class: "tk-provider-host" }, entry.provider),
    ]),
    el("p", { class: "tk-summary" }, entry.summary),
    el("p", { class: "tk-benefit" }, el("em", {}, entry.benefit)),
    el("a", {
      href: entry.signup_url,
      target: "_blank",
      rel: "noopener noreferrer",
      class: "tk-signup",
    }, "Get a key →"),
  ]));

  // key row: input + Save + Test + Remove (same shape + classes as settings.js)
  const input = el("input", {
    type: "password",
    class: "prov-key-input",
    autocomplete: "off",
    spellcheck: "false",
    placeholder: entry.has_key
      ? `Paste new ${entry.key_env} to replace`
      : `Paste ${entry.key_env}`,
  });
  const saveBtn = el("button", {
    type: "button",
    class: "set-btn set-btn-primary",
    onclick: () => onSave(entry, input),
  }, "Save");
  const testBtn = el("button", {
    type: "button",
    class: "set-btn set-btn-ghost",
    disabled: !entry.has_key,
    title: entry.has_key ? "Probe the stored key against the source's API" : "No key to test",
    onclick: () => onTest(entry, card),
  }, "Test");
  const delBtn = el("button", {
    type: "button",
    class: "set-btn set-btn-danger",
    disabled: !(entry.has_key && entry.source === "user"),
    title: entry.source === "user" ? "Remove the stored key" : "No stored key to remove",
    onclick: () => onRemove(entry),
  }, "Remove");
  input.addEventListener("keydown", (e) => { if (e.key === "Enter") onSave(entry, input); });
  card.append(el("div", { class: "prov-key-row" }, [input, saveBtn, testBtn, delBtn]));

  // result row (for inline Test feedback — empty by default; mirrors settings.js)
  card.append(el("div", { class: "prov-result", dataset: { role: "result" } }));

  return card;
}


// ---- actions --------------------------------------------------------------
async function onSave(entry, input) {
  const key = (input.value || "").trim();
  if (!key) {
    toast("Paste a key first", "warn");
    input.focus();
    return;
  }
  try {
    const res = await api("POST", `/keys/${entry.key_env}`, { api_key: key });
    if (res && res.saved) {
      toast(`${entry.display_name}: saved (••••${res.status?.last4 || "—"})`);
      input.value = "";
      await refresh();
    } else if (res && res.probe && !res.probe.ok) {
      toast(`Probe failed: ${res.probe.reason || "unknown"}`, "warn");
    } else {
      toast("Save returned no confirmation", "warn");
    }
  } catch (e) {
    // 422 → S2 probe failed; offer force-save
    if (e.status === 422 && e.body?.probe) {
      const probe = e.body.probe;
      if (confirm(`Probe failed (${probe.reason || probe.status}). Store the key anyway?`)) {
        try {
          const res = await api("POST", `/keys/${entry.key_env}`, { api_key: key, force: true });
          toast(`${entry.display_name}: force-saved (••••${res.status?.last4 || "—"})`);
          input.value = "";
          await refresh();
        } catch (e2) {
          toast(`Save failed: ${e2.message}`, "err");
        }
      }
    } else {
      toast(`Save failed: ${e.message}`, "err");
    }
  }
}


async function onRemove(entry) {
  if (!confirm(`Remove ${entry.display_name}? The tool reverts to unauth rate limits.`))
    return;
  try {
    await api("DELETE", `/keys/${entry.key_env}`);
    toast(`${entry.display_name}: removed`);
    await refresh();
  } catch (e) {
    toast(`Remove failed: ${e.message}`, "err");
  }
}


async function onTest(entry, card) {
  const result = card.querySelector('[data-role="result"]');
  if (result) {
    result.textContent = "";
    result.append(el("span", { class: "prov-testing" }, "Probing…"));
  }
  try {
    const res = await api("POST", `/keys/${entry.key_env}/test`);
    if (result) {
      result.textContent = "";
      if (res.ok) {
        result.append(el("span", { class: "set-pill set-pill-ok" }, "✓ Reachable"));
      } else {
        result.append(el("span", { class: "set-pill set-pill-err" },
          res.reason ? `✗ ${res.reason.slice(0, 80)}` : `✗ HTTP ${res.status}`));
      }
    }
  } catch (e) {
    if (result) {
      result.textContent = "";
      result.append(el("span", { class: "prov-err-msg" }, `Test failed: ${e.message}`));
    }
  }
}


// ---- main loop ------------------------------------------------------------
async function refresh() {
  const root = document.getElementById(ROOT_ID);
  if (!root) return;
  try {
    const data = await api("GET", "/keys");
    const keys = data?.keys || [];
    root.replaceChildren();
    if (keys.length === 0) {
      root.append(el("div", { class: "set-loading" }, "(no tool keys defined)"));
      return;
    }
    for (const entry of keys) root.append(toolKeyCard(entry));
  } catch (e) {
    root.replaceChildren(
      el("div", { class: "set-loading set-error" }, `Failed to load tool keys: ${e.message}`),
    );
  }
}


refresh();
