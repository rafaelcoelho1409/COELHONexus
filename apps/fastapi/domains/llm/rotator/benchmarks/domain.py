from __future__ import annotations

try:
    from bs4 import BeautifulSoup
    _BS4_AVAILABLE = True
except ImportError:
    _BS4_AVAILABLE = False

from .params import (
    _OPENEVALS_BENCHMARK_MAP,
    _OPENLM_COLUMN_MAP,
    _PROVIDER_SUFFIXES,
    SCORE_NORMS,
)
from .patterns import (
    CELL_NUMBER_RE,
    DASH_RUN_RE,
    PROVIDER_PREFIX_RE,
    WHITESPACE_RE,
)


def normalize_model_name(name: str) -> str:
    """L1 normalizer; preserves size identifiers (-flash, -lite, -nano, -mini)."""
    s = (name or "").strip().lower()
    s = PROVIDER_PREFIX_RE.sub("", s)
    s = WHITESPACE_RE.sub("-", s)
    for _ in range(4):
        before = s
        for suffix in _PROVIDER_SUFFIXES:
            if s.endswith(suffix):
                s = s[: -len(suffix)]
                break
        if s == before:
            break
    s = DASH_RUN_RE.sub("-", s)
    return s.strip("-")


def coerce_score(raw: str) -> float | None:
    if not raw or raw.strip() in ("—", "-", "—", "N/A", "TBD"):
        return None
    m = CELL_NUMBER_RE.search(raw.replace(",", ""))
    if not m:
        return None
    try:
        return float(m.group(0))
    except ValueError:
        return None


def normalize_openevals_key(key: str) -> str | None:
    k = (key or "").strip().lower().replace("-", "_").replace(" ", "_")
    return _OPENEVALS_BENCHMARK_MAP.get(k)


def parse_openlm_table(html: str) -> dict[str, dict[str, float]]:
    """OpenLM.ai Chatbot Arena+ HTML; picks the table whose headers contain both 'Arena' and 'Model'."""
    if not _BS4_AVAILABLE:
        return {}
    soup = BeautifulSoup(html, "html.parser")
    best_table = None
    best_headers: list[str] = []
    for tbl in soup.find_all("table"):
        head = tbl.find("tr")
        if not head:
            continue
        headers = [
            (th.get_text(" ", strip = True) or "").lower()
            for th in head.find_all(["th", "td"])
        ]
        has_model = any("model" in h for h in headers)
        has_arena = any("arena" in h for h in headers)
        if has_model and has_arena and len(headers) > len(best_headers):
            best_table = tbl
            best_headers = headers
    if best_table is None:
        return {}
    model_col_idx = next(
        (i for i, h in enumerate(best_headers) if "model" in h), None
    )
    if model_col_idx is None:
        return {}
    metric_cols: list[tuple[int, str]] = []
    for idx, header in enumerate(best_headers):
        for substr, our_key in _OPENLM_COLUMN_MAP.items():
            if substr in header:
                metric_cols.append((idx, our_key))
                break
    out: dict[str, dict[str, float]] = {}
    for tr in best_table.find_all("tr")[1:]:
        cells = tr.find_all(["td", "th"])
        if len(cells) <= model_col_idx:
            continue
        name = cells[model_col_idx].get_text(" ", strip=True)
        if not name:
            continue
        scores: dict[str, float] = {}
        for col_idx, key in metric_cols:
            if col_idx >= len(cells):
                continue
            v = coerce_score(cells[col_idx].get_text(" ", strip=True))
            if v is not None:
                scores[key] = v
        if scores:
            out[normalize_model_name(name)] = scores
    return out


def parse_openevals_payload(body: dict) -> dict[str, dict[str, float]]:
    out: dict[str, dict[str, float]] = {}
    models = body.get("models") or body.get("results") or []
    for item in models:
        if not isinstance(item, dict):
            continue
        name = (
            item.get("model_id") or item.get("model") or item.get("name")
            or item.get("id") or ""
        )
        if not name:
            continue
        scores_source = item.get("scores") or item.get("metrics") or item
        if not isinstance(scores_source, dict):
            continue
        scores: dict[str, float] = {}
        for raw_key, raw_val in scores_source.items():
            our_key = normalize_openevals_key(raw_key)
            if our_key is None:
                continue
            if isinstance(raw_val, dict):
                raw_val = raw_val.get("value") or raw_val.get("score")
            if raw_val is None:
                continue
            try:
                scores[our_key] = float(raw_val)
            except (TypeError, ValueError):
                continue
        if scores:
            out[normalize_model_name(str(name))] = scores
    return out


def parse_oolong_payload(body: dict) -> dict[str, dict[str, float]]:
    out: dict[str, dict[str, float]] = {}
    for item in body.get("models") or []:
        if not isinstance(item, dict):
            continue
        name = item.get("model") or item.get("name") or ""
        if not name:
            continue
        elo = item.get("score") or item.get("rating") or item.get("elo")
        if elo is None:
            continue
        try:
            out[normalize_model_name(str(name))] = {"lmarena_coding": float(elo)}
        except (TypeError, ValueError):
            continue
    return out


def normalize_metric(metric: str, raw: float) -> float:
    lo, hi = SCORE_NORMS.get(metric, (0.0, 1.0))
    if hi <= lo:
        return 0.0
    return max(0.0, min(1.0, (raw - lo) / (hi - lo)))


def compute_composite_score(
    scores: dict[str, float],
    weights: dict[str, float],
) -> float:
    """Denominator = sum of weights for PRESENT metrics — partial coverage comparable to full."""
    if not scores or not weights:
        return 0.0
    weighted_sum = 0.0
    weight_total = 0.0
    for metric, weight in weights.items():
        raw = scores.get(metric)
        if raw is None:
            continue
        norm = normalize_metric(metric, raw)
        weighted_sum += weight * norm
        weight_total += weight
    if weight_total == 0.0:
        return 0.0
    return weighted_sum / weight_total


def compute_warm_start_score(
    benchmark_score: float,
    pilot_score: float | None,
    *,
    alpha: float,
) -> float:
    """alpha=1.0 → pure benchmark prior; alpha=0.0 → pure PILOT posterior."""
    a = max(0.0, min(1.0, alpha))
    if pilot_score is None:
        return benchmark_score
    return a * benchmark_score + (1.0 - a) * pilot_score


def merge_leaderboards(
    canonical: str,
    boards: list[dict[str, dict[str, float]]],
) -> dict[str, float]:
    merged: dict[str, float] = {}
    for board in boards:
        per_model = board.get(canonical, {})
        if per_model:
            merged.update(per_model)
    return merged
