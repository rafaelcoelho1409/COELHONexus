# Knowledge Distiller — Go Module Registry Support (Resolver Stage A)

**Status**: Planned follow-up. Not blocking any current test case.
**Date**: 2026-04-22
**Trigger**: Discovered during `"LangChain (Python) + LangChain (Go)"` polyglot disambiguation test.

## The gap in one sentence

`services/knowledge/registry.py::hint_lookup` probes PyPI, npm, and crates.io only — there is no Go module registry probe, so Go-specific frameworks resolve via generic web search and frequently land on `pkg.go.dev` (Tier 4 Playwright) instead of the repo's README (Tier-GH, cleaner + faster).

## Test evidence (2026-04-22)

Input: `"LangChain (Python) + LangChain (Go)"`

Resolver output:

| Variant | docs_url | tier | confidence | registry_exists |
|---|---|---|---|---|
| `LangChain` (Python) | `docs.langchain.com/` | 1 | 0.95 | `true` (pypi) |
| `LangChain Go` | `pkg.go.dev/github.com/tmc/langchaingo` | 4 | 0.75 | `false` |

LLM decomposer successfully produced distinct canonical names (`LangChain` vs `LangChain Go`), so disambiguation works end-to-end — this is a quality-tier issue, not a correctness bug. The Go variant would benefit from Tier-GH ingestion (raw README fetch via GitHub API) instead of Tier 4 Playwright against a pkg.go.dev registry page that renders godoc + README behind JS.

## Why pkg.go.dev is suboptimal here

- **Tier 4 means Playwright** — pkg.go.dev is built on Next.js/Fern-class rendering. Code blocks + tab widgets may strip in the same way DeepAgents did on docs.langchain.com pre-resolver-fix.
- **Registry page, not docs hub** — `pkg.go.dev/github.com/tmc/langchaingo` is structurally equivalent to `pypi.org/project/langchain/` (a listing), not `docs.langchain.com/` (the docs site). The `_BAD_HOSTS` filter in `services/search_chain.py` already rejects PyPI/npm/crates.io/rubygems as non-docs, but `pkg.go.dev` is absent from that set.
- **Tier-GH exists for exactly this case** — `services/knowledge/github_ingest.py::_ingest_github_tree()` fetches `github.com/{org}/{repo}/git/trees/{default_branch}?recursive=1`, filters `*.md`, and bulk-downloads raw markdown. For a README-only repo like `tmc/langchaingo`, this is the correct ingestion strategy; it completes in ~5 seconds.

## Minimal change

Add a Go module probe to `services/knowledge/registry.py::hint_lookup`. Two candidate data sources:

1. **`deps.dev` API** — `https://api.deps.dev/v3/systems/go/packages/{module-path}` returns `{versions, links.repo}`. Free, no auth, covers the whole Go module ecosystem. Recommended primary.
2. **`proxy.golang.org`** — `https://proxy.golang.org/{module-path}/@latest` returns `{Version, Time}` but not the repo URL. Would need a follow-up call to `@v/{version}.info` and parsing — more fragile. Recommended only as a fallback.

### Implementation sketch

```python
# services/knowledge/registry.py

async def _go_module_hint(framework: str) -> Optional[RegistryHint]:
    # Go module paths are of the form host/org/repo — e.g. github.com/tmc/langchaingo.
    # The decomposer's canonical_name won't typically carry the full path, so we
    # normalize: "langchaingo" → search deps.dev for the package name, or accept
    # a full path verbatim.
    norm = framework.lower().strip().replace(" ", "")
    url = f"https://api.deps.dev/v3/systems/go/packages/{norm}"
    async with httpx.AsyncClient(timeout=_TIMEOUT_SECONDS,
                                 headers={"User-Agent": _USER_AGENT}) as c:
        r = await c.get(url)
    if r.status_code == 404:
        return None
    r.raise_for_status()
    data = r.json() or {}
    pkg = data.get("package") or {}
    versions = data.get("versions") or []
    latest = versions[0].get("versionKey", {}).get("version") if versions else None
    # deps.dev's `links` array carries {"label": "REPO", "url": "..."} entries
    links = {l.get("label"): l.get("url") for l in pkg.get("links") or []}
    return RegistryHint(
        exists=True,
        homepage=None,           # Go modules rarely declare a separate docs homepage
        repo=links.get("REPO") or links.get("SOURCE_REPO"),
        latest_version=latest,
        all_versions=[v.get("versionKey", {}).get("version") for v in versions[:_MAX_VERSIONS]],
        source="go",
    )
```

Wire it into the probe order in `hint_lookup`:

```python
if lang in ("go", "golang"):
    order = [_go_module_hint, _pypi_hint, _npm_hint, _crates_hint]
else:
    order = [_pypi_hint, _npm_hint, _crates_hint, _go_module_hint]  # go last for other langs
```

This way, any topic flagged Go (via parenthetical hint or mastered_technologies context) probes `deps.dev` first; all other topics fall through to Go only if the other three miss (cheap tail, no behavior regression for Python/JS/Rust).

## Downstream effect

When `deps.dev` returns `repo=github.com/tmc/langchaingo`, the existing resolver flow already handles the rest:

1. LLM rerank receives the registry hint with `repo_url` populated — prompt rule #3 (registry-hint-authoritative) biases toward the repo.
2. `docs_probe.py::_upgrade_git_host_url` detects the GitHub URL and tries to upgrade (`homepage` > `has_pages` > `readme_only`).
3. For `tmc/langchaingo` specifically (no dedicated docs site, no GH Pages), `github_discover` ends up `readme_only` → the ingester dispatches to `github_ingest._ingest_github_tree()` (Tier-GH).
4. MinIO receives `README.md` + any other `*.md` in the repo. Clean, fast, no Playwright.

No ingester changes needed. This is purely a Stage A (registry) improvement.

## Not addressed by this change

- **Multi-module Go repos** (e.g., `github.com/foo/bar/module-a`). `deps.dev` resolves the specific module path correctly, but our canonical_name normalization may lose the sub-path. Handle if it comes up.
- **Go modules without a github.com repo** (rare — gitlab, codeberg, self-hosted). `deps.dev` returns the repo URL verbatim; the resolver's GitHub-upgrade logic only handles github.com. Would need to generalize or fall through to the LLM rerank for those.
- **Structured `language` field on `DecompositionTopic`** — the LLM currently handles parenthetical language qualifiers well enough via canonical_name suffixing (`"LangChain Go"`). A proper `language` field would be more robust but is not required for this fix to work.

## Effort estimate

~40 LoC in `services/knowledge/registry.py` + ~5 LoC in `hint_lookup`'s order logic. No schema changes, no ingester changes, no prompt changes. One PR.

## How to resume

Open `services/knowledge/registry.py`, add `_go_module_hint` alongside `_pypi_hint` / `_npm_hint` / `_crates_hint`, wire into `hint_lookup`'s `order` list. Re-test by resolving a Go-only framework (e.g., `"gin-gonic"`, `"echo (Go)"`, `"fiber (Go)"`) — expect `registry_source="go"`, `repo=github.com/{org}/{repo}` populated in the ResolvedDocs. Then verify Tier-GH ingestion picks it up via the existing `github_discover` logic.
