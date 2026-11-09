"""page_cards — pre-compiled regex patterns."""
from __future__ import annotations

import re


REFERENCE_PATH_RE = re.compile(r"(?:^|/)(?:api|reference|refs?|sdk)(?:/|$)", re.IGNORECASE)
REFERENCE_DOC_PATH_RE = re.compile(r"(?:^|/)(?:reference|refs?)(?:/|$)", re.IGNORECASE)
API_PATH_RE = re.compile(r"(?:^|/)api(?:/|$)", re.IGNORECASE)
LOOKUP_FILE_RE = re.compile(
    r"(?:^|[-_/])(?:api-reference|api-docs|references?|integrations?)(?:[-_/]|\.[a-z0-9]+$)",
    re.IGNORECASE,
)
LEARNING_FILE_RE = re.compile(
    r"(?:^|[-_/])(?:build|customi[sz]e|getting-started|quickstart|tutorial|how-to|"
    r"troubleshooting|best-practices|common-patterns|why-use|using|human-in-the-loop|"
    r"async|dynamic|fault-tolerance|event-streaming|streaming|context-management|"
    r"permissions|profiles|system-prompt|memory|sandbox)(?:[-_/]|\.[a-z0-9]+$)",
    re.IGNORECASE,
)
TEACHING_LANGUAGE_RE = re.compile(
    r"\b(?:build(?:s|ing)?|configur(?:e|es|ing|ation)|creat(?:e|es|ing)|implement(?:s|ing)?|"
    r"learn(?:s|ing)?|set(?:s|ting)? up|walks? through|step-by-step|tutorial|example)\b",
    re.IGNORECASE,
)
HEADING_RE = re.compile(r"^#{1,3}\s+(.+?)\s*#*\s*$", re.MULTILINE)
