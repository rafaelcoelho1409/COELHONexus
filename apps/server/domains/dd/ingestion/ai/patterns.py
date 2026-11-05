from __future__ import annotations

import re


# A run id is a `uuid4().hex`.
RUN_ID_RE = re.compile(r"^[a-f0-9]{32}$")
