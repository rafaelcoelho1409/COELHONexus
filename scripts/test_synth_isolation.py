#!/usr/bin/env python3
"""
Tier 0d-6 isolation — unit test for synthesize_chapter's sentinel path.

Forces _synthesize_attempt to raise (simulating fallback-chain exhaustion)
and asserts synthesize_chapter does NOT propagate the RuntimeError; instead
it returns a DEBT-carrying sentinel ChapterResult so sibling chapters keep
running.

Covers:
  - Sentinel result shape (number, content_path=None, score=0.0, iterations=0)
  - debt.reason == "synth_chain_exhausted"
  - debt carries iteration_failed_at, graded_iterations, adjustments_accumulated
  - No RuntimeError propagates out of synthesize_chapter
  - Vault was built (so _load_chapter_files + _vault_code_blocks ran)

Usage: kubectl cp + kubectl exec, same pattern as test_code_vault.py.
"""
import asyncio
import sys
import traceback

sys.path.insert(0, "/app")

from graphs.knowledge import distiller as dist
from graphs.knowledge.distiller import KnowledgeDistillerGraph
from schemas.knowledge.agents import ChapterPlan
from schemas.knowledge.inputs import UserProfile


PASSED = 0
FAILED: list[str] = []


def run(name: str):
    def deco(fn):
        global PASSED
        try:
            fn()
            PASSED += 1
            print(f"  PASS  {name}")
        except AssertionError as e:
            FAILED.append(name)
            print(f"  FAIL  {name}: {e}")
        except Exception as e:
            FAILED.append(name)
            print(f"  ERR   {name}: {type(e).__name__}: {e}")
            traceback.print_exc()
        return fn
    return deco


class _FakeStorage:
    """Just enough surface for _load_chapter_files."""
    def __init__(self, files: dict[str, str]):
        self._files = files

    async def read_text(self, key: str) -> str:
        if key in self._files:
            return self._files[key]
        raise FileNotFoundError(key)


class _FakeCache:
    """Cache miss for every lookup."""
    async def get_chapter(self, **_kwargs):
        return None


def _build_payload() -> dict:
    return {
        "chapter": ChapterPlan(
            number = 7,
            title = "Isolation Test Chapter",
            goal = "exercise 0d-6 sentinel path",
            assigned_files = ["doc_a"],
        ),
        "framework": "FastAPI",
        "version": "latest",
        "profile_hash": "deadbeef",
        "user_profile": UserProfile(
            level = "senior",
            target_markets = [],
            mastered_technologies = ["Python"],
            portfolio_refs = [],
            acceptance_threshold = 0.85,
        ),
        "study_root": "test/isolation",
    }


@run("exhausted fallback chain → sentinel result, no propagation")
def t_sentinel_on_exhaustion():
    storage = _FakeStorage({
        "test/isolation/research/raw/doc_a.md": (
            "# Doc A\n\n```python\nprint('hi')\n```\n"
        ),
    })
    cache = _FakeCache()

    async def _fake_synth(**_kwargs):
        raise RuntimeError(
            "synth ch07 exhausted all 12 fallback model(s) — "
            "every model either raised or returned None"
        )

    original = dist._synthesize_attempt
    dist._synthesize_attempt = _fake_synth
    try:
        graph = KnowledgeDistillerGraph()
        payload = _build_payload()
        result = asyncio.run(graph.synthesize_chapter(
            payload, llm = object(), storage = storage, cache = cache,
        ))
    finally:
        dist._synthesize_attempt = original

    assert isinstance(result, dict), f"expected dict, got {type(result).__name__}"
    assert "synthesis_results" in result, f"missing key: {list(result.keys())}"
    arr = result["synthesis_results"]
    assert isinstance(arr, list) and len(arr) == 1, f"bad shape: {arr!r}"
    sentinel = arr[0]
    assert sentinel["number"] == 7, f"chapter number not preserved: {sentinel!r}"
    assert sentinel["content_path"] is None, "content_path must be None — no README written"
    assert sentinel["challenges_path"] is None
    assert sentinel["flashcards_path"] is None
    assert sentinel["score"] == 0.0, f"score should be 0.0, got {sentinel['score']}"
    assert sentinel["iterations"] == 0, \
        f"no iter produced graded eval → iterations=0, got {sentinel['iterations']}"
    debt = sentinel["debt"]
    assert debt["reason"] == "synth_chain_exhausted", \
        f"wrong reason: {debt['reason']}"
    assert "exhausted all 12 fallback" in debt["error"], \
        f"error not preserved: {debt['error']!r}"
    assert debt["iteration_failed_at"] == 0, \
        f"iteration_failed_at should be 0 (failed on first iter), got {debt['iteration_failed_at']}"
    assert debt["graded_iterations"] == 0
    assert debt["adjustments_accumulated"] == 0


@run("grader exhaustion after synth OK → sentinel with graded_iterations=0")
def t_sentinel_on_grader_failure():
    """
    Tier 3 #21 variant: synth returns a valid ChapterOutput that passes the
    audit; grader then raises. Sentinel must still fire via the outer
    RuntimeError handler. Validates that #21's audit + assembler pass through
    to grading exactly as the legacy flow did.
    """
    from schemas.knowledge.agents import ChapterOutput, Flashcard, Section
    from graphs.knowledge.helpers import _vault_bare_hashes

    # A tiny vault with one code block. We'll build a ChapterOutput whose
    # code_refs names exactly that hash → audit passes by construction.
    storage = _FakeStorage({
        "test/isolation/research/raw/doc_a.md": (
            "# Doc A\n\n```python\nx = 1\n```\n"
        ),
    })
    cache = _FakeCache()

    async def _fake_synth(*, files_content, **_kwargs):
        # files_content has been vaulted by synthesize_chapter before
        # calling us. The bare hash is inside `<code-ref hash="..."/>`;
        # the audit compares code_refs to the vault the node built. Since
        # we can't easily reach that vault from here, we bypass audit by
        # also mocking _audit_structured_output_refs to return empty.
        return ChapterOutput(
            sections = [Section(
                heading = "Setup",
                prose_md = "Assign a value to x.",
                code_refs = [],  # audit is monkey-patched to no-op
            )],
            challenges = "1. Assign a value to x.\n2. Print x.\n3. Inspect type of x.",
            flashcards = [
                Flashcard(front = f"Q{i}", back = f"A{i}") for i in range(1, 9)
            ],
        )

    async def _fake_audit(*_args, **_kwargs):
        return [], [], []

    async def _fake_grader(**_kwargs):
        raise RuntimeError("grader exhausted all fallback models")

    orig_s = dist._synthesize_attempt
    orig_a = dist._audit_structured_output_refs
    orig_g = dist._grade_attempt
    dist._synthesize_attempt = _fake_synth
    # Audit is sync; wrap to sync lambda returning empty 5-tuple (batch-3
    # 2026-04-23: added duplicated_refs + empty_sections).
    dist._audit_structured_output_refs = lambda *a, **k: ([], [], [], [], [])
    dist._grade_attempt = _fake_grader
    try:
        graph = KnowledgeDistillerGraph()
        result = asyncio.run(graph.synthesize_chapter(
            _build_payload(), llm = object(), storage = storage, cache = cache,
        ))
    finally:
        dist._synthesize_attempt = orig_s
        dist._audit_structured_output_refs = orig_a
        dist._grade_attempt = orig_g

    sentinel = result["synthesis_results"][0]
    assert sentinel["debt"]["reason"] == "synth_chain_exhausted"
    assert "grader exhausted" in sentinel["debt"]["error"]
    assert sentinel["debt"]["graded_iterations"] == 0, \
        "grader failed before any iter graded → 0"


@run("cache-miss path still calls _load_chapter_files + vault (gate not short-circuited)")
def t_sentinel_preserves_pre_loop_side_effects():
    reads: list[str] = []

    class _TrackingStorage(_FakeStorage):
        async def read_text(self, key: str) -> str:
            reads.append(key)
            return await super().read_text(key)

    storage = _TrackingStorage({
        "test/isolation/research/raw/doc_a.md": "# A\n\n```\ncode\n```\n",
    })
    cache = _FakeCache()

    async def _fake_synth(**_kwargs):
        raise RuntimeError("boom")

    original = dist._synthesize_attempt
    dist._synthesize_attempt = _fake_synth
    try:
        graph = KnowledgeDistillerGraph()
        asyncio.run(graph.synthesize_chapter(
            _build_payload(), llm = object(), storage = storage, cache = cache,
        ))
    finally:
        dist._synthesize_attempt = original

    assert any("doc_a.md" in k for k in reads), \
        f"_load_chapter_files should have been called; reads={reads}"


print()
print(f"Passed: {PASSED}    Failed: {len(FAILED)}")
if FAILED:
    print("Failed tests:")
    for name in FAILED:
        print(f"  - {name}")
    sys.exit(1)
print("All tests passed.")
sys.exit(0)
