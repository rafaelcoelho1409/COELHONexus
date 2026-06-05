from __future__ import annotations


# fnmatch deny-list applied to every multi-page tier — strips marketing, legal,
# contributor pages, churn, and non-HTML assets.
DEFAULT_DENY_PATTERNS: tuple[str, ...] = (
    "*/blog/*", "*/news/*", "*/posts/*", "*/announcements/*",
    "*/changelog/*", "*/changelogs/*", "*/releases/*", "*/release-notes/*",
    "*/whats-new/*", "*/history/*",
    "*/pricing/*", "*/jobs/*", "*/careers/*", "*/contact/*",
    "*/case-studies/*", "*/customers/*", "*/events/*", "*/webinar*",
    "*/newsletter/*", "*/partners/*", "*/solutions/*", "*/products/*",
    "*/enterprise/*",
    "*/legal/*", "*/privacy/*", "*/terms/*", "*/cookie*",
    "*/trademark*", "*/license*/*", "*/lics/*",
    "*/about/*", "*/team/*", "*/sponsors/*", "*/governance/*",
    "*/contributing/*", "*/contribute/*", "*/code-of-conduct/*",
    "*/security-policy/*", "*/security/*", "*/community/*",
    "*/forum/*", "*/discuss/*", "*/gallery/*", "*/showcase/*",
    "*/archive/*", "*/archives/*", "*/legacy/*", "*/old/*",
    "*/deprecated/*",
    "*/search.html", "*/genindex*", "*/py-modindex*",
    "*/tag/*", "*/tags/*", "*/categories/*",
    "*.pdf", "*.zip", "*.tar", "*.gz", "*.tgz",
    "*.png", "*.jpg", "*.jpeg", "*.gif", "*.svg", "*.webp",
    "*.mp4", "*.mov", "*.webm",
)


# Conservative stage-1 path-noise filter. `/contributing/`, `/community/`,
# `/code-of-conduct/` intentionally absent — some frameworks host real teaching
# content there; semantic off_topic in planner handles those.
# Version-tag form catches XGBoost-style `v2.1.0.html` per-version pages.
DEFAULT_EXCLUDE_PATH_PATTERNS: tuple[str, ...] = (
    r"/events?(\.html?|/|$)",
    r"/blog(\.html?|/|$)",
    r"/news(\.html?|/|$)",
    r"/jobs?(\.html?|/|$)",
    r"/careers?(\.html?|/|$)",
    r"/hiring(\.html?|/|$)",
    r"/sponsors?(\.html?|/|$)",
    r"/meetups?(\.html?|/|$)",
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
