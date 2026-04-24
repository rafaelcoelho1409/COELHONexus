# COELHONexus Web — GOTTH Frontend Scaffold

**Stack:** Go + Templ + Tailwind + HTMX + PWA + DaisyUI. Memos-inspired visual theme.

This scaffold is **additive** — it does not replace the existing `main.go`. Integrate piece by piece.

## Files created

```
apps/web/
├── tailwind.config.js              # Memos theme (memos + memos-dark)
├── static/css/input.css            # Tailwind source (compiled → main.css)
├── templates/
│   ├── base.templ                  # Page shell: sidebar + main, HTMX + Lucide
│   ├── sidebar.templ               # Memos-style left rail navigation
│   ├── home.templ                  # Landing: greeting + quick actions + feed
│   ├── youtube_ask.templ           # RAG search MVP screen (SSE streaming)
│   └── helpers.go                  # Tiny Go helpers for templates
└── scripts/build.sh                # templ → tailwind → go build pipeline
```

## One-time setup (install 2 binaries, zero Node.js)

```bash
# Templ generator
go install github.com/a-h/templ/cmd/templ@latest

# Tailwind standalone binary
mkdir -p apps/web/bin
curl -Lo apps/web/bin/tailwindcss \
  https://github.com/tailwindlabs/tailwindcss/releases/latest/download/tailwindcss-linux-x64
chmod +x apps/web/bin/tailwindcss
```

## Build

```bash
cd apps/web
./scripts/build.sh
# or individually:
#   templ generate
#   ./bin/tailwindcss -i static/css/input.css -o static/css/main.css --minify --watch
#   go build -o bin/web .
```

`templ generate` produces `*_templ.go` files next to each `.templ`. These are
regular Go code — your `main.go` can import and call the templates directly:

```go
import "coelhonexus-web/templates"

// ...
func homeHandler(w http.ResponseWriter, r *http.Request) {
    templates.Home().Render(r.Context(), w)
}
```

## Go module needs these deps (add when you wire it in)

```
github.com/a-h/templ v0.3.x
```

Router-wise you can stay with `net/http` or add `chi` for cleaner route groups.
No other deps required.

## Skaffold sync

Already configured — `skaffold.yaml` syncs `**/*.go`, `**/*.templ`, `static/**`
into the web container. `templ generate` can run inside the container via a
file-watcher, OR run on the host and let Skaffold sync the generated
`*_templ.go` files (simpler).

## PWA

`static/sw.js` exists. Add a `static/manifest.json` (referenced by
`base.templ`) when you're ready. Minimal manifest:

```json
{
  "name": "COELHONexus",
  "short_name": "Nexus",
  "start_url": "/",
  "display": "standalone",
  "theme_color": "#16a34a",
  "background_color": "#ffffff",
  "icons": [
    { "src": "/static/icon-192.png", "sizes": "192x192", "type": "image/png" },
    { "src": "/static/icon-512.png", "sizes": "512x512", "type": "image/png" }
  ]
}
```

## Memos-aesthetic mapping

Visual references implemented in this scaffold:

| Memos pattern | Mapped to |
|---|---|
| Green primary (#16a34a) | DaisyUI `primary` |
| Airy zinc neutrals | DaisyUI `base-100/200/300` |
| Left sidebar with grouped nav + small caps labels | `templates/sidebar.templ` |
| Memo card (rounded-lg, border-zinc-200, hover-shadow) | `.memo-card` component class |
| Tag chips | `.tag` component class |
| Dark mode toggle | `window.toggleTheme()` in `base.templ`, `memos-dark` theme |

## Next steps (beyond scaffold)

1. **Wire `main.go`** to call `templates.Home()` for the root route
2. **Install `github.com/a-h/templ`** via `go get`
3. **Run the build script** once to generate main.css + compiled templates
4. **Ship `/youtube/ask`** as the MVP feature screen (template already written)
5. **Add SSE handler** in `main.go` that streams FastAPI's `/api/v1/youtube/agents/search/stream` through to the browser with `text/event-stream`

See `docs/COELHONEXUS-FRONTEND-STACK.md` for stack rationale.
