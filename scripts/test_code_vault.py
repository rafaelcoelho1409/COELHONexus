#!/usr/bin/env python3
"""
Tier 0a code-vault primitives — unit tests.

Exercises _vault_code_blocks, _restore_code_blocks, _audit_sentinel_roundtrip
from apps/fastapi/graphs/knowledge/helpers.py. Runs INSIDE the fastapi pod
(markdown-it-py is only installed in /app/.venv).

Usage:
  kubectl cp scripts/test_code_vault.py <ns>/<pod>:/tmp/ \
      -c coelhonexus-fastapi-container
  kubectl exec -n <ns> <pod> -c coelhonexus-fastapi-container \
      -- /app/.venv/bin/python /tmp/test_code_vault.py

Exit 0 on all pass, 1 on any failure.

Coverage:
  - identity transform (empty doc, no code)
  - basic round-trip (single fence)
  - multi-language, multi-block round-trip
  - info-string preservation (language tags + attrs)
  - tilde fences (~~~)
  - doc that is entirely a fence
  - empty fenced block
  - unicode inside code
  - identical blocks collapse to one sentinel
  - pre-existing sentinel in source -> ValueError
  - inline `code` NOT vaulted
  - audit: clean / missing / unexpected
  - nested fence (```` outer closes only at ````)
  - large document (50 blocks)
"""
import sys
import traceback

sys.path.insert(0, "/app")

from graphs.knowledge.helpers import (
    _VAULT_SENTINEL_RE,
    _audit_sentinel_roundtrip,
    _restore_code_blocks,
    _vault_code_blocks,
)


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


@run("empty document — identity transform")
def t_empty():
    vaulted, vault = _vault_code_blocks("")
    assert vaulted == ""
    assert vault == {}


@run("no fenced blocks — identity transform")
def t_no_code():
    src = "# Heading\n\nJust prose. `inline code` doesn't count.\n"
    vaulted, vault = _vault_code_blocks(src)
    assert vaulted == src, f"content mutated: {vaulted!r}"
    assert vault == {}


@run("basic round-trip — single python fence")
def t_basic():
    src = "intro\n\n```python\nprint('hi')\n```\n\nouter\n"
    vaulted, vault = _vault_code_blocks(src)
    assert len(vault) == 1
    assert "```python" not in vaulted
    assert "print('hi')" not in vaulted
    restored = _restore_code_blocks(vaulted, vault)
    assert restored == src, f"round-trip drift:\n  src={src!r}\n  got={restored!r}"


@run("multi-fence multi-language round-trip")
def t_multi_lang():
    src = (
        "# Setup\n\n```bash\ncd /tmp\n```\n\n"
        "Then:\n\n```python\nimport os\nos.chdir('/tmp')\n```\n\n"
        "Config:\n\n```yaml\nkey: value\n```\n"
    )
    vaulted, vault = _vault_code_blocks(src)
    assert len(vault) == 3, f"expected 3 blocks, got {len(vault)}"
    assert _restore_code_blocks(vaulted, vault) == src


@run("info-string / language tag preserved")
def t_lang_tag():
    src = "```python { .line-numbers data-lang='py' }\nx = 1\n```\n"
    _, vault = _vault_code_blocks(src)
    assert len(vault) == 1
    original = next(iter(vault.values()))
    assert "{ .line-numbers data-lang='py' }" in original, \
        f"info-string lost: {original!r}"


@run("tilde fences (~~~) supported")
def t_tilde_fence():
    src = "prose\n\n~~~rust\nfn main() {}\n~~~\n\nouter\n"
    vaulted, vault = _vault_code_blocks(src)
    assert len(vault) == 1, "tilde fence not vaulted"
    assert _restore_code_blocks(vaulted, vault) == src


@run("document that is entirely one fenced block")
def t_only_code():
    src = "```python\nprint(1)\nprint(2)\n```\n"
    vaulted, vault = _vault_code_blocks(src)
    assert len(vault) == 1
    assert _restore_code_blocks(vaulted, vault) == src


@run("empty fenced block")
def t_empty_block():
    src = "before\n\n```\n```\n\nafter\n"
    vaulted, vault = _vault_code_blocks(src)
    assert len(vault) == 1
    assert _restore_code_blocks(vaulted, vault) == src


@run("unicode inside code block")
def t_unicode_code():
    src = "```python\nprint('café 日本語 🐍')\n```\n"
    vaulted, vault = _vault_code_blocks(src)
    original = next(iter(vault.values()))
    assert "café" in original
    assert "日本語" in original
    assert "🐍" in original
    assert _restore_code_blocks(vaulted, vault) == src


@run("identical blocks collapse to one sentinel")
def t_dedup():
    src = "```py\nx\n```\n\n---\n\n```py\nx\n```\n"
    vaulted, vault = _vault_code_blocks(src)
    assert len(vault) == 1, f"identical blocks should collapse; got {len(vault)}"
    sentinel = next(iter(vault))
    assert vaulted.count(sentinel) == 2
    assert _restore_code_blocks(vaulted, vault) == src


@run("pre-existing sentinel in source raises ValueError")
def t_collision():
    src = 'leaked sentinel: <code-ref hash="abc123def456"/>\n'
    try:
        _vault_code_blocks(src)
    except ValueError as e:
        assert "sentinel" in str(e).lower()
        return
    raise AssertionError("expected ValueError for pre-existing sentinel")


@run("inline `code` spans are NOT vaulted")
def t_inline_code_untouched():
    src = "Call `foo()` or `bar.baz()`; no fences here.\n"
    vaulted, vault = _vault_code_blocks(src)
    assert vault == {}, "inline code must not be vaulted"
    assert vaulted == src


@run("audit: clean round-trip")
def t_audit_clean():
    src = "```py\nx = 1\n```\n"
    vaulted, vault = _vault_code_blocks(src)
    missing, unexpected = _audit_sentinel_roundtrip(vaulted, vault)
    assert missing == [] and unexpected == []


@run("audit: LLM dropped a sentinel -> missing flagged")
def t_audit_missing():
    src = "```py\nkeep\n```\n\n```py\ndrop\n```\n"
    vaulted, vault = _vault_code_blocks(src)
    sentinels = list(vault.keys())
    dropped = sentinels[-1]
    llm_output = vaulted.replace(dropped, "")
    missing, unexpected = _audit_sentinel_roundtrip(llm_output, vault)
    assert dropped in missing, f"dropped sentinel not flagged; missing={missing}"
    assert unexpected == []


@run("audit: LLM hallucinated a sentinel -> unexpected flagged")
def t_audit_unexpected():
    src = "```py\nx\n```\n"
    vaulted, vault = _vault_code_blocks(src)
    fake = '<code-ref hash="deadbeefdead"/>'
    llm_output = vaulted + "\n" + fake
    missing, unexpected = _audit_sentinel_roundtrip(llm_output, vault)
    assert missing == []
    assert fake in unexpected, f"hallucinated sentinel not flagged; unexpected={unexpected}"


@run("nested fence: 4-backtick outer closes only at 4-backtick")
def t_nested_fence():
    src = "````md\nexample:\n```py\nprint(1)\n```\nend\n````\n"
    vaulted, vault = _vault_code_blocks(src)
    assert len(vault) == 1, f"expected one outer fence, got {len(vault)}"
    original = next(iter(vault.values()))
    assert "```py" in original, "inner backticks should be part of outer content"
    assert _restore_code_blocks(vaulted, vault) == src


@run("large document: 50 prose+fence sections")
def t_large_doc():
    parts = []
    for i in range(50):
        parts.append(
            f"## Section {i}\n\n"
            f"Prose for section {i}.\n\n"
            f"```python\n# block {i}\nvalue_{i} = {i}\n```\n"
        )
    src = "\n".join(parts)
    vaulted, vault = _vault_code_blocks(src)
    assert len(vault) == 50, f"expected 50 unique blocks, got {len(vault)}"
    assert _restore_code_blocks(vaulted, vault) == src


@run("sentinels match the module regex")
def t_sentinel_shape():
    src = "```py\ny = 2\n```\n"
    _, vault = _vault_code_blocks(src)
    for sentinel in vault:
        assert _VAULT_SENTINEL_RE.fullmatch(sentinel), \
            f"sentinel doesn't match module regex: {sentinel!r}"


@run("vault is idempotent: same input -> same sentinels")
def t_idempotent():
    src = "```py\nz = 3\n```\n\n```js\nconsole.log(1)\n```\n"
    v1, vault1 = _vault_code_blocks(src)
    v2, vault2 = _vault_code_blocks(src)
    assert v1 == v2
    assert vault1 == vault2


print()
print(f"Passed: {PASSED}    Failed: {len(FAILED)}")
if FAILED:
    print("Failed tests:")
    for name in FAILED:
        print(f"  - {name}")
    sys.exit(1)
print("All tests passed.")
sys.exit(0)
