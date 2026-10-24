"""resolver schemas — HTTP boundary types for the catalog concept.

`CatalogEntry` is the shared request-scoped annotation ("slug path
param → catalog dict or 404", resolved by `service.get_catalog_entry`)
used by the resolver/runs/debug routers.
"""
from . import service

from typing import Annotated

from fastapi import Depends


CatalogEntry = Annotated[dict, Depends(service.get_catalog_entry)]
