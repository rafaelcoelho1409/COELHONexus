"""Offline check of the LLM-usage counters (`domains.settings.llm_counter`): the Redis hashes and the JSON shapes the
Pipeline / Ask usage drawers render, for the `dd` and `ycs` namespaces.

    cd apps/server && .venv/bin/python ../../scripts/server/test_llm_counters.py

An in-memory Redis stands in for the cluster (`infra.redis.service.new_redis` is patched), so the test pins the exact
key strings, hash fields and read shapes — the contract the live drawers, the DD snapshots and in-flight counters depend on."""
import asyncio
import os
import sys
import uuid
from pathlib import Path

for name, value in {"REDIS_HOST": "localhost", "REDIS_PORT": "6379", "REDIS_PASSWORD": "x", "ENVIRONMENT": "local"}.items():
    os.environ.setdefault(name, value)
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "apps" / "server"))

from langchain_core.messages import AIMessage  # noqa: E402
from langchain_core.outputs import ChatGeneration, LLMResult  # noqa: E402
from pydantic import BaseModel  # noqa: E402

import domains, infra  # noqa: E402


# ---- in-memory Redis ------------------------------------------------------------------------------------------------
STORE: dict[str, dict[str, int | str]] = {}
TTLS: dict[str, int] = {}


class FakePipe:
    def __init__(self):
        self.ops = []

    def hset(self, key, field, value):
        self.ops.append(("hset", key, field, value))

    def hincrby(self, key, field, amount):
        self.ops.append(("hincrby", key, field, amount))

    def expire(self, key, seconds):
        self.ops.append(("expire", key, seconds))

    async def execute(self):
        for op, key, a, *rest in self.ops:
            if op == "hset":
                STORE.setdefault(key, {})[a] = rest[0]
            elif op == "hincrby":
                h = STORE.setdefault(key, {})
                h[a] = int(h.get(a, 0)) + rest[0]
            else:
                TTLS[key] = a
        self.ops = []


class FakeRedis:
    def pipeline(self, transaction=False):
        return FakePipe()

    async def hgetall(self, key):
        return {k.encode(): str(v).encode() for k, v in STORE.get(key, {}).items()}

    async def aclose(self):
        pass


infra.redis.service.new_redis = lambda *a, **k: FakeRedis()


class FakeStorage:
    def __init__(self, blob=None):
        self.blob, self.written = blob, {}

    async def read_text(self, key):
        if self.blob is None:
            raise KeyError(key)
        return self.blob

    async def write(self, key, content, content_type=None):
        self.written[key] = content


def ok(label, cond, extra=""):
    assert cond, f"FAIL {label} {extra}"
    print(f"ok  {label}")


lc = domains.settings.llm_counter
LC = lc.service
Usage = lc.entities.Usage
RAW = {"usage": {"prompt_tokens": 4, "completion_tokens": 6, "completion_tokens_details": {"reasoning_tokens": 2}}, "model": "m"}
AI = AIMessage(content="hello", usage_metadata={"input_tokens": 10, "output_tokens": 5, "total_tokens": 15})
RESULT = LLMResult(generations=[[ChatGeneration(message=AI)]], llm_output={"model_name": "nvidia/model-a"})
ZERO = {"calls": 0, "tokens_in": 0, "tokens_out": 0, "reasoning_tokens": 0}


async def run():
    # ---------------- keys: the strings live counters and DD snapshots already use ----------------
    ok("keys: dd + ycs counters / models / snapshot strings unchanged", (
        lc.keys.counters_key("dd", "th") == "coelhonexus:dd:th:llm:counters"
        and lc.keys.models_key("dd", "th", "n") == "coelhonexus:dd:th:llm:models:n"
        and lc.keys.snapshot_key("dd", "a/b") == "observability/dd/llm-counters/a%2Fb.json"
        and lc.keys.counters_key("ycs", "ex") == "coelhonexus:ycs:ex:llm:counters"
        and lc.keys.models_key("ycs", "ex", "v") == "coelhonexus:ycs:ex:llm:models:v"))

    # ---------------- DD ----------------
    await LC.bump_response(RAW, deployment="x")
    ok("dd: no context -> no-op", not STORE)
    with LC.llm_context(ns="dd", run_id="th-1", node_id="node_a", stage="planner"):
        await LC.bump_response(RAW, deployment="prov/m")
        await LC.bump_response(AI, deployment="prov/m")      # what the chat adapter passes: the bare AIMessage
        await LC.bump_current_failure(Exception("Error code: 504 gateway"))
        await LC.bump_current_failure(RuntimeError("429 rate limited"))
    with LC.llm_context(ns="dd", run_id="th-1", node_id="node_b", stage="planner"):
        await LC.bump_current_failure(domains.settings.chat.errors.ChatTimeoutError("hard backstop fired"))
    ok("dd: context restored after the block", LC.get_context() is None)
    with LC.llm_context(ns="dd", run_id="", node_id="n", stage="planner"):
        await LC.bump_response(RAW, deployment="prov/m")
    ok("dd: empty run id -> no-op", "coelhonexus:dd::llm:counters" not in STORE)
    hash_ = STORE["coelhonexus:dd:th-1:llm:counters"]
    ok("dd: hash fields written", hash_ == {
        "stage": "planner",
        "node:node_a:calls": 2, "node:node_a:tokens_in": 14, "node:node_a:tokens_out": 11, "node:node_a:reasoning_tokens": 2,
        "total:calls": 2, "total:tokens_in": 14, "total:tokens_out": 11, "total:reasoning_tokens": 2,
        "node:node_a:errors": 2, "total:errors": 3, "errkind:504": 1, "errkind:rate_limit": 1,
        "node:node_b:errors": 1, "errkind:timeout": 1,
    }, str(hash_))
    ok("dd: TTL applied", TTLS["coelhonexus:dd:th-1:llm:counters"] == lc.params.COUNTER_TTL_S)
    got = await LC.read_counters("dd", "th-1")
    ok("dd: read shape", got == {
        "thread_id": "th-1", "stage": "planner",
        "total": {"calls": 2, "tokens_in": 14, "tokens_out": 11, "reasoning_tokens": 2, "errors": 3},
        "error_kinds": {"504": 1, "rate_limit": 1, "timeout": 1},
        "by_node": {
            "node_a": {"calls": 2, "tokens_in": 14, "tokens_out": 11, "reasoning_tokens": 2, "errors": 2,
                       "by_model": {"prov/m": {"calls": 2, "tokens_in": 14, "tokens_out": 11, "reasoning_tokens": 2}}},
            "node_b": {"calls": 0, "tokens_in": 0, "tokens_out": 0, "reasoning_tokens": 0, "errors": 1, "by_model": {}},
        },
    }, str(got))
    storage = FakeStorage()
    infra.minio.service.get_storage = lambda: storage
    ok("dd: snapshot persists the read payload", await LC.snapshot("dd", "th-1") is True
       and next(iter(storage.written)) == "observability/dd/llm-counters/th-1.json")
    ok("dd: snapshot skipped with zero calls", await LC.snapshot("dd", "th-empty") is False)
    ok("dd: ycs has no snapshot", await LC.snapshot("ycs", "th-1") is False)
    infra.minio.service.get_storage = lambda: FakeStorage('{"thread_id": "th-old", "total": {"calls": 9}}')
    ok("dd: read falls back to the MinIO snapshot when Redis is empty", (await LC.read_counters("dd", "th-old"))["total"]["calls"] == 9)
    ok("dd: ycs never reads a snapshot", (await LC.read_counters("ycs", "th-old"))["total"]["calls"] == 0)
    infra.minio.service.get_storage = lambda: FakeStorage()
    ok("dd: read with nothing anywhere is the empty shape", (await LC.read_counters("dd", "th-none")) == {
        "thread_id": "th-none", "stage": None, "total": {**ZERO, "errors": 0}, "error_kinds": {}, "by_node": {}})

    # ---------------- YCS ----------------
    STORE.clear()
    await LC.bump_current_call(usage=Usage(None, 1, 1, 0), model="x")
    ok("ycs: no context -> no-op", not STORE)
    LC.set_context(ns="ycs", run_id="ex-1", node_id="vid-1")
    await LC.bump_current_call(usage=Usage(None, 3, 4, 1), model="m1")
    await LC.bump_current_call(usage=Usage(None, 1, 2, 0), model="m2")
    await LC.bump_current_failure(RuntimeError("504"))        # ycs does not count failures
    LC.clear_context()
    h = STORE["coelhonexus:ycs:ex-1:llm:counters"]
    ok("ycs: extract/video hash fields (no stage / errors fields)", h == {
        "node:vid-1:calls": 2, "node:vid-1:tokens_in": 4, "node:vid-1:tokens_out": 6, "node:vid-1:reasoning_tokens": 1,
        "total:calls": 2, "total:tokens_in": 4, "total:tokens_out": 6, "total:reasoning_tokens": 1,
    }, str(h))
    g = await LC.read_counters("ycs", "ex-1")
    ok("ycs: read shape", g == {
        "extract_id": "ex-1",
        "total": {"calls": 2, "tokens_in": 4, "tokens_out": 6, "reasoning_tokens": 1},
        "by_node": {"vid-1": {"calls": 2, "tokens_in": 4, "tokens_out": 6, "reasoning_tokens": 1, "by_model": {
            "m1": {"calls": 1, "tokens_in": 3, "tokens_out": 4, "reasoning_tokens": 1},
            "m2": {"calls": 1, "tokens_in": 1, "tokens_out": 2, "reasoning_tokens": 0}}}},
    }, str(g))
    LC.set_context(ns="ycs", run_id="th-ask")
    LC.set_node(node="generate")
    await LC.bump_current_call(usage=Usage(None, 5, 5, 0), model="m3")
    t = await LC.read_counters("ycs", "th-ask")
    ok("ycs: Ask thread aggregate keyed by node", t["total"]["calls"] == 1 and t["by_node"]["generate"]["by_model"]["m3"]["tokens_in"] == 5, str(t))
    delta = {**ZERO, "calls": 2, "tokens_in": 4}
    ok("diff_usage: the usage between two payloads, per model", lc.domain.diff_usage(
        {"total": {"calls": 1, "tokens_in": 5}, "by_node": {}},
        {"total": {"calls": 3, "tokens_in": 9}, "by_node": {"n": {"by_model": {"m": {"calls": 2, "tokens_in": 4}}}}},
    ) == {"total": delta, "by_model": {"m": delta}})
    ok("ycs: empty id -> empty shape", (await LC.read_counters("ycs", "")) == {"extract_id": "", "total": ZERO, "by_node": {}})
    LC.set_context(ns="ycs", run_id="th-u")
    await LC.bump_current_call(usage=Usage(None, 1, 1, 0), model="mu")
    ok("ycs: a call before any node tags itself counts under 'unknown'", "unknown" in (await LC.read_counters("ycs", "th-u"))["by_node"])
    LC.clear_context()
    LC.set_node(node="orphan")
    ok("set_node outside a context is a no-op", LC.get_context() is None)

    # ---- concurrent tasks keep their own attribution (YCS extracts N videos at once) ----
    async def worker(i):
        LC.set_context(ns="ycs", run_id="ex-par", node_id=f"v{i}")
        await asyncio.sleep(0)
        await LC.bump_current_call(usage=Usage(None, i + 1, 0, 0), model="mp")
    await asyncio.gather(*(asyncio.create_task(worker(i)) for i in range(3)))
    par = await LC.read_counters("ycs", "ex-par")
    ok("concurrent tasks: one node each, no cross-attribution",
       sorted(par["by_node"]) == ["v0", "v1", "v2"] and par["by_node"]["v2"]["tokens_in"] == 3 and par["total"]["tokens_in"] == 6, str(par))

    # ---- callback: counts the call through the shared parser ----
    LC.set_context(ns="ycs", run_id="th-cb")
    LC.set_node(node="classify")
    cb = LC.LLMCounterCallback()
    rid = uuid.uuid4()
    await cb.on_llm_start({}, ["p"], run_id=rid)
    await cb.on_llm_end(RESULT, run_id=rid)
    c = await LC.read_counters("ycs", "th-cb")
    ok("callback: usage_metadata counted under the model the response names",
       c["total"]["tokens_in"] == 10 and c["total"]["tokens_out"] == 5 and "nvidia/model-a" in c["by_node"]["classify"]["by_model"], str(c))
    LC.clear_context()

    # ---- ycs Ask path: `resilient_ainvoke` / `hedged_ainvoke` hand the bare AIMessage to `capture_llm_usage` ----
    LC.set_context(ns="ycs", run_id="th-ask2")
    LC.set_node(node="generate")
    ask_msg = AIMessage(content="hi", response_metadata={"model_name": "nvidia/model-b"}, usage_metadata={
        "input_tokens": 7, "output_tokens": 3, "total_tokens": 10, "output_token_details": {"reasoning": 2}})
    await domains.ycs.rag.service.capture_llm_usage(ask_msg, duration_s=0.2)
    await domains.ycs.rag.service.capture_llm_usage(AIMessage(content="no usage"))
    a = await LC.read_counters("ycs", "th-ask2")
    ok("ycs ask: bare AIMessage counted once (tokens, reasoning, model); usage-less reply skipped", a == {
        "extract_id": "th-ask2",
        "total": {"calls": 1, "tokens_in": 7, "tokens_out": 3, "reasoning_tokens": 2},
        "by_node": {"generate": {"calls": 1, "tokens_in": 7, "tokens_out": 3, "reasoning_tokens": 2, "by_model": {
            "nvidia/model-b": {"calls": 1, "tokens_in": 7, "tokens_out": 3, "reasoning_tokens": 2}}}},
    }, str(a))
    LC.clear_context()

    # ---- the chat adapter (what DD calls): a reply is counted once, a failure once, per adapter call ----
    chat = domains.settings.chat.service

    class Schema(BaseModel):
        a: int

    class StubModel:
        def __init__(self, outcome):
            self.outcome = outcome

        def with_structured_output(self, schema, **kwargs):
            return StubStructured(self.outcome)

        async def ainvoke(self, messages):
            if isinstance(self.outcome, BaseException):
                raise self.outcome
            return self.outcome

    class StubStructured(StubModel):
        async def ainvoke(self, messages):
            raw = await super().ainvoke(messages)
            return {"raw": raw, "parsed": Schema(a=1), "parsing_error": None}

    chat._apply_endpoint = lambda *a, **k: False
    reply = AIMessage(content="ok", response_metadata={"model_name": "prov/m"},
                      usage_metadata={"input_tokens": 10, "output_tokens": 5, "total_tokens": 15})
    with LC.llm_context(ns="dd", run_id="th-chat", node_id="n1", stage="synth"):
        chat.build_chat_model = lambda **k: StubModel(reply)
        text, meta = await chat.chat_text_async("hi")
        parsed, _ = await chat.chat_structured_async("hi", Schema)
        chat.build_chat_model = lambda **k: StubModel(RuntimeError("InternalServerError: Error code: 504 - x"))
        for call in (lambda: chat.chat_text_async("hi"), lambda: chat.chat_structured_async("hi", Schema)):
            try:
                await call()
                raise AssertionError("the failing call should raise")
            except chat.errors.ChatError:
                pass
    ok("chat adapter: returns the reply untouched", text == "ok" and parsed == Schema(a=1) and meta["model"] == "prov/m")
    c = await LC.read_counters("dd", "th-chat")
    ok("chat adapter: 2 replies + 2 failures counted once each, under the model the reply names", c == {
        "thread_id": "th-chat", "stage": "synth",
        "total": {"calls": 2, "tokens_in": 20, "tokens_out": 10, "reasoning_tokens": 0, "errors": 2},
        "error_kinds": {"504": 2},
        "by_node": {"n1": {"calls": 2, "tokens_in": 20, "tokens_out": 10, "reasoning_tokens": 0, "errors": 2, "by_model": {
            "prov/m": {"calls": 2, "tokens_in": 20, "tokens_out": 10, "reasoning_tokens": 0}}}},
    }, str(c))
    outside = len(STORE)
    chat.build_chat_model = lambda **k: StubModel(reply)
    await chat.chat_text_async("hi")
    ok("chat adapter: outside any context nothing is counted", len(STORE) == outside)


asyncio.run(run())
print("ALL OK")
