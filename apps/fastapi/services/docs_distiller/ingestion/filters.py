"""URL & language filters shared across Tier 3 / Tier 4.

Centralizes the catalog of polyglot frameworks, per-language path slugs,
and the URL deny-list. Carried forward from the validated v3 ingest path
so behavior on Docker / OpenTelemetry / Kubernetes / Grafana stays the
same — only the surrounding infrastructure (storage, dispatch) is new.
"""
import fnmatch
import re
from urllib.parse import urlparse


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


def is_polyglot(framework_name: str) -> bool:
    return (framework_name or "").strip().lower() in POLYGLOT_FRAMEWORKS


def build_language_filter(
    language: str | None,
) -> tuple[list[str], list[str]]:
    """Return (allow, deny) glob lists for the given language.

    No language → empty allow (don't over-restrict on unusual path layouts)
    + the standard deny list.

    Specific language → allow only that language's slugs (plus a small set
    of language-agnostic ones) + deny all other languages' slugs.
    """
    if not language:
        return [], list(DEFAULT_DENY_PATTERNS)

    key = language.strip().lower()
    target = LANGUAGE_PATH_MAP.get(key, [key])

    # Drop 2-char slugs from the *other* languages' deny list — "js" and
    # "go" alone match too much else (e.g. javascript samples inside a
    # Python project).
    other_slugs = [
        slug
        for k, slugs in LANGUAGE_PATH_MAP.items()
        if k != key
        for slug in slugs
        if len(slug) > 2
    ]

    allow = [
        "*concept*", "*specification*", "*spec*", "*overview*",
        *[f"*/{s}/*" for s in target],
        *[f"*/{s}-*/*" for s in target],
    ]
    deny = [
        *DEFAULT_DENY_PATTERNS,
        *[f"*/{s}/*" for s in other_slugs],
    ]
    return allow, deny


def should_keep(
    url: str,
    allow: list[str],
    deny: list[str],
) -> bool:
    """fnmatch glob test — pass any explicit allow first; otherwise
    pass when nothing in deny matches.
    """
    if any(fnmatch.fnmatch(url, p) for p in deny):
        return False
    if allow:
        return any(fnmatch.fnmatch(url, p) for p in allow)
    return True


def same_host(url: str, host: str) -> bool:
    return (urlparse(url).netloc or "").lower() == host.lower()
