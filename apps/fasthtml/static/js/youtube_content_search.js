/* YouTube Content Search — form handler + DOM update.
 *
 * Contract:
 *   POST /api/v1/youtube/runs  body={video_url, question}
 *   -> {indexed, answer, citations, model, latency_s}
 *
 * /api/* is reverse-proxied to FastAPI by FastHTML's proxy.py.
 */
(() => {
  const form         = document.getElementById("ycs-form");
  const submitBtn    = document.getElementById("ycs-submit");
  const urlInput     = document.getElementById("ycs-video-url");
  const questionEl   = document.getElementById("ycs-question");
  const statusEl     = document.getElementById("ycs-status");
  const statusText   = document.getElementById("ycs-status-text");
  const indexedEl    = document.getElementById("ycs-indexed");
  const answerEl     = document.getElementById("ycs-answer");
  const answerText   = document.getElementById("ycs-answer-text");
  const answerMeta   = document.getElementById("ycs-answer-meta");
  const citationsEl  = document.getElementById("ycs-citations");
  const citationsList= document.getElementById("ycs-citations-list");

  function setStatus(kind, text) {
    statusEl.classList.remove("ycs-status-running", "ycs-status-error");
    if (text) {
      statusEl.style.display = "block";
      if (kind) statusEl.classList.add(`ycs-status-${kind}`);
      statusText.textContent = text;
    } else {
      statusEl.style.display = "none";
      statusText.textContent = "";
    }
  }

  function hideResults() {
    indexedEl.style.display = "none";
    answerEl.style.display = "none";
    citationsEl.style.display = "none";
  }

  function escapeHtml(s) {
    return String(s)
      .replaceAll("&", "&amp;")
      .replaceAll("<", "&lt;")
      .replaceAll(">", "&gt;")
      .replaceAll('"', "&quot;")
      .replaceAll("'", "&#39;");
  }

  function renderResult(data) {
    if (data.indexed) {
      const i = data.indexed;
      const lang = i.lang || "?";
      indexedEl.style.display = "block";
      indexedEl.textContent =
        `Indexed: ${i.title} — ${i.chunks_upserted} chunks (${lang})`;
    }

    answerEl.style.display = "block";
    answerText.textContent = data.answer || "(empty answer)";
    const lat = data.latency_s != null ? `${data.latency_s.toFixed(2)}s` : "—";
    answerMeta.textContent = `via ${data.model || "—"} · ${lat}`;

    citationsList.innerHTML = "";
    const citations = data.citations || [];
    if (citations.length) {
      citationsEl.style.display = "block";
      for (const c of citations) {
        const a = document.createElement("a");
        a.className = "ycs-citation";
        a.href = `https://www.youtube.com/watch?v=${encodeURIComponent(c.video_id)}`;
        a.target = "_blank";
        a.rel = "noopener noreferrer";
        a.innerHTML = `
          <span class="ycs-citation-title">${escapeHtml(c.title || c.video_id)}</span>
          <span class="ycs-citation-meta">chunk ${c.chunk_index + 1} / ${c.total_chunks}</span>
        `;
        citationsList.appendChild(a);
      }
    } else {
      citationsEl.style.display = "none";
    }
  }

  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    const video_url = urlInput.value.trim();
    const question  = questionEl.value.trim();
    if (!video_url || !question) return;

    hideResults();
    setStatus("running", "Indexing transcript + answering…");
    submitBtn.disabled = true;
    submitBtn.textContent = "Asking…";

    try {
      const r = await fetch("/api/v1/youtube/runs", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ video_url, question }),
      });
      if (!r.ok) {
        const errBody = await r.text();
        throw new Error(`HTTP ${r.status}: ${errBody.slice(0, 200)}`);
      }
      const data = await r.json();
      setStatus(null, "");
      renderResult(data);
    } catch (err) {
      setStatus("error", err.message || String(err));
    } finally {
      submitBtn.disabled = false;
      submitBtn.textContent = "Ask";
    }
  });

  // ===== Step 1 · Source · Search mode ============================
  // Plain helpers — kept inside the IIFE so they share escapeHtml etc.
  const byId = (id) => document.getElementById(id);
  // Cart state: video_id -> video object. Persists across searches.
  const cart = new Map();

  const numVal = (id) => {
    const el = byId(id);
    if (!el || el.value === "") return null;
    const n = Number(el.value);
    return Number.isFinite(n) ? n : null;
  };
  const strVal = (id) => {
    const el = byId(id);
    if (!el) return null;
    const v = (el.value || "").trim();
    return v === "" ? null : v;
  };
  const boolVal = (id) => !!(byId(id) || {}).checked;

  function buildSearchBody() {
    const body = {
      query: (byId("ycs-search-query").value || "").trim(),
      max_results: numVal("ycs-search-max") || 10,
    };
    if (boolVal("ycs-filter-sort-date")) body.sort_by_date = true;
    const duration = strVal("ycs-filter-duration");
    if (duration) body.duration = duration;
    const dmin = numVal("ycs-filter-duration-min");
    if (dmin !== null) body.duration_min = dmin;
    const dmax = numVal("ycs-filter-duration-max");
    if (dmax !== null) body.duration_max = dmax;
    const da = strVal("ycs-filter-date-after");
    if (da) body.date_after = da;
    const db = strVal("ycs-filter-date-before");
    if (db) body.date_before = db;
    const minV = numVal("ycs-filter-min-views");
    if (minV !== null) body.min_views = minV;
    const maxV = numVal("ycs-filter-max-views");
    if (maxV !== null) body.max_views = maxV;
    const minL = numVal("ycs-filter-min-likes");
    if (minL !== null) body.min_likes = minL;
    const live = strVal("ycs-filter-live-status");
    if (live) body.live_status = live;
    const avail = strVal("ycs-filter-availability");
    if (avail) body.availability = avail;
    const age = numVal("ycs-filter-age-limit");
    if (age !== null) body.age_limit = age;
    const t = strVal("ycs-filter-title");
    if (t) body.title_contains = t;
    const desc = strVal("ycs-filter-description");
    if (desc) body.description_contains = desc;
    const ch = strVal("ycs-filter-channel");
    if (ch) body.channel_name = ch;
    return body;
  }

  function formatViews(n) {
    if (n == null) return "";
    if (n >= 1e9) return `${(n / 1e9).toFixed(1)}B views`;
    if (n >= 1e6) return `${(n / 1e6).toFixed(1)}M views`;
    if (n >= 1e3) return `${(n / 1e3).toFixed(1)}K views`;
    return `${n} views`;
  }
  function formatDate(d) {
    if (!d || d.length < 8) return "";
    return `${d.slice(0, 4)}-${d.slice(4, 6)}-${d.slice(6, 8)}`;
  }

  function updateCartCount() {
    const n = cart.size;
    const el = byId("ycs-cart-count");
    if (el) el.textContent = `${n} video${n === 1 ? "" : "s"} staged`;
    const btn = byId("ycs-cart-continue");
    if (btn) btn.disabled = n === 0;
  }

  function renderSearchResults(data) {
    const list = byId("ycs-search-results");
    list.innerHTML = "";
    const videos = data.videos || [];
    if (!videos.length) {
      list.innerHTML = '<div class="ycs-search-empty">No results.</div>';
      return;
    }
    for (const v of videos) {
      const inCart = cart.has(v.id);
      const dur = v.duration_string || "";
      const meta = [
        v.channel || "",
        dur,
        formatViews(v.view_count),
        formatDate(v.upload_date),
      ].filter(Boolean).join(" · ");
      const row = document.createElement("div");
      row.className = "ycs-result";
      row.dataset.videoId = v.id;
      row.innerHTML = `
        <a class="ycs-result-thumb" href="${escapeHtml(v.url)}" target="_blank" rel="noopener noreferrer">
          ${v.thumbnail ? `<img src="${escapeHtml(v.thumbnail)}" alt="" loading="lazy">` : ""}
          ${dur ? `<span class="ycs-result-dur">${escapeHtml(dur)}</span>` : ""}
        </a>
        <div class="ycs-result-body">
          <a class="ycs-result-title" href="${escapeHtml(v.url)}" target="_blank" rel="noopener noreferrer">
            ${escapeHtml(v.title || v.id)}
          </a>
          <div class="ycs-result-meta">${escapeHtml(meta)}</div>
          ${v.description ? `<div class="ycs-result-desc">${escapeHtml(v.description.slice(0, 220))}</div>` : ""}
        </div>
        <button type="button" class="ycs-result-add${inCart ? ' added' : ''}" data-video-id="${escapeHtml(v.id)}">
          ${inCart ? '✓ Staged' : '+ Stage'}
        </button>
      `;
      // Stash the full video object so cart adds carry payload without
      // re-querying the API.
      row._video = v;
      list.appendChild(row);
    }
  }

  async function fetchAndRender({
    endpoint, body, submitBtn, submitLabel, runningLabel, statusVerb, statusSummary,
  }) {
    const statusEl = byId("ycs-search-status");
    statusEl.className = "ycs-search-status running";
    statusEl.textContent = runningLabel;
    if (submitBtn) {
      submitBtn.disabled = true;
      submitBtn.dataset.idleLabel = submitBtn.dataset.idleLabel || submitBtn.textContent;
      submitBtn.textContent = runningLabel;
    }
    try {
      const r = await fetch(endpoint, {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify(body),
      });
      if (!r.ok) {
        const txt = await r.text();
        throw new Error(`HTTP ${r.status}: ${txt.slice(0, 220)}`);
      }
      const data = await r.json();
      statusEl.className = "ycs-search-status";
      const n = data.total_results;
      statusEl.textContent = statusSummary(n, data);
      renderSearchResults(data);
    } catch (err) {
      statusEl.className = "ycs-search-status error";
      statusEl.textContent = err.message || String(err);
      byId("ycs-search-results").innerHTML = "";
    } finally {
      if (submitBtn) {
        submitBtn.disabled = false;
        submitBtn.textContent = submitBtn.dataset.idleLabel || submitLabel;
      }
    }
  }

  async function runSearch() {
    const body = buildSearchBody();
    if (!body.query) return;
    await fetchAndRender({
      endpoint: "/api/v1/youtube/search",
      body,
      submitBtn: byId("ycs-search-submit"),
      submitLabel: "Search",
      runningLabel: "Searching…",
      statusVerb: "Searching",
      statusSummary: (n, d) =>
        `${n} result${n === 1 ? "" : "s"} for "${d.query}"`,
    });
  }

  async function runVideos() {
    const raw = (byId("ycs-videos-input").value || "");
    const video_inputs = raw
      .split(/[\n,]+/)
      .map((s) => s.trim())
      .filter(Boolean);
    if (!video_inputs.length) return;
    await fetchAndRender({
      endpoint: "/api/v1/youtube/videos",
      body: { video_inputs },
      submitBtn: byId("ycs-videos-submit"),
      submitLabel: "Find videos",
      runningLabel: "Fetching…",
      statusVerb: "Fetching",
      statusSummary: (n) =>
        `${n} video${n === 1 ? "" : "s"} found from ${video_inputs.length} input${video_inputs.length === 1 ? "" : "s"}`,
    });
  }

  async function runPlaylist() {
    const playlist = (byId("ycs-playlist-input").value || "").trim();
    if (!playlist) return;
    const max_results = numVal("ycs-playlist-max");
    const body = { playlist, max_results: max_results == null ? 0 : max_results };
    await fetchAndRender({
      endpoint: "/api/v1/youtube/playlist",
      body,
      submitBtn: byId("ycs-playlist-submit"),
      submitLabel: "Find videos",
      runningLabel: "Fetching playlist…",
      statusVerb: "Fetching",
      statusSummary: (n) =>
        `${n} video${n === 1 ? "" : "s"} in the playlist`,
    });
  }

  async function runChannel() {
    const channel = (byId("ycs-channel-input").value || "").trim();
    if (!channel) return;
    const max_results = numVal("ycs-channel-max");
    const body = { channel, max_results: max_results == null ? 30 : max_results };
    await fetchAndRender({
      endpoint: "/api/v1/youtube/channel",
      body,
      submitBtn: byId("ycs-channel-submit"),
      submitLabel: "Find videos",
      runningLabel: "Fetching channel…",
      statusVerb: "Fetching",
      statusSummary: (n) =>
        `${n} video${n === 1 ? "" : "s"} from the channel`,
    });
  }

  // Tab switching — single .active class on the tab strip + tab body
  const tabs = document.querySelectorAll(".ycs-tab");
  const tabBodies = document.querySelectorAll(".ycs-tab-body");
  function activateTab(key) {
    tabs.forEach((t) => t.classList.toggle("active", t.dataset.tab === key));
    tabBodies.forEach((b) =>
      b.classList.toggle("active", b.id === `ycs-tab-body-${key}`),
    );
  }
  tabs.forEach((t) => {
    t.addEventListener("click", () => activateTab(t.dataset.tab));
  });

  // Wire filters toggle
  const filtersToggle = byId("ycs-filters-toggle");
  const filtersBody = byId("ycs-filters-body");
  if (filtersToggle && filtersBody) {
    filtersToggle.addEventListener("click", () => {
      const showing = filtersBody.style.display !== "none";
      filtersBody.style.display = showing ? "none" : "grid";
      filtersToggle.classList.toggle("open", !showing);
    });
  }

  // Wire each tab's submit button + Enter-key in its input
  const searchSubmit = byId("ycs-search-submit");
  if (searchSubmit) searchSubmit.addEventListener("click", runSearch);
  const queryInput = byId("ycs-search-query");
  if (queryInput) {
    queryInput.addEventListener("keydown", (e) => {
      if (e.key === "Enter") { e.preventDefault(); runSearch(); }
    });
  }
  const videosSubmit = byId("ycs-videos-submit");
  if (videosSubmit) videosSubmit.addEventListener("click", runVideos);
  const playlistSubmit = byId("ycs-playlist-submit");
  if (playlistSubmit) playlistSubmit.addEventListener("click", runPlaylist);
  const playlistInput = byId("ycs-playlist-input");
  if (playlistInput) {
    playlistInput.addEventListener("keydown", (e) => {
      if (e.key === "Enter") { e.preventDefault(); runPlaylist(); }
    });
  }
  const channelSubmit = byId("ycs-channel-submit");
  if (channelSubmit) channelSubmit.addEventListener("click", runChannel);
  const channelInput = byId("ycs-channel-input");
  if (channelInput) {
    channelInput.addEventListener("keydown", (e) => {
      if (e.key === "Enter") { e.preventDefault(); runChannel(); }
    });
  }

  // Stage/unstage on result-card button click (delegated)
  const resultsEl = byId("ycs-search-results");
  if (resultsEl) {
    resultsEl.addEventListener("click", (e) => {
      const btn = e.target.closest(".ycs-result-add");
      if (!btn) return;
      const row = btn.closest(".ycs-result");
      const v = row && row._video;
      if (!v) return;
      if (cart.has(v.id)) {
        cart.delete(v.id);
        btn.classList.remove("added");
        btn.textContent = "+ Stage";
      } else {
        cart.set(v.id, v);
        btn.classList.add("added");
        btn.textContent = "✓ Staged";
      }
      updateCartCount();
    });
  }
})();
