# Ingestion Module Restructure Plan — 2026-05-22

Target: `apps/fastapi/domains/dd/ingestion/`

```
ingestion/
  __init__.py
  dispatch.py                  # orchestrator — stays flat (351 lines, all functions)
  cdp.py                       # stays flat (54 lines)
  seeder.py                    # stays flat (63 lines)
  extract.py                   # stays flat (110 lines)
  snapshot.py                  # stays flat (119 lines)

  storage/                     # merge storage_minio.py + store.py (tightly coupled)
    __init__.py
    constants.py               # key-building helpers (framework_prefix, page_key, vault_*_key, etc.)
    types.py                   # ManifestEntry dataclass, ContentType alias
    service.py                 # MinIOStorage class + get_storage + Store class + read_* helpers

  progress/                    # progress.py -> split
    __init__.py
    constants.py               # _TTL_S, _LOCK_TTL_S, _THROTTLE_S, _RELEASE_SCRIPT
    types.py                   # IngestCancelled exception, Progress class
    service.py                 # Redis functions (acquire_lock, release_lock, read_progress, etc.)

  filters/                     # filters.py -> split
    __init__.py
    constants.py               # POLYGLOT_FRAMEWORKS, LANGUAGE_PATH_MAP, regexes, deny patterns
    service.py                 # is_polyglot, build_language_filter, should_keep, etc.

  post/                        # post.py -> split
    __init__.py
    constants.py               # MONOLITH_SPLIT_THRESHOLD_BYTES, _SOURCE_LINE_RE, etc.
    service.py                 # split_monolith, dedup_pages, apply_to_store

  tiers/                       # group tier1-5 + tier4_playwright
    __init__.py                # re-exports the 5 run() functions
    types.py                   # ManifestDetected, EmptyLinksDetected exceptions
    tier1.py                   # flat — self-contained (165 lines, llms-full.txt)
    tier2.py                   # flat — self-contained (267 lines, llms.txt index)
    tier3.py                   # flat — self-contained (248 lines, sitemap.xml)
    tier4.py                   # flat — self-contained (467 lines, HTTP BFS + SPA)
    tier5.py                   # flat — self-contained (326 lines, GitHub API)
    tier4_playwright.py        # Crawl4AI helper (used by tier4, renamed from playwright_crawl.py)
```

## Rationale

- **storage/** merges storage_minio.py + store.py — store sits on top of MinIOStorage, same domain. Key-building helpers are constants/templates. ManifestEntry is a dataclass.
- **Tier files stay flat inside tiers/** — self-contained strategy modules (constants + functions + one run() entry). Exception classes extract to types.py.
- **Small utilities stay flat** — cdp/seeder/extract/snapshot are <120 lines, single-purpose.
- **dispatch.py stays flat** — orchestrator, all functions, no types/constants worth extracting.

## Convention (project-wide)

- `constants.py`: module-level variables (ints, floats, strings, dicts, lists, tuples, compiled regexes, sets)
- `service.py`: all functions
- `types.py`: dataclass, BaseModel, TypedDict, Enum, exception classes
