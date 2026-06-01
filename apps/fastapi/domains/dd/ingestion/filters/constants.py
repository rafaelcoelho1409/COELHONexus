"""Constants for URL & language filters shared across Tier 3 / Tier 4.

Centralizes the catalog of polyglot frameworks, per-language path slugs,
and the URL deny-list. Carried forward from the validated v3 ingest path
so behavior on Docker / OpenTelemetry / Kubernetes / Grafana stays the
same — only the surrounding infrastructure (storage, dispatch) is new.
"""
import re


# Frameworks whose docs ship a polyglot bundle (content covers many
# programming languages). When the user requests a specific language on
# one of these, we filter to that language's paths + language-agnostic
# pages (concepts, specification). Intentionally conservative — add
# entries as new polyglot frameworks surface.
POLYGLOT_FRAMEWORKS: frozenset[str] = frozenset({
    "opentelemetry",
    "grpc",
    "protobuf",
    "protocol buffers",
    "kubernetes",
    "prometheus",
    "apache kafka",
    "kafka",
    "rabbitmq",
    "elastic",
    "elasticsearch",
    "pulsar",
    "etcd",
})


# Programming-language → URL path slug aliases.
LANGUAGE_PATH_MAP: dict[str, list[str]] = {
    "python":     ["python", "py"],
    "javascript": ["javascript", "js", "nodejs", "node"],
    "typescript": ["typescript", "ts"],
    "go":         ["go", "golang"],
    "rust":       ["rust", "rs"],
    "java":       ["java"],
    "kotlin":     ["kotlin", "kt"],
    "csharp":     ["csharp", "cs", "dotnet", "net"],
    "ruby":       ["ruby", "rb"],
    "php":        ["php"],
    "swift":      ["swift"],
    "cpp":        ["cpp", "c-plus-plus", "c++"],
    "c":          ["c-lang"],          # avoid bare "c" — matches too much
    "elixir":     ["elixir", "ex"],
    "erlang":     ["erlang"],
    "scala":      ["scala"],
    "haskell":    ["haskell", "hs"],
}


# Conservative deny-list — strips marketing, legal, contributor pages,
# changelog churn, and non-HTML assets. Applies to all multi-page tiers
# regardless of language selection.
DEFAULT_DENY_PATTERNS: tuple[str, ...] = (
    # release / churn
    "*/blog/*", "*/news/*", "*/posts/*", "*/announcements/*",
    "*/changelog/*", "*/changelogs/*", "*/releases/*", "*/release-notes/*",
    "*/whats-new/*", "*/history/*",
    # marketing
    "*/pricing/*", "*/jobs/*", "*/careers/*", "*/contact/*",
    "*/case-studies/*", "*/customers/*", "*/events/*", "*/webinar*",
    "*/newsletter/*", "*/partners/*", "*/solutions/*", "*/products/*",
    "*/enterprise/*",
    # legal / governance
    "*/legal/*", "*/privacy/*", "*/terms/*", "*/cookie*",
    "*/trademark*", "*/license*/*", "*/lics/*",
    "*/about/*", "*/team/*", "*/sponsors/*", "*/governance/*",
    # contributor / community
    "*/contributing/*", "*/contribute/*", "*/code-of-conduct/*",
    "*/security-policy/*", "*/security/*", "*/community/*",
    "*/forum/*", "*/discuss/*", "*/gallery/*", "*/showcase/*",
    # stale / archived
    "*/archive/*", "*/archives/*", "*/legacy/*", "*/old/*",
    "*/deprecated/*",
    # generated / index pages
    "*/search.html", "*/genindex*", "*/py-modindex*",
    "*/tag/*", "*/tags/*", "*/categories/*",
    # non-HTML
    "*.pdf", "*.zip", "*.tar", "*.gz", "*.tgz",
    "*.png", "*.jpg", "*.jpeg", "*.gif", "*.svg", "*.webp",
    "*.mp4", "*.mov", "*.webm",
)


# Localization paths to drop (when target language is English-biased).
NON_TARGET_LANGUAGE_PATH_RE = re.compile(
    r"/(zh|cn|ja|ko|pt|fr|de|es|ru|it|tr|pl|nl|vi|th|ar|id|hi)(-[a-z]{2})?(/|$)",
    re.IGNORECASE,
)


# =============================================================================
# Path-pattern filter — stage 1 of the noise removal pipeline.
# Cheap deterministic regex match on URL paths. Catches obvious non-docs
# pages (events / blog / changelog / jobs / sponsor) BEFORE we fetch
# them, saving bandwidth + MinIO storage + planner embedding cost.
# Catalog entries can extend or replace these defaults via the
# `path_filter` field in sources.yaml.
# =============================================================================

# Conservative defaults — only patterns that are NEVER teaching content.
# Things like `/contributing/`, `/community/`, `/code-of-conduct/` are
# intentionally absent because some frameworks DO host real teaching
# content under those paths; we let the semantic off_topic filter in the
# planner handle those.
DEFAULT_EXCLUDE_PATH_PATTERNS: tuple[str, ...] = (
    # Marketing / community
    r"/events?(\.html?|/|$)",
    r"/blog(\.html?|/|$)",
    r"/news(\.html?|/|$)",
    r"/jobs?(\.html?|/|$)",
    r"/careers?(\.html?|/|$)",
    r"/hiring(\.html?|/|$)",
    r"/sponsors?(\.html?|/|$)",
    r"/meetups?(\.html?|/|$)",
    # Release churn — both directory and file forms. The single-file
    # variant catches Kafka `changelog.html` (69 H2s), SHAP
    # `release_notes.html` (91 H2s), Novu `changelog.html`, ADTK
    # `releasehistory.html`. The version-tag form catches XGBoost-style
    # `v2.1.0.html` per-version release pages.
    r"/changelogs?(\.html?|/|$)",
    r"/release[-_]?notes?(\.html?|/|$)",
    r"/releases(\.html?|/|$)",
    r"/release[-_]?history(\.html?|/|$)",
    r"/releasehistory(\.html?|/|$)",
    r"/whats[-_]?new(\.html?|/|$)",
    r"/whatsnew(\.html?|/|$)",
    r"/history(\.html?|/|$)",
    r"/migration[-_]?guide(\.html?|/|$)",
    r"/upgrad(?:e|ing)(\.html?|/|$)",
    r"/v\d+(?:\.\d+){1,3}(?:\.html?)?$",
)

_DEFAULT_EXCLUDE_RE = re.compile(
    "|".join(f"(?:{p})" for p in DEFAULT_EXCLUDE_PATH_PATTERNS),
    re.IGNORECASE,
)
