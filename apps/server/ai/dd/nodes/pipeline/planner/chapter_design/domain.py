"""Deterministic candidate grouping, score summaries, and assignment repair."""
from __future__ import annotations

import heapq
import math
import re
from collections import Counter, defaultdict

from . import params


TOKEN_RE = re.compile(r"[a-z][a-z0-9_]{1,}", re.IGNORECASE)
STOP = frozenset({
    "the", "and", "for", "this", "that", "with", "from", "into", 
    "your", "you", "are", "was", "use", "using", "how", "can", 
    "will", "not", "but", "its", "has", "have", "guide", "docs",
    "page", "overview", "reference", "documentation", "chapter", 
    "learn", "learns",
})


def tokens(text: str) -> list[str]:
    return [token.lower() for token in TOKEN_RE.findall(text or "") if token.lower() not in STOP]


def card_text(card: dict) -> str:
    """Return the consistent semantic text used for embedding and lexical similarity."""
    def items(value) -> list[str]:
        if isinstance(value, str):
            return [value]
        if isinstance(value, (list, tuple)):
            return [str(item) for item in value if item is not None]
        return []

    fields = [
        *items(card.get("title")),
        *items(card.get("topic")),
        *items(card.get("summary")),
        *items(card.get("teaches")),
        *items(card.get("prerequisites")),
        (str(card.get("key") or "").rsplit("/", 1)[-1].replace("-", " ")),
    ]
    return " ".join(value.strip() for value in fields if value and value.strip())


def local_tfidf(cards: list[dict]) -> list[dict[str, float]]:
    """A deterministic fallback when the configured embedding endpoint is unavailable."""
    documents = [Counter(tokens(card_text(card))) for card in cards]
    df = Counter(token for doc in documents for token in doc)
    n = max(1, len(documents))
    out: list[dict[str, float]] = []
    for doc in documents:
        vector = {token: (1.0 + math.log(count)) * (1.0 + math.log((n + 1) / (df[token] + 1))) for token, count in doc.items()}
        norm = math.sqrt(sum(value * value for value in vector.values())) or 1.0
        out.append({token: value / norm for token, value in vector.items()})
    return out


def cosine(first, second) -> float:
    if isinstance(first, dict) and isinstance(second, dict):
        if len(first) > len(second):
            first, second = second, first
        dot = sum(value * second.get(key, 0.0) for key, value in first.items())
        n1 = math.sqrt(sum(value * value for value in first.values()))
        n2 = math.sqrt(sum(value * value for value in second.values()))
    else:
        a = list(first or [])
        b = list(second or [])
        if len(a) != len(b) or not a:
            return 0.0
        dot = sum(x * y for x, y in zip(a, b))
        n1 = math.sqrt(sum(x * x for x in a))
        n2 = math.sqrt(sum(y * y for y in b))
    return max(-1.0, min(1.0, dot / (n1 * n2))) if n1 and n2 else 0.0


def normalize_embeddings(vectors: list[list[float]], expected: int) -> list[list[float]] | None:
    if len(vectors) != expected or not vectors:
        return None
    width = len(vectors[0])
    if not width or any(len(vector) != width for vector in vectors):
        return None
    normalized = []
    for vector in vectors:
        norm = math.sqrt(sum(float(value) ** 2 for value in vector))
        normalized.append([float(value) / norm for value in vector] if norm else [0.0] * width)
    return normalized


def _same_directory(first: dict, second: dict) -> bool:
    a = (first.get("url") or first.get("key") or "").rstrip("/").rsplit("/", 1)[0]
    b = (second.get("url") or second.get("key") or "").rstrip("/").rsplit("/", 1)[0]
    return bool(a and a == b)


def _hierarchical_cuts(cards: list[dict], vectors, wanted: set[int], url_weight: float) -> dict[int, list[list[int]]]:
    """Average-link hierarchy with a deterministic heap; snapshots are taken at requested chapter counts."""
    n = len(cards)
    if not n:
        return {}
    active = set(range(n))
    members = {i: [i] for i in range(n)}
    sizes = {i: 1 for i in range(n)}
    similarities: dict[tuple[int, int], float] = {}
    heap: list[tuple[float, int, int]] = []
    for i in range(n):
        for j in range(i + 1, n):
            score = cosine(vectors[i], vectors[j])
            if _same_directory(cards[i], cards[j]):
                score += url_weight
            score = max(-1.0, min(1.0, score))
            similarities[(i, j)] = score
            heapq.heappush(heap, (-score, i, j))

    snapshots: dict[int, list[list[int]]] = {}
    if n in wanted:
        snapshots[n] = [[i] for i in range(n)]
    next_id = n
    min_k = min(wanted) if wanted else 1
    while len(active) > min_k:
        while heap:
            neg_score, left, right = heapq.heappop(heap)
            if left in active and right in active:
                break
        else:
            break
        merged = next_id
        next_id += 1
        other_ids = [item for item in active if item != left and item != right]
        for other in other_ids:
            left_key = (min(left, other), max(left, other))
            right_key = (min(right, other), max(right, other))
            sim_sum = similarities.get(left_key, 0.0) * sizes[left] + similarities.get(right_key, 0.0) * sizes[right]
            score = sim_sum / (sizes[left] + sizes[right])
            pair = (min(merged, other), max(merged, other))
            similarities[pair] = score
            heapq.heappush(heap, (-score, pair[0], pair[1]))
        members[merged] = sorted(members[left] + members[right])
        sizes[merged] = sizes[left] + sizes[right]
        active.remove(left)
        active.remove(right)
        active.add(merged)
        if len(active) in wanted:
            snapshots[len(active)] = [members[cluster] for cluster in sorted(active, key = lambda item: members[item][0])]
    return snapshots


def _reference_groups(cards: list[dict]) -> list[list[int]]:
    groups: dict[str, list[int]] = defaultdict(list)
    for index, card in enumerate(cards):
        url = card.get("url") or card.get("key") or ""
        directory = url.rstrip("/").rsplit("/", 1)[0]
        groups[directory or "(reference)"].append(index)
    out: list[list[int]] = []
    for directory, indices in sorted(groups.items()):
        for offset in range(0, len(indices), params.REFERENCE_GROUP_PAGES):
            out.append(indices[offset:offset + params.REFERENCE_GROUP_PAGES])
    return out


def _partition_metrics(
    groups: list[list[int]], cards: list[dict], vectors, total_minutes: float, target: int,
    chapter_overhead_minutes: float = 0.0,
) -> dict:
    teaching_groups = [[idx for idx in group if cards[idx].get("kind") != "reference"] for group in groups]
    teaching_groups = [group for group in teaching_groups if group]
    intra = []
    centroids = []
    minutes = []
    for group in teaching_groups:
        pair_scores = [cosine(vectors[a], vectors[b]) for pos, a in enumerate(group) for b in group[pos + 1:]]
        if pair_scores:
            intra.extend(pair_scores)
        if vectors and isinstance(vectors[0], list):
            width = len(vectors[0])
            centroid = [sum(vectors[idx][dim] for idx in group) / len(group) for dim in range(width)]
        else:
            centroid: dict[str, float] = {}
            for idx in group:
                for token, value in vectors[idx].items():
                    centroid[token] = centroid.get(token, 0.0) + value / len(group)
        centroids.append(centroid)
        minutes.append(
            sum(float(cards[idx].get("tutorial_example_minutes") or 0.0) for idx in group)
            + chapter_overhead_minutes
        )
    inter = [cosine(centroids[a], centroids[b]) for a in range(len(centroids)) for b in range(a + 1, len(centroids))]
    cohesion = sum(intra) / len(intra) if intra else 0.0
    separation = sum(inter) / len(inter) if inter else 0.0
    mean = sum(minutes) / len(minutes) if minutes else 0.0
    variance = sum((value - mean) ** 2 for value in minutes) / len(minutes) if minutes else 0.0
    balance = max(0.0, 1.0 - min(1.0, math.sqrt(variance) / mean)) if mean else 1.0
    over_budget = sum(1 for value in minutes if value > params.MAX_MINUTES)
    over_budget_excess = sum(
        max(0.0, value - params.MAX_MINUTES) / params.MAX_MINUTES
        for value in minutes
    ) if params.MAX_MINUTES > 0 else 0.0
    count_distance = abs(len(teaching_groups) - target) / max(target, 1)
    count_ratio = len(teaching_groups) / max(target, 1) if teaching_groups else 1.0
    count_penalty = (
        params.TOO_MANY_CHAPTERS_WEIGHT * max(0.0, math.log(count_ratio))
        + params.TOO_FEW_CHAPTERS_WEIGHT * max(0.0, math.log(1.0 / count_ratio))
    )
    # Penalize both fragmentation and chapters that are too large to be useful.
    # This is a ranking signal, not a chapter-count ceiling: the architect can
    # still choose a larger count when the source cards show distinct objectives.
    score = (
        0.45 * cohesion
        + 0.30 * (1.0 - separation)
        + 0.20 * balance
        - 0.06 * over_budget
        - params.OVER_BUDGET_WEIGHT * over_budget_excess
        - count_penalty
    )
    return {
        "n_tutorial_chapters": len(teaching_groups),
        "tutorial_minutes": round(total_minutes, 1),
        "chapter_tutorial_minutes": [round(value, 1) for value in minutes],
        "cohesion": round(cohesion, 4),
        "separation": round(separation, 4),
        "balance": round(balance, 4),
        "over_budget": over_budget,
        "over_budget_excess": round(over_budget_excess, 4),
        "target_distance": count_distance,
        "count_penalty": round(count_penalty, 4),
        "score": round(score, 4),
    }


def build_candidate_partitions(
    cards: list[dict], vectors, *, total_minutes: float, must_teach_by_page: dict[str, int],
    best_code_by_page: dict[str, list[dict]] | None = None,
    chapter_overhead_minutes: float = 0.0,
) -> dict:
    teaching = [card for card in cards if card.get("kind") != "reference"]
    references = [card for card in cards if card.get("kind") == "reference"]
    card_index = {card["key"]: i for i, card in enumerate(cards)}
    n_best_code = sum(must_teach_by_page.values())
    target_basis = "unique_tutorial_minutes" if n_best_code else "teaching_page_fallback"
    target = (
        int(round(total_minutes / params.TARGET_MINUTES))
        if n_best_code
        else max(1, int(round(len(teaching) / 11)))
    )
    target = max(1, min(len(teaching) or 1, target))
    # Score the complete hierarchy. The 8-option budget below limits prompt size,
    # not the number of chapters the architect may select or return.
    wanted = set(range(1, len(teaching) + 1)) if teaching else set()
    teaching_indices = [card_index[card["key"]] for card in teaching]
    teaching_vectors = [vectors[index] for index in teaching_indices]
    teaching_cards = [cards[index] for index in teaching_indices]
    reference_indices = [card_index[card["key"]] for card in references]
    reference_groups = _reference_groups(references)
    reference_groups = [[reference_indices[index] for index in group] for group in reference_groups]

    alternatives: list[tuple[str, int, list[list[int]]]] = []
    for label, url_weight in (("semantic", 0.0), ("navigation", params.URL_SIMILARITY_BONUS)):
        cuts = _hierarchical_cuts(teaching_cards, teaching_vectors, wanted, url_weight)
        for k, groups in sorted(cuts.items()):
            global_groups = [[teaching_indices[index] for index in group] for group in groups]
            alternatives.append((label, k, global_groups))
    if not alternatives and references:
        alternatives.append(("reference", 0, []))
    if not alternatives and teaching:
        alternatives.append(("fallback", 1, [teaching_indices]))

    ranked = []
    seen = set()
    for label, k, groups in alternatives:
        signature = tuple(tuple(cards[index]["key"] for index in group) for group in groups)
        if signature in seen:
            continue
        seen.add(signature)
        partition_id = f"{label}-{k:02d}"
        metrics = _partition_metrics(groups, cards, vectors, total_minutes, target, chapter_overhead_minutes)
        ranked.append({
            "id": partition_id,
            "method": label,
            "groups": groups,
            "metrics": metrics,
        })

    ranked.sort(key = lambda item: (-item["metrics"]["score"], item["metrics"]["n_tutorial_chapters"], item["id"]))
    # A candidate whose largest tutorial group is many times the soft chapter
    # budget cannot be a usable study plan; it only pushes the real design into
    # downstream emergency splitting. Keep all other counts eligible, so this
    # does not impose a maximum chapter count.
    max_candidate_minutes = params.MAX_MINUTES * params.MAX_CANDIDATE_CHAPTER_OVER_BUDGET_FACTOR
    viable = [
        item for item in ranked
        if max(item["metrics"].get("chapter_tutorial_minutes") or [0.0]) <= max_candidate_minutes
    ]
    if not viable:
        viable = ranked
    n_rejected_extreme = len(ranked) - len(viable)

    # Keep the design prompt compact while exposing viable concise and broad
    # chapter-count choices, plus the strongest measured partitions.
    available_counts = sorted({item["metrics"]["n_tutorial_chapters"] for item in viable})
    n_teaching = len(teaching)
    # Offer the smallest viable partition and the time-based estimate; chapter
    # count remains a soft anchor and there is no maximum.
    anchor_counts = {target}
    if available_counts:
        anchor_counts.add(available_counts[0])
    anchor_counts.update(round(target * factor) for factor in (0.5, 1.5, 2.0))
    anchor_counts.update(round(n_teaching * fraction) for fraction in (0.25, 0.5))
    selected = []
    selected_counts = set()
    for wanted_count in sorted(anchor_counts):
        if not available_counts:
            break
        count = min(available_counts, key = lambda value: (abs(value - wanted_count), value))
        if count in selected_counts:
            continue
        option = next(item for item in viable if item["metrics"]["n_tutorial_chapters"] == count)
        selected.append(option)
        selected_counts.add(count)
    for option in viable:
        if len(selected) >= params.MAX_PARTITION_OPTIONS:
            break
        if option["id"] not in {item["id"] for item in selected}:
            selected.append(option)
    selected.sort(key = lambda item: (-item["metrics"]["score"], item["metrics"]["n_tutorial_chapters"], item["id"]))

    partitions = []
    for option in selected:
        label, k, groups, metrics = option["method"], option["metrics"]["n_tutorial_chapters"], option["groups"], option["metrics"]
        partition_id = option["id"]
        all_groups = [*groups, *reference_groups]
        group_rows = []
        for group_index, group in enumerate(all_groups, start = 1):
            group_id = f"{partition_id}-g{group_index:02d}"
            page_rows = []
            for index in group:
                card = cards[index]
                page_rows.append({
                    "key": card["key"],
                    "title": card.get("title") or card["key"].rsplit("/", 1)[-1],
                    "topic": card.get("topic") or "",
                    "teaches": card.get("teaches") or [],
                    "kind": card.get("kind") or "concept",
                    "code_role": card.get("code_role") or "none",
                    "best_code_count": int(must_teach_by_page.get(card["key"], 0)),
                    "best_code": list((best_code_by_page or {}).get(card["key"], [])),
                    "reading_minutes": float(card.get("reading_minutes") or 0.0),
                })
            top_topics = Counter(row["topic"] for row in page_rows if row["topic"])
            group_rows.append({
                "group_id": group_id,
                "kind": "reference" if all(cards[index].get("kind") == "reference" for index in group) else "teaching",
                "n_pages": len(group),
                "reading_minutes": round(sum(row["reading_minutes"] for row in page_rows), 1),
                "n_best_code": sum(row["best_code_count"] for row in page_rows),
                "best_code": [fact for row in page_rows for fact in row["best_code"]],
                "topics": [topic for topic, _ in top_topics.most_common(8)],
                "pages": page_rows,
            })
        partitions.append({
            "id": partition_id,
            "method": label,
            "groups": group_rows,
            "metrics": metrics,
        })
    return {
        "target_chapters": target,
        "target_basis": target_basis,
        "n_partitions_total_scored": len(ranked),
        "n_partitions_rejected_extreme": n_rejected_extreme,
        "target_minutes": params.TARGET_MINUTES,
        "max_minutes_soft": params.MAX_MINUTES,
        "chapter_overhead_minutes": chapter_overhead_minutes,
        "unique_tutorial_minutes": round(total_minutes, 1),
        "n_best_code": sum(must_teach_by_page.values()),
        "partitions": partitions,
    }


def _reachable(start: int, goal: int, edges: dict[int, set[int]]) -> bool:
    todo = [start]
    seen = set()
    while todo:
        item = todo.pop()
        if item == goal:
            return True
        if item in seen:
            continue
        seen.add(item)
        todo.extend(edges.get(item) or ())
    return False


def _concept_key(value) -> str:
    return re.sub(r"[^a-z0-9]+", " ", str(value or "").casefold()).strip()


def _reference_title(value: str) -> str:
    """Append one consistent reference suffix, even when source topics already include it."""
    title = " ".join(str(value or "").split())
    title = re.sub(r"(?:\s+(?:api\s+)?reference)+$", "", title, flags = re.IGNORECASE).strip()
    return f"{title or 'API'} Reference"


def _chapter_teaching_concepts(chapter: dict, cards_by_key: dict[str, dict]) -> set[str]:
    """Source-grounded tutorial concepts; lookup-only reference pages don't create required lessons."""
    member_cards = [
        cards_by_key.get(page_key) or {}
        for page_key in chapter.get("member_doc_keys") or []
    ]
    if member_cards and all(card.get("kind") == "reference" for card in member_cards):
        return set()
    values = list(chapter.get("key_concepts") or [])
    for card in member_cards:
        if card.get("kind") == "reference":
            continue
        topic = card.get("topic")
        if topic:
            values.append(topic)
        teaches = card.get("teaches") or []
        if isinstance(teaches, str):
            values.append(teaches)
        elif isinstance(teaches, (list, tuple)):
            values.extend(teaches)
    return {normalized for value in values if (normalized := _concept_key(value))}


def recover_partition_choice(answer: dict | None, candidates: dict) -> tuple[dict | None, str | None, bool]:
    """Normalize a bad partition label from group ids; mark exact, complete matches as safe recovery."""
    if not isinstance(answer, dict):
        return answer, None, False
    partitions = candidates.get("partitions") or []
    by_id = {
        item.get("id"): item
        for item in partitions
        if isinstance(item, dict) and isinstance(item.get("id"), str)
    }
    requested = answer.get("candidate_partition_id")
    if isinstance(requested, str) and requested in by_id:
        return answer, None, False

    assigned: list[str] = []
    for chapter in answer.get("chapters") or []:
        if not isinstance(chapter, dict):
            return answer, None, False
        group_ids = chapter.get("group_ids") or []
        if not isinstance(group_ids, list) or any(not isinstance(group_id, str) or not group_id for group_id in group_ids):
            return answer, None, False
        assigned.extend(group_ids)
    if not assigned:
        return answer, None, False

    exact_matches = []
    ranked_matches = []
    assigned_set = set(assigned)
    for partition in by_id.values():
        expected = {
            group.get("group_id")
            for group in partition.get("groups") or []
            if isinstance(group, dict) and isinstance(group.get("group_id"), str)
        }
        if not expected:
            continue
        overlap = len(assigned_set & expected)
        if overlap:
            ranked_matches.append((overlap, partition))
        if len(assigned) == len(assigned_set) and len(expected) == len(assigned) and expected == assigned_set:
            exact_matches.append(partition)
    if len(exact_matches) == 1:
        selected = exact_matches[0]
        exact = True
    else:
        ranked_matches.sort(key = lambda item: item[0], reverse = True)
        if not ranked_matches or len(ranked_matches) > 1 and ranked_matches[0][0] == ranked_matches[1][0]:
            return answer, None, False
        selected = ranked_matches[0][1]
        exact = False

    recovered = dict(answer)
    recovered_id = selected["id"]
    recovered["candidate_partition_id"] = recovered_id
    return recovered, recovered_id, exact


def reconcile_design(answer: dict | None, candidates: dict, cards: list[dict]) -> tuple[list[dict], dict]:
    """Map group ids to pages and repair omissions, duplicates, unknown ids, and prerequisite cycles."""
    answer, _, _ = recover_partition_choice(answer, candidates)
    partitions = candidates.get("partitions") or []
    by_partition = {item["id"]: item for item in partitions}
    selected = by_partition.get((answer or {}).get("candidate_partition_id")) or (partitions[0] if partitions else None)
    if selected is None:
        return [], {"fallback_used": True, "reason": "no_candidate_partition"}
    group_map = {group["group_id"]: group for group in selected["groups"]}
    card_map = {card["key"]: card for card in cards}
    raw_chapters = (answer or {}).get("chapters") or []
    out: list[dict] = []
    used: set[str] = set()
    unknown_groups = 0
    duplicate_groups = 0
    for raw in raw_chapters:
        group_ids = []
        for group_id in raw.get("group_ids") or []:
            if group_id not in group_map:
                unknown_groups += 1
            elif group_id in used:
                duplicate_groups += 1
            else:
                used.add(group_id)
                group_ids.append(group_id)
        if not group_ids:
            continue
        group_rows = [group_map[group_id] for group_id in group_ids]
        page_keys = [page["key"] for group in group_rows for page in group["pages"] if page["key"] in card_map]
        page_keys = list(dict.fromkeys(page_keys))
        if not page_keys:
            continue
        reference_only = all(card_map[key].get("kind") == "reference" for key in page_keys)
        kind = "appendix" if raw.get("kind") == "appendix" or reference_only else "tutorial"
        if kind == "tutorial":
            # A reference group cannot be hidden inside a tutorial, even if the model tried to merge it.
            reference_keys = [key for key in page_keys if card_map[key].get("kind") == "reference"]
            page_keys = [key for key in page_keys if key not in set(reference_keys)]
            if reference_keys:
                ref_title = "API Reference"
                out.append({
                    "title": ref_title,
                    "description": "Reference pages for APIs and options used by this framework.",
                    "objective": "Look up detailed API behavior when needed.",
                    "key_concepts": [], "member_doc_keys": reference_keys,
                    "prerequisites": [], "kind": "appendix", "role": "appendix",
                    "capstone_ability": False, "capstone_reason": "Reference lookup does not form a tutorial application.",
                    "synth_eligible": False,
                    "synth_exclusion_reason": "Detailed API reference is kept for lookup and does not add a learning objective to the tutorial.",
                    "n_best_code": 0, "reading_minutes": 0.0,
                    "tutorial_minutes": 0.0,
                })
            if not page_keys:
                continue
        synth_exclusion_reason = " ".join(str(raw.get("synth_exclusion_reason") or "").split())[:300]
        if reference_only:
            synth_eligible = False
            synth_exclusion_reason = synth_exclusion_reason or (
                "Reference-only pages remain available for lookup and do not add a tutorial learning objective."
            )
        else:
            synth_eligible = bool(raw.get("synth_eligible", True))
        if synth_eligible:
            synth_exclusion_reason = ""
        elif not synth_exclusion_reason:
            synth_eligible = True
        out.append({
            "title": (
                _reference_title(raw.get("title") or "Related Topics")[:120]
                if kind == "appendix"
                else " ".join(str(raw.get("title") or "Related Topics").split())[:120]
            ),
            "description": " ".join(str(raw.get("description") or "").split())[:400],
            "objective": " ".join(str(raw.get("objective") or "").split())[:300],
            "group_ids": group_ids,
            "key_concepts": list(raw.get("key_concepts") or [])[:16],
            "member_doc_keys": page_keys,
            "prerequisites": list(raw.get("prerequisites") or []),
            "kind": kind,
            "role": "appendix" if kind == "appendix" else "tutorial",
            "capstone_ability": bool(raw.get("capstone_ability")),
            "capstone_reason": " ".join(str(raw.get("capstone_reason") or "").split())[:300],
            "synth_eligible": synth_eligible,
            "synth_exclusion_reason": "" if synth_eligible else synth_exclusion_reason,
            "n_best_code": sum(int(card_map[key].get("n_best_code") or 0) for key in page_keys),
            "reading_minutes": round(sum(float(card_map[key].get("reading_minutes") or 0.0) for key in page_keys), 1),
            "tutorial_minutes": 0.0 if kind == "appendix" else round(
                float(candidates.get("chapter_overhead_minutes") or 0.0)
                + sum(float(card_map[key].get("tutorial_example_minutes") or 0.0) for key in page_keys),
                1,
            ),
        })
    missing_groups = [group_id for group_id in group_map if group_id not in used]
    fallback_groups = 0
    for group_id in missing_groups:
        group = group_map[group_id]
        page_keys = [page["key"] for page in group["pages"] if page["key"] in card_map]
        if not page_keys:
            continue
        first = card_map[page_keys[0]]
        is_reference = group.get("kind") == "reference"
        title = " ".join(str(first.get("topic") or first.get("title") or "Related Topics").split())
        out.append({
            "title": _reference_title(title)[:120] if is_reference else title[:100],
            "description": "Reference pages grouped by their source documentation section." if is_reference else "Related source pages retained after an incomplete chapter assignment.",
            "objective": "Find detailed API behavior when needed." if is_reference else f"Understand {title}.",
            "group_ids": [group_id],
            "key_concepts": list(first.get("teaches") or [])[:12],
            "member_doc_keys": page_keys,
            "prerequisites": [],
            "kind": "appendix" if is_reference else "tutorial",
            "role": "appendix" if is_reference else "gap",
            "capstone_ability": False,
            "capstone_reason": (
                "Reference material is for lookup."
                if is_reference
                else "The chapter designer did not return a valid assignment, so example compatibility is unassessed."
            ),
            "synth_eligible": not is_reference,
            "synth_exclusion_reason": (
                "Detailed API reference is kept for lookup and does not add a learning objective to the tutorial."
                if is_reference else ""
            ),
            "n_best_code": sum(int(card_map[key].get("n_best_code") or 0) for key in page_keys),
            "reading_minutes": round(sum(float(card_map[key].get("reading_minutes") or 0.0) for key in page_keys), 1),
            "tutorial_minutes": 0.0 if is_reference else round(
                float(candidates.get("chapter_overhead_minutes") or 0.0)
                + sum(float(card_map[key].get("tutorial_example_minutes") or 0.0) for key in page_keys),
                1,
            ),
        })
        fallback_groups += 1

    # Never let a Synth exclusion remove unique best code or a distinct
    # source-card teaching point from the learning path. Old or invalid model
    # answers also fail open to synthesis.
    eligible_code_ids = {
        fact.get("hash")
        for chapter in out
        if chapter.get("synth_eligible", True)
        for group_id in chapter.get("group_ids") or []
        for fact in group_map.get(group_id, {}).get("best_code") or []
        if fact.get("hash")
    }
    eligible_concepts: set[str] = set()
    for chapter in out:
        if chapter.get("synth_eligible", True):
            eligible_concepts.update(_chapter_teaching_concepts(chapter, card_map))
    synth_exclusion_overrides = 0
    synth_concept_exclusion_overrides = 0
    for chapter in out:
        if chapter.get("synth_eligible", True):
            continue
        chapter_code_ids = {
            fact.get("hash")
            for group_id in chapter.get("group_ids") or []
            for fact in group_map.get(group_id, {}).get("best_code") or []
            if fact.get("hash")
        }
        chapter_concepts = _chapter_teaching_concepts(chapter, card_map)
        missing_concepts = chapter_concepts - eligible_concepts
        if chapter_code_ids - eligible_code_ids or missing_concepts:
            chapter["synth_eligible"] = True
            chapter["synth_exclusion_reason"] = ""
            if chapter_code_ids - eligible_code_ids:
                synth_exclusion_overrides += 1
            if missing_concepts:
                synth_concept_exclusion_overrides += 1
            eligible_code_ids.update(chapter_code_ids)
            eligible_concepts.update(chapter_concepts)

    # Track tutorial groups that cannot form a single application, but keep their
    # instructional content as tutorials; capstone fit is separate from content kind.
    incapable = []
    for chapter in out:
        if chapter.get("kind") == "tutorial" and chapter.get("n_best_code", 0) and not chapter.get("capstone_ability"):
            incapable.append(chapter["title"])

    # Keep only resolvable prerequisite edges and drop the minimum set needed to make them acyclic.
    title_index: dict[str, int] = {}
    for index, chapter in enumerate(out):
        title_index.setdefault(chapter["title"].casefold(), index)
    edges: dict[int, set[int]] = defaultdict(set)
    cycle_drops = []
    for index, chapter in enumerate(out):
        accepted = []
        for name in chapter.get("prerequisites") or []:
            dep = title_index.get(str(name).casefold())
            if dep is None or dep == index or _reachable(index, dep, edges):
                cycle_drops.append({"chapter": chapter["title"], "prerequisite": name})
                continue
            edges[dep].add(index)
            accepted.append(out[dep]["title"])
        chapter["prerequisites"] = accepted

    # Ensure titles and page membership are unique before the deterministic selector sees the proposal set.
    seen_titles: Counter[str] = Counter()
    seen_pages: set[str] = set()
    duplicate_titles = 0
    for chapter in out:
        key = chapter["title"].casefold()
        seen_titles[key] += 1
        if seen_titles[key] > 1:
            duplicate_titles += 1
            chapter["title"] = f"{chapter['title']} ({seen_titles[key]})"
        unique_keys = []
        for key in chapter.get("member_doc_keys") or []:
            if key not in seen_pages:
                seen_pages.add(key)
                unique_keys.append(key)
        chapter["member_doc_keys"] = unique_keys
    out = [chapter for chapter in out if chapter.get("member_doc_keys")]
    stats = {
        "candidate_partition_id": selected["id"],
        "candidate_score": selected["metrics"]["score"],
        "n_candidate_groups": len(group_map),
        "n_missing_groups_repaired": fallback_groups,
        "n_unknown_groups": unknown_groups,
        "n_duplicate_groups": duplicate_groups,
        "n_duplicate_titles_repaired": duplicate_titles,
        "n_prerequisite_cycle_edges_dropped": len(cycle_drops),
        "prerequisite_cycle_repairs": cycle_drops,
        "capstone_incapable_groups": incapable,
        "n_synth_exclusion_overrides": synth_exclusion_overrides,
        "n_synth_concept_exclusion_overrides": synth_concept_exclusion_overrides,
        "n_synth_eligible": sum(bool(chapter.get("synth_eligible", True)) for chapter in out),
        "n_synth_excluded": sum(not bool(chapter.get("synth_eligible", True)) for chapter in out),
        "fallback_used": bool(
            fallback_groups or unknown_groups or duplicate_groups or duplicate_titles or cycle_drops
            or synth_exclusion_overrides or synth_concept_exclusion_overrides
        ),
        "n_pages_assigned": len(seen_pages),
        "n_chapters": len(out),
    }
    return out, stats
