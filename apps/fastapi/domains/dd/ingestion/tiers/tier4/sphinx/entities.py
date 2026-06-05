"""Value objects for the sphinx discovery + split pipeline. Inventory
methods are read-only queries that travel with the parsed objects.inv."""
from __future__ import annotations

from typing import NamedTuple

from .params import DOC_ROLES, SPLIT_MEMBER_ROLES, SPLIT_TOP_ROLES


class InventoryEntity(NamedTuple):
    """One entry from a Sphinx `objects.inv` payload."""
    name:     str    # "adtk.detector.ThresholdAD"
    role:     str    # "py:class" / "std:doc" / "py:method" / …
    page_url: str    # absolute URL, no fragment
    anchor:   str    # fragment ID (e.g. "adtk.detector.ThresholdAD"); "" for std:doc
    dispname: str    # human-readable display name


class Inventory(NamedTuple):
    """Parsed Sphinx `objects.inv` — canonical entity catalog for one site."""
    project:  str
    version:  str
    base_url: str
    entities: list[InventoryEntity]

    def doc_pages(self) -> set[str]:
        """Page URLs reachable from std:doc / std:label — canonical crawl set."""
        return {
            e.page_url for e in self.entities
            if e.role in DOC_ROLES and e.page_url
        }

    def all_pages(self) -> set[str]:
        """All entity pages — superset of doc_pages including autodoc hosts."""
        return {e.page_url for e in self.entities if e.page_url}

    def splittable_entities_on(
        self, page_url: str,
    ) -> tuple[list[InventoryEntity], list[InventoryEntity]]:
        """(top, members). top = classes/functions/exceptions/modules.
        Falls back to members on 1-class-N-methods pages."""
        norm = page_url.split("#", 1)[0]
        top:     list[InventoryEntity] = []
        members: list[InventoryEntity] = []
        for e in self.entities:
            if e.page_url != norm or not e.anchor:
                continue
            if e.role in SPLIT_TOP_ROLES:
                top.append(e)
            elif e.role in SPLIT_MEMBER_ROLES:
                members.append(e)
        return top, members


class SubPage(NamedTuple):
    """One virtual sub-page emitted by maybe_split_page."""
    slug_suffix: str   # appended to parent slug; never contains slashes
    sub_url:     str   # parent URL + `#anchor` for citation traceability
    title:       str   # human-readable section title
    body_md:     str   # markdown for THIS sub-section only
