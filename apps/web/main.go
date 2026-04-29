package main

import (
	"encoding/json"
	"fmt"
	"io"
	"log"
	"net/http"
	"net/http/httputil"
	"net/url"
	"os"
	"strings"
	"time"
)

// FastAPI client
var fastAPIURL = getEnv("FASTAPI_URL", "http://coelhonexus-fastapi:8000")
var httpClient = &http.Client{Timeout: 10 * time.Second}

func getEnv(key, fallback string) string {
	if value := os.Getenv(key); value != "" {
		return value
	}
	return fallback
}

func main() {
	mux := http.NewServeMux()

	// Static files
	mux.Handle("/static/", http.StripPrefix("/static/", http.FileServer(http.Dir("static"))))

	// Health check
	mux.HandleFunc("/health", func(w http.ResponseWriter, r *http.Request) {
		w.WriteHeader(http.StatusOK)
		w.Write([]byte("OK"))
	})

	// Home page - calls FastAPI /health
	mux.HandleFunc("/", homeHandler)

	// KD markdown inspector
	mux.HandleFunc("/kd/inspect", kdInspectHandler)
	mux.HandleFunc("/api/kd/inspect/", kdInspectProxyHandler)

	// Test FastAPI connection
	mux.HandleFunc("/api/test", testFastAPIHandler)

	port := getEnv("PORT", "3000")
	log.Printf("Web server starting on :%s", port)
	log.Printf("FastAPI URL: %s", fastAPIURL)

	if err := http.ListenAndServe(":"+port, mux); err != nil {
		log.Fatal(err)
	}
}

// homePage — Memos-inspired landing, rendered inline for the time being.
// Tailwind + DaisyUI via CDN (memos theme defined inline via daisyUI plugin
// config); zero build step required. When the Templ scaffold in templates/
// is fully wired in (see apps/web/FRONTEND-SCAFFOLD.md), swap this handler
// to call `templates.Home().Render(r.Context(), w)`.
const homePage = `<!DOCTYPE html>
<html lang="en" data-theme="emerald">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <meta name="theme-color" content="#16a34a">
    <title>COELHONexus · AI Engineering Hub</title>
    <link rel="preconnect" href="https://fonts.googleapis.com">
    <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
    <link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&family=JetBrains+Mono:wght@400;500&display=swap" rel="stylesheet">
    <!-- DaisyUI 4 ships built-in themes (emerald=light/green, forest=dark/green) - pre-compiled, no custom var overrides needed -->
    <link href="https://cdn.jsdelivr.net/npm/daisyui@4.12.14/dist/full.min.css" rel="stylesheet">
    <script src="https://cdn.tailwindcss.com?plugins=typography"></script>
    <script src="https://unpkg.com/htmx.org@2.0.4"></script>
    <script src="https://unpkg.com/lucide@latest" defer></script>
    <script>
      tailwind.config = {
        theme: { extend: { fontFamily: {
          sans: ['Inter', 'ui-sans-serif', 'system-ui', 'sans-serif'],
          mono: ['JetBrains Mono', 'ui-monospace', 'monospace'],
        } } },
      };
    </script>
    <style>
      body { font-family: Inter, ui-sans-serif, system-ui, sans-serif;
        -webkit-font-smoothing: antialiased; line-height: 1.55; }
      code, pre { font-family: 'JetBrains Mono', ui-monospace, monospace; }
      .memo-card { @apply bg-base-100 border border-base-300 rounded-lg p-4
        hover:shadow-md hover:border-primary/30 transition-all; }
      .nav-item { @apply flex items-center gap-3 px-3 py-2 text-sm font-medium
        rounded-md text-base-content/70 hover:text-base-content hover:bg-base-200
        transition-colors no-underline; }
      .nav-item-active { @apply bg-base-200 text-primary; }
    </style>
</head>
<body class="min-h-screen bg-base-200 text-base-content">
  <div class="flex min-h-screen">
    <!-- Sidebar -->
    <aside class="fixed left-0 top-0 h-screen w-64 bg-base-100 border-r border-base-300 flex flex-col">
      <div class="px-4 py-5 border-b border-base-300">
        <div class="flex items-center gap-2">
          <span class="w-8 h-8 rounded-md bg-primary/10 text-primary flex items-center justify-center">
            <i data-lucide="layers" class="w-5 h-5"></i>
          </span>
          <div>
            <div class="font-semibold text-sm">COELHONexus</div>
            <div class="text-[0.65rem] text-base-content/60 uppercase tracking-wider">AI Engineering Hub</div>
          </div>
        </div>
      </div>
      <nav class="flex-1 px-3 py-4 flex flex-col gap-1 overflow-y-auto">
        <a href="#kd" class="nav-item">
          <i data-lucide="book-open-text" class="w-4 h-4"></i><span>Knowledge Distiller</span>
        </a>
        <a href="#ask" class="nav-item">
          <i data-lucide="message-square-more" class="w-4 h-4"></i><span>YouTube Ask</span>
        </a>
      </nav>
      <div class="px-3 py-3 border-t border-base-300 flex items-center justify-between">
        <button onclick="toggleTheme()" class="btn btn-sm btn-ghost" title="Toggle theme">
          <i data-lucide="moon" class="w-4 h-4"></i>
        </button>
        <span class="text-[0.7rem] text-base-content/50">v0.1 · GOTTH</span>
      </div>
    </aside>

    <!-- Main -->
    <main class="flex-1 ml-64">
      <div class="max-w-5xl mx-auto px-8 py-10">
        <header class="mb-8">
          <h1 class="text-2xl font-bold tracking-tight">Welcome back</h1>
          <p class="text-sm text-base-content/60 mt-1">
            Knowledge Distiller, YouTube RAG, and catalog health — all in one place.
          </p>
        </header>

        <!-- Two primary features -->
        <div class="grid grid-cols-1 md:grid-cols-2 gap-4 mb-10">
          <a href="#kd" class="memo-card flex items-start gap-4 group no-underline">
            <span class="w-12 h-12 rounded-lg bg-primary/10 text-primary flex items-center justify-center shrink-0 group-hover:bg-primary group-hover:text-primary-content transition-colors">
              <i data-lucide="book-open-text" class="w-6 h-6"></i>
            </span>
            <div class="min-w-0">
              <div class="text-base font-semibold">Knowledge Distiller</div>
              <div class="text-xs text-base-content/60 mt-1">Turn framework docs into chapter-structured study guides with synthesized code, flashcards, and challenges.</div>
            </div>
          </a>
          <a href="#ask" class="memo-card flex items-start gap-4 group no-underline">
            <span class="w-12 h-12 rounded-lg bg-primary/10 text-primary flex items-center justify-center shrink-0 group-hover:bg-primary group-hover:text-primary-content transition-colors">
              <i data-lucide="message-square-more" class="w-6 h-6"></i>
            </span>
            <div class="min-w-0">
              <div class="text-base font-semibold">YouTube Ask</div>
              <div class="text-xs text-base-content/60 mt-1">Agentic RAG over ingested YouTube content — ask in plain language, get grounded answers with citations.</div>
            </div>
          </a>
        </div>

        <!-- Backend status strip -->
        <div class="memo-card">
          <div class="flex items-center justify-between gap-4">
            <div>
              <div class="text-[0.7rem] uppercase tracking-wider text-base-content/60">FastAPI backend</div>
              <div id="backend-result" class="text-xs mt-1 text-base-content/60">Not yet checked.</div>
            </div>
            <button class="btn btn-sm btn-primary gap-2"
              hx-get="/api/test"
              hx-target="#backend-result"
              hx-swap="innerHTML">
              <i data-lucide="activity" class="w-4 h-4"></i>
              Test /health
            </button>
          </div>
        </div>
      </div>
    </main>
  </div>

  <script>
    if ("serviceWorker" in navigator) {
      window.addEventListener("load", () => navigator.serviceWorker.register("/static/sw.js").catch(()=>{}));
    }
    // DaisyUI built-in themes: emerald (light, green primary) <-> forest (dark, green primary).
    // Auto-detect system preference on first load, then remember user's choice.
    const themeKey = "coelhonexus:theme";
    const LIGHT = "emerald", DARK = "forest";
    const saved = localStorage.getItem(themeKey);
    const prefersDark = window.matchMedia && window.matchMedia("(prefers-color-scheme: dark)").matches;
    const initial = saved || (prefersDark ? DARK : LIGHT);
    document.documentElement.setAttribute("data-theme", initial);
    window.toggleTheme = () => {
      const cur = document.documentElement.getAttribute("data-theme");
      const next = (cur === DARK) ? LIGHT : DARK;
      document.documentElement.setAttribute("data-theme", next);
      localStorage.setItem(themeKey, next);
    };
    document.addEventListener("DOMContentLoaded", () => { if (window.lucide) window.lucide.createIcons(); });
    document.body.addEventListener("htmx:afterSwap", () => { if (window.lucide) window.lucide.createIcons(); });
  </script>
</body>
</html>`

func homeHandler(w http.ResponseWriter, r *http.Request) {
	w.Header().Set("Content-Type", "text/html; charset=utf-8")
	w.Write([]byte(homePage))
}

// kdInspectPage — full-screen 3-pane markdown inspector.
// Frame is identical to homePage (sidebar + DaisyUI emerald) but the main
// pane swaps in framework / file lists / rendered previews via HTMX from
// the FastAPI inspect router (proxied through /api/kd/inspect/*).
const kdInspectPage = `<!DOCTYPE html>
<html lang="en" data-theme="emerald">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <meta name="theme-color" content="#16a34a">
  <title>Inspect Markdown · COELHONexus</title>
  <link rel="preconnect" href="https://fonts.googleapis.com">
  <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
  <link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&family=JetBrains+Mono:wght@400;500&display=swap" rel="stylesheet">
  <link href="https://cdn.jsdelivr.net/npm/daisyui@4.12.14/dist/full.min.css" rel="stylesheet">
  <script src="https://cdn.tailwindcss.com?plugins=typography"></script>
  <script src="https://unpkg.com/htmx.org@2.0.4"></script>
  <script src="https://unpkg.com/lucide@latest" defer></script>
  <script>
    tailwind.config = { theme: { extend: { fontFamily: {
      sans: ['Inter', 'ui-sans-serif', 'system-ui', 'sans-serif'],
      mono: ['JetBrains Mono', 'ui-monospace', 'monospace'],
    } } } };
  </script>
  <style>
    body { font-family: Inter, ui-sans-serif, system-ui, sans-serif;
      -webkit-font-smoothing: antialiased; line-height: 1.55; }
    code, pre { font-family: 'JetBrains Mono', ui-monospace, monospace; }
    .nav-item { @apply flex items-center gap-3 px-3 py-2 text-sm font-medium
      rounded-md text-base-content/70 hover:text-base-content hover:bg-base-200
      transition-colors no-underline; }
    .nav-item-active { @apply bg-base-200 text-primary; }
    /* Codehilite outputs <pre> with inline styles; ensure prose doesn't
       fight them by giving them their own wrapper appearance. */
    .prose pre { background: #0d1117; color: #e6edf3; border-radius: 0.5rem;
      padding: 1rem; overflow-x: auto; font-size: 0.85rem; line-height: 1.55; }
    .prose pre code { background: transparent; padding: 0; color: inherit; }
    .prose code { background: rgba(0,0,0,0.06); padding: 0.1rem 0.35rem;
      border-radius: 0.25rem; font-size: 0.9em; }
    [data-theme="forest"] .prose code { background: rgba(255,255,255,0.08); }
  </style>
</head>
<body class="min-h-screen bg-base-200 text-base-content">
  <div class="flex min-h-screen">
    <!-- Sidebar -->
    <aside class="fixed left-0 top-0 h-screen w-64 bg-base-100 border-r border-base-300 flex flex-col z-20">
      <div class="px-4 py-5 border-b border-base-300">
        <a href="/" class="flex items-center gap-2 no-underline">
          <span class="w-8 h-8 rounded-md bg-primary/10 text-primary flex items-center justify-center">
            <i data-lucide="layers" class="w-5 h-5"></i>
          </span>
          <div>
            <div class="font-semibold text-sm">COELHONexus</div>
            <div class="text-[0.65rem] text-base-content/60 uppercase tracking-wider">AI Engineering Hub</div>
          </div>
        </a>
      </div>
      <nav class="flex-1 px-3 py-4 flex flex-col gap-1 overflow-y-auto">
        <div class="text-[0.7rem] uppercase tracking-wider text-base-content/50 px-3 py-2">Knowledge</div>
        <a href="/" class="nav-item"><i data-lucide="home" class="w-4 h-4"></i><span>Home</span></a>
        <a href="/kd/inspect" class="nav-item nav-item-active"><i data-lucide="file-search" class="w-4 h-4"></i><span>Inspect Markdown</span></a>
      </nav>
      <div class="px-3 py-3 border-t border-base-300 flex items-center justify-between">
        <button onclick="toggleTheme()" class="btn btn-sm btn-ghost" title="Toggle theme">
          <i data-lucide="moon" class="w-4 h-4"></i>
        </button>
        <span class="text-[0.7rem] text-base-content/50">v0.1 · GOTTH</span>
      </div>
    </aside>

    <!-- Main: 3-pane inspector -->
    <main class="flex-1 ml-64 flex h-screen">
      <!-- Frameworks rail -->
      <aside class="w-56 shrink-0 border-r border-base-300 overflow-y-auto bg-base-100">
        <div class="px-4 py-3 border-b border-base-300 sticky top-0 bg-base-100 z-10">
          <h2 class="text-[0.7rem] font-semibold uppercase tracking-wider text-base-content/60">Frameworks</h2>
          <p class="text-[0.65rem] text-base-content/50 mt-0.5">Ingested into MinIO</p>
        </div>
        <nav id="kd-framework-list"
             hx-get="/api/kd/inspect/frameworks"
             hx-trigger="load"
             hx-swap="innerHTML"
             class="p-2 flex flex-col gap-0.5">
          <div class="text-xs text-base-content/50 px-3 py-2">Loading…</div>
        </nav>
      </aside>
      <!-- File list rail -->
      <aside class="w-80 shrink-0 border-r border-base-300 overflow-y-auto bg-base-100">
        <div class="px-4 py-3 border-b border-base-300 sticky top-0 bg-base-100 z-10 flex items-center gap-2">
          <h2 class="text-[0.7rem] font-semibold uppercase tracking-wider text-base-content/60 truncate flex-1"
              id="kd-file-pane-title">Files</h2>
          <input type="search" placeholder="filter…"
                 class="input input-xs input-bordered w-28 text-xs"
                 oninput="kdFilterFiles(this.value)" />
        </div>
        <div id="kd-file-list" class="p-2 flex flex-col gap-0.5">
          <div class="text-xs text-base-content/50 px-3 py-4">Pick a framework on the left.</div>
        </div>
      </aside>
      <!-- Preview pane -->
      <section class="flex-1 overflow-y-auto bg-base-200 min-w-0">
        <div class="max-w-4xl mx-auto px-8 py-8" id="kd-preview">
          <div class="text-base-content/50 text-center py-24">
            <i data-lucide="file-search" class="w-12 h-12 mx-auto mb-3 opacity-40"></i>
            <div class="text-sm">Select a file to preview its rendered markdown.</div>
            <div class="text-xs mt-1 opacity-70">Quality stats appear above the rendered output.</div>
          </div>
        </div>
      </section>
    </main>
  </div>

  <script>
    // Theme toggle (parity with homePage).
    const themeKey = "coelhonexus:theme";
    const LIGHT = "emerald", DARK = "forest";
    const saved = localStorage.getItem(themeKey);
    const prefersDark = window.matchMedia && window.matchMedia("(prefers-color-scheme: dark)").matches;
    document.documentElement.setAttribute("data-theme", saved || (prefersDark ? DARK : LIGHT));
    window.toggleTheme = () => {
      const cur = document.documentElement.getAttribute("data-theme");
      const next = cur === DARK ? LIGHT : DARK;
      document.documentElement.setAttribute("data-theme", next);
      localStorage.setItem(themeKey, next);
    };
    // Lucide re-init on every htmx swap so newly-rendered icons appear.
    document.addEventListener("DOMContentLoaded", () => { if (window.lucide) window.lucide.createIcons(); });
    document.body.addEventListener("htmx:afterSwap", () => { if (window.lucide) window.lucide.createIcons(); });
    // Case-insensitive substring filter for the file list.
    window.kdFilterFiles = (q) => {
      const re = q ? new RegExp(q.replace(/[.*+?^${}()|[\]\\]/g, "\\$&"), "i") : null;
      document.querySelectorAll("#kd-file-list [data-file-row]").forEach((row) => {
        row.style.display = !re || re.test(row.dataset.fileRow) ? "" : "none";
      });
    };
  </script>
</body>
</html>`

// kdInspectHandler — KD markdown inspector page (3-pane HTMX layout).
// HTML kept inline to match the homePage pattern; once the Templ pipeline
// is wired up (apps/web/templates/kd_inspect.templ already exists), swap
// this for `templates.KDInspect().Render(r.Context(), w)`.
func kdInspectHandler(w http.ResponseWriter, r *http.Request) {
	w.Header().Set("Content-Type", "text/html; charset=utf-8")
	w.Write([]byte(kdInspectPage))
}

// kdInspectProxyHandler — reverse-proxies /api/kd/inspect/* to the FastAPI
// inspect router at /api/v1/knowledge/inspect/*. Same-origin keeps HTMX
// fragments simple (no CORS, no auth header juggling).
func kdInspectProxyHandler(w http.ResponseWriter, r *http.Request) {
	target, err := url.Parse(fastAPIURL)
	if err != nil {
		http.Error(w, "bad upstream URL", http.StatusInternalServerError)
		return
	}
	proxy := httputil.NewSingleHostReverseProxy(target)
	originalDirector := proxy.Director
	proxy.Director = func(req *http.Request) {
		originalDirector(req)
		// /api/kd/inspect/<rest>  →  /api/v1/knowledge/inspect/<rest>
		req.URL.Path = "/api/v1/knowledge/inspect/" + strings.TrimPrefix(
			req.URL.Path, "/api/kd/inspect/",
		)
		req.Host = target.Host
	}
	proxy.ServeHTTP(w, r)
}

func testFastAPIHandler(w http.ResponseWriter, r *http.Request) {
	// Call FastAPI /health endpoint. DaisyUI alert markup so the fragment
	// styles cleanly inside the Memos-themed dashboard.
	resp, err := httpClient.Get(fastAPIURL + "/health")
	if err != nil {
		w.WriteHeader(http.StatusServiceUnavailable)
		fmt.Fprintf(w, `<div role="alert" class="alert alert-error text-xs p-3">
			<i data-lucide="x-circle" class="w-4 h-4"></i>
			<span>FastAPI error: %s</span>
		</div>`, err.Error())
		return
	}
	defer resp.Body.Close()

	body, _ := io.ReadAll(resp.Body)

	var result map[string]interface{}
	json.Unmarshal(body, &result)

	w.WriteHeader(http.StatusOK)
	fmt.Fprintf(w, `<div role="alert" class="alert alert-success text-xs p-3">
		<i data-lucide="check-circle" class="w-4 h-4"></i>
		<div><strong>Healthy</strong><br><span class="text-[0.7rem] text-base-content/70 font-mono">%s</span></div>
	</div>`, string(body))
}
