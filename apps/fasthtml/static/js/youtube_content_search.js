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
})();
