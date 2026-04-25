#!/usr/bin/env python3
"""
Tier 3 #21 — ChapterOutput audit + assembler unit tests.

Exercises `_audit_structured_output_refs`, `_assemble_chapter_markdown`,
and `_format_structured_output_feedback` in isolation — no LLM, no MinIO.
Runs INSIDE the fastapi pod (same pattern as test_code_vault.py).

Usage:
  kubectl cp scripts/test_structured_output.py <ns>/<pod>:/tmp/ \
      -c coelhonexus-fastapi-container
  kubectl exec -n <ns> <pod> -c coelhonexus-fastapi-container \
      -- /app/.venv/bin/python /tmp/test_structured_output.py

Exit 0 on all pass, 1 on any failure.
"""
import sys
import traceback

sys.path.insert(0, "/app")

from graphs.knowledge.helpers import (
    _assemble_chapter_markdown,
    _audit_structured_output_refs,
    _format_structured_output_feedback,
    _vault_bare_hashes,
    _vault_code_blocks,
)
from schemas.knowledge.agents import ChapterOutput, Flashcard, Section


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


def _build_output(sections: list[Section]) -> ChapterOutput:
    """Shortcut — ChapterOutput requires ≥8 flashcards; build defaults."""
    return ChapterOutput(
        sections = sections,
        challenges = "1. What does X do?\n2. How does Y work?\n3. Z?",
        flashcards = [
            Flashcard(front = f"Q{i}", back = f"A{i}") for i in range(1, 9)
        ],
    )


@run("audit: empty vault, no refs → all empty")
def t_empty_vault():
    output = _build_output([
        Section(heading = "Intro", prose_md = "Hello world.", code_refs = []),
    ])
    result = _audit_structured_output_refs(output, {})
    # OP-23 (2026-04-24): audit returns 6-tuple (missing, invented,
    # fence_sections, duplicated_refs, empty_sections, thin_sections).
    assert len(result) == 6
    for lst in result:
        assert lst == []


@run("audit: vault hash referenced once → pass")
def t_all_referenced():
    src = "# Heading\n\n```python\nprint('hi')\n```\n"
    _, vault = _vault_code_blocks(src)
    bare_hash = next(iter(_vault_bare_hashes(vault)))
    output = _build_output([
        Section(heading = "Intro", prose_md = "Call print.", code_refs = [bare_hash]),
    ])
    missing, invented, fence_sections, duplicated_refs, empty_sections, thin_sections = (
        _audit_structured_output_refs(output, vault)
    )
    assert missing == []
    assert invented == []
    assert fence_sections == []
    assert duplicated_refs == []
    assert empty_sections == []


@run("audit: vault hash NOT referenced → missing flagged")
def t_missing_ref():
    src = "```python\nprint('hi')\n```\n"
    _, vault = _vault_code_blocks(src)
    bare_hash = next(iter(_vault_bare_hashes(vault)))
    output = _build_output([
        Section(heading = "Intro", prose_md = "No code here.", code_refs = []),
    ])
    missing, invented, fence_sections, duplicated_refs, empty_sections, thin_sections = (
        _audit_structured_output_refs(output, vault)
    )
    assert missing == [bare_hash], f"expected [{bare_hash!r}], got {missing}"
    assert invented == []


@run("audit: invented hash → flagged as invented")
def t_invented_ref():
    src = "```py\nx = 1\n```\n"
    _, vault = _vault_code_blocks(src)
    real_hash = next(iter(_vault_bare_hashes(vault)))
    fake_hash = "deadbeefcafe"
    output = _build_output([
        Section(
            heading = "Intro",
            prose_md = "Real + fake refs.",
            code_refs = [real_hash, fake_hash],
        ),
    ])
    missing, invented, _, _, _, _ = _audit_structured_output_refs(output, vault)
    assert missing == []
    assert invented == [fake_hash], f"expected [{fake_hash!r}], got {invented}"


@run("audit: ``` in prose_md → fence_section flagged")
def t_fence_in_prose():
    output = _build_output([
        Section(
            heading = "Bad",
            prose_md = "Here is code: ```python\nx = 1\n```\nend.",
            code_refs = [],
        ),
        Section(
            heading = "Good",
            prose_md = "Pure prose.",
            code_refs = [],
        ),
    ])
    _, _, fence_sections, _, _, _ = _audit_structured_output_refs(output, {})
    assert fence_sections == ["Bad"], f"got {fence_sections}"


@run("audit: multi-section refs union counts correctly")
def t_union_across_sections():
    src = (
        "```py\na = 1\n```\n\n"
        "```js\nconst b = 2;\n```\n\n"
        "```rust\nlet c = 3;\n```\n"
    )
    _, vault = _vault_code_blocks(src)
    hashes = sorted(_vault_bare_hashes(vault))
    assert len(hashes) == 3
    output = _build_output([
        Section(heading = "Py", prose_md = ".", code_refs = [hashes[0]]),
        Section(heading = "JS", prose_md = ".", code_refs = [hashes[1]]),
        # ch_refs misses the third hash → audit flags missing
    ])
    missing, invented, _, _, _, _ = _audit_structured_output_refs(output, vault)
    assert missing == [hashes[2]], f"expected [{hashes[2]!r}], got {missing}"
    assert invented == []


@run("assemble: empty vault, prose only")
def t_assemble_prose_only():
    output = _build_output([
        Section(heading = "Intro", prose_md = "Hello.", code_refs = []),
    ])
    md = _assemble_chapter_markdown(output, {}, chapter_title = "Test Chapter")
    assert md.startswith("# Test Chapter")
    assert "## Intro" in md
    assert "Hello." in md
    # No code anywhere
    assert "```" not in md


@run("assemble: one code_ref → fence emitted from vault verbatim")
def t_assemble_single_code_ref():
    src = "```python\nprint('hi')\n```\n"
    _, vault = _vault_code_blocks(src)
    bare_hash = next(iter(_vault_bare_hashes(vault)))
    original_fence = next(iter(vault.values()))
    output = _build_output([
        Section(heading = "Say Hi", prose_md = "Greet.", code_refs = [bare_hash]),
    ])
    md = _assemble_chapter_markdown(output, vault)
    assert "## Say Hi" in md
    assert "Greet." in md
    assert original_fence in md, f"expected verbatim fence in md; got:\n{md}"


@run("assemble: multiple refs in order")
def t_assemble_order_preserved():
    src = "```py\nA\n```\n\n```py\nB\n```\n"
    _, vault = _vault_code_blocks(src)
    hashes = sorted(_vault_bare_hashes(vault))
    fences = [v for k, v in sorted(vault.items(), key=lambda kv: hashes.index(
        kv[0].split('"')[1]
    ))]
    output = _build_output([
        Section(heading = "Both", prose_md = "prose.", code_refs = hashes),
    ])
    md = _assemble_chapter_markdown(output, vault)
    # Both fences present and in the order we asked for
    idx_a = md.find(fences[0])
    idx_b = md.find(fences[1])
    assert idx_a != -1 and idx_b != -1
    assert idx_a < idx_b, f"expected fence[0] before fence[1]; a={idx_a} b={idx_b}"


@run("assemble: invented ref is silently skipped")
def t_assemble_skips_invented():
    src = "```py\nkeep\n```\n"
    _, vault = _vault_code_blocks(src)
    real = next(iter(_vault_bare_hashes(vault)))
    output = _build_output([
        Section(
            heading = "Mix",
            prose_md = "Real + fake.",
            code_refs = [real, "deadbeef1234"],
        ),
    ])
    md = _assemble_chapter_markdown(output, vault)
    # real fence shows up; fake hash silently skipped (audit would've caught)
    real_fence = next(iter(vault.values()))
    assert real_fence in md
    assert "deadbeef1234" not in md


@run("feedback: missing + invented + fence_sections all rendered")
def t_feedback_render():
    src = "```py\nkeep\n```\n"
    _, vault = _vault_code_blocks(src)
    real_hash = next(iter(_vault_bare_hashes(vault)))
    fb = _format_structured_output_feedback(
        missing = [real_hash],
        invented = ["deadbeef1234"],
        fence_sections = ["Bad Section"],
        vault = vault,
    )
    assert "STRUCTURED OUTPUT FAILURE" in fb
    assert real_hash in fb
    assert "deadbeef1234" in fb
    assert "Bad Section" in fb


@run("round-trip: vault → ChapterOutput → assemble reconstructs faithful markdown")
def t_round_trip():
    src = (
        "# Chapter Title\n\n"
        "## Section 1\n\n"
        "prose intro\n\n"
        "```python\nprint(1)\n```\n\n"
        "more prose\n\n"
        "```python\nprint(2)\n```\n"
    )
    _, vault = _vault_code_blocks(src)
    hashes = sorted(_vault_bare_hashes(vault))
    output = _build_output([
        Section(
            heading = "Section 1",
            prose_md = "prose intro\n\nmore prose",
            code_refs = hashes,
        ),
    ])
    # Audit passes
    missing, invented, fence_sections, duplicated, empty, thin = (
        _audit_structured_output_refs(output, vault)
    )
    assert missing == [] and invented == [] and fence_sections == []
    assert duplicated == [] and empty == []
    assert thin == []
    # Assembly produces valid markdown containing both code blocks
    md = _assemble_chapter_markdown(output, vault, chapter_title = "Chapter Title")
    assert "# Chapter Title" in md
    assert "## Section 1" in md
    for fence in vault.values():
        assert fence in md


# -----------------------------------------------------------------------------
# Batch-3 (2026-04-23): distribution fixes
# -----------------------------------------------------------------------------


@run("audit: hash in TWO sections' code_refs → duplicated_refs flagged")
def t_duplicated_refs():
    src = "```py\nx = 1\n```\n\n```py\ny = 2\n```\n"
    _, vault = _vault_code_blocks(src)
    hashes = sorted(_vault_bare_hashes(vault))
    output = _build_output([
        Section(heading = "A", prose_md = "first.", code_refs = [hashes[0]]),
        Section(heading = "B", prose_md = "second.", code_refs = [hashes[0], hashes[1]]),
        # hashes[0] in both A and B → duplicated
    ])
    missing, invented, _, duplicated, _, _ = _audit_structured_output_refs(output, vault)
    assert missing == []
    assert invented == []
    assert duplicated == [hashes[0]], f"expected [{hashes[0]!r}], got {duplicated}"


@run("audit: section with substantive prose + 0 code_refs → empty_sections flagged")
def t_empty_section_with_prose():
    src = "```py\nkeep\n```\n"
    _, vault = _vault_code_blocks(src)
    bare = next(iter(_vault_bare_hashes(vault)))
    output = _build_output([
        Section(heading = "Intro", prose_md = "Use it.", code_refs = [bare]),
        Section(
            heading = "Filler",
            prose_md = "This section has substantial prose but no code. " * 3,
            code_refs = [],
        ),
    ])
    _, _, _, _, empty, _ = _audit_structured_output_refs(output, vault)
    assert empty == ["Filler"], f"got {empty}"


@run("audit: transitional section (≤40 chars prose, no code) → NOT flagged")
def t_transitional_section_ok():
    src = "```py\nx = 1\n```\n"
    _, vault = _vault_code_blocks(src)
    bare = next(iter(_vault_bare_hashes(vault)))
    output = _build_output([
        Section(heading = "A", prose_md = "See next section.", code_refs = []),
        Section(heading = "B", prose_md = "Use it.", code_refs = [bare]),
    ])
    _, _, _, _, empty, _ = _audit_structured_output_refs(output, vault)
    assert empty == [], f"transitional should not flag; got {empty}"


@run("assembler: duplicated ref emitted at most once (defense)")
def t_assembler_dedup_duplicate_ref():
    src = "```py\nunique\n```\n"
    _, vault = _vault_code_blocks(src)
    bare = next(iter(_vault_bare_hashes(vault)))
    original = next(iter(vault.values()))
    # Bad output: same hash in TWO sections (audit would flag this; we want
    # to confirm the assembler still produces non-duplicated markdown)
    output = _build_output([
        Section(heading = "A", prose_md = ".", code_refs = [bare]),
        Section(heading = "B", prose_md = ".", code_refs = [bare]),
    ])
    md = _assemble_chapter_markdown(output, vault)
    assert md.count(original) == 1, \
        f"expected fence emitted 1 time; got {md.count(original)}"


@run("feedback: duplicated_refs + empty_sections rendered")
def t_feedback_batch3_signals():
    from graphs.knowledge.helpers import _format_structured_output_feedback
    fb = _format_structured_output_feedback(
        missing = [],
        invented = [],
        fence_sections = [],
        vault = {'<code-ref hash="abc123def456"/>': "```py\nx\n```"},
        duplicated_refs = ["abc123def456"],
        empty_sections = ["Filler"],
    )
    assert "duplicated" in fb.lower() or "EXACTLY ONE" in fb
    assert "abc123def456" in fb
    assert "Filler" in fb


print()
print(f"Passed: {PASSED}    Failed: {len(FAILED)}")
if FAILED:
    print("Failed tests:")
    for name in FAILED:
        print(f"  - {name}")
    sys.exit(1)
print("All tests passed.")
sys.exit(0)
