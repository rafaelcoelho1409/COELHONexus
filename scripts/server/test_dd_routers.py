"""Characterization test of the dd endpoints that start, gate, cancel, wipe and report runs (ingestion runs, planner, synth,
pipeline) — the ones backed by Redis single-flight locks and run registries.

    cd apps/server && .venv/bin/python ../../scripts/server/test_dd_routers.py            # check against the golden file
    cd apps/server && .venv/bin/python ../../scripts/server/test_dd_routers.py --update   # rewrite the golden file

The real routers run in a FastAPI `TestClient` over a throwaway `redis-server`; MinIO is an in-memory fake and the Celery
`.delay` calls are recorded instead of sent. Every step records its HTTP status + JSON and the full Redis state after it
(keys, types, TTL in minutes, values), with ids and timestamps normalised. The refactors that moved Redis out of the
routers had to leave this transcript byte-identical."""
import atexit
import json
import os
import re
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from types import SimpleNamespace

GOLDEN = Path(__file__).with_name("golden") / "dd_routers.json"


# ---- throwaway redis (REDIS_* must be set before `infra` is imported) ----------------------------------------------
def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


PORT = _free_port()
_proc = subprocess.Popen(
    ["redis-server", "--port", str(PORT), "--bind", "127.0.0.1", "--save", "", "--appendonly", "no", "--dir", tempfile.mkdtemp()],
    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
)
atexit.register(_proc.terminate)
os.environ.update(REDIS_HOST="127.0.0.1", REDIS_PORT=str(PORT), REDIS_PASSWORD="", ENVIRONMENT="local", DD_STUDY_SEM="2")
for _name in ("POSTGRES_HOST", "POSTGRES_USER", "POSTGRES_PASSWORD", "POSTGRES_DATABASE"):
    os.environ.pop(_name, None)
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "apps" / "server"))

from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

import domains, entrypoints, infra  # noqa: E402
import entrypoints.api.v1.router as v1  # noqa: E402

rs = infra.redis.service.new_redis_sync()
for _ in range(50):
    try:
        rs.ping()
        break
    except Exception:
        time.sleep(0.1)


# ---- fakes ---------------------------------------------------------------------------------------------------------
class FakeMinIO:
    def __init__(self):
        self.objects: dict[str, str] = {}

    async def read_text(self, key):
        if key not in self.objects:
            raise KeyError(key)
        return self.objects[key]

    async def exists(self, key):
        return key in self.objects

    async def list(self, prefix):
        return sorted(k for k in self.objects if k.startswith(prefix))

    async def write(self, key, content, content_type=None):
        self.objects[key] = content

    async def delete_prefix(self, prefix):
        gone = [k for k in self.objects if k.startswith(prefix)]
        for k in gone:
            del self.objects[k]
        return len(gone)


MINIO = FakeMinIO()
infra.minio.service.get_storage = lambda: MINIO

CALLS: list = []


def _stub(name):
    def delay(*args, **kwargs):
        CALLS.append([name, list(args)])
        return SimpleNamespace(id=f"task-{len(CALLS)}")
    return delay


import domains.dd.ingestion.task  # noqa: E402,F401
import domains.dd.planner.task  # noqa: E402,F401
import domains.dd.synth.task  # noqa: E402,F401

domains.dd.ingestion.task.run_ingestion.delay = _stub("run_ingestion")
domains.dd.planner.task.run_planner.delay = _stub("run_planner")
domains.dd.planner.task.resume_planner.delay = _stub("resume_planner")
domains.dd.synth.task.run_study.delay = _stub("run_study")
domains.dd.synth.task.run_single_chapter.delay = _stub("run_single_chapter")
domains.dd.synth.task.resume_synth.delay = _stub("resume_synth")


async def _no_catalog_check(slug):
    return {"name": slug}


entrypoints.api.v1.dd.resolver.service.get_catalog_entry = _no_catalog_check

app = FastAPI()
app.include_router(v1.api_v1, prefix="/api")
client = TestClient(app)


# ---- transcript ----------------------------------------------------------------------------------------------------
_UUID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")
_HEX = re.compile(r"\b[0-9a-f]{32}\b")
_TS = re.compile(r"\b\d{9,11}\.\d+\b")


def norm(value):
    if isinstance(value, str):
        return _TS.sub("<ts>", _HEX.sub("<hex>", _UUID.sub("<uuid>", value)))
    if isinstance(value, list):
        return [norm(v) for v in value]
    if isinstance(value, dict):
        return {norm(k): norm(v) for k, v in value.items()}
    if isinstance(value, float) and value > 1e9:
        return "<ts>"
    return value


def _dec(raw):
    return raw.decode() if isinstance(raw, bytes) else raw


def redis_state() -> dict:
    out = {}
    for key in sorted(_dec(k) for k in rs.keys("*")):
        kind = _dec(rs.type(key))
        ttl = rs.ttl(key)
        entry = {"type": kind, "ttl_min": round(ttl / 60) if ttl and ttl > 0 else None}
        if kind == "string":
            entry["value"] = norm(_dec(rs.get(key)))
        elif kind == "list":
            entry["items"] = norm([_dec(v) for v in rs.lrange(key, 0, -1)])
        elif kind == "set":
            entry["members"] = sorted(norm(_dec(v)) for v in rs.smembers(key))
        out[norm(key)] = entry
    return out


TRANSCRIPT: list[dict] = []


def step(name, method, path, **kwargs):
    CALLS.clear()
    resp = client.request(method, f"/api/v1/dd{path}", **kwargs)
    try:
        body = resp.json()
    except Exception:
        body = resp.text
    if isinstance(body, dict) and isinstance(body.get("propagated_to"), list):
        body = {**body, "propagated_to": sorted(body["propagated_to"])}
    TRANSCRIPT.append({
        "step": name, "status": resp.status_code, "json": norm(body), "celery": norm(list(CALLS)),
        "redis": redis_state(), "minio": sorted(MINIO.objects),
    })
    return body


def reset():
    rs.flushall()
    MINIO.objects.clear()


def seed_plan(slug, chapter_ids=("ch-1", "ch-2")):
    MINIO.objects[domains.dd.planner.keys.plan_latest_key(slug)] = json.dumps({"chapters": [{"id": c, "title": c} for c in chapter_ids]})


def seed_manifest(slug):
    MINIO.objects[domains.dd.ingestion.storage.keys.manifest_key(slug)] = json.dumps({"entries": [], "slug": slug})


def val(key):
    raw = rs.get(key)
    return _dec(raw) if raw is not None else None


# ===== ingestion runs =====
reset()
queued = step("runs: start fw", "POST", "/runs", json={"slug": "fw", "refresh": False})
run_id = queued["run_id"]
step("runs: start fw again (same slug locked)", "POST", "/runs", json={"slug": "fw"})
step("runs: start fw2 (other slug locked)", "POST", "/runs", json={"slug": "fw2"})
step("runs: active without a progress record", "GET", "/runs/active")
rs.set(domains.dd.ingestion.runtime.progress.keys.progress_key(run_id), json.dumps({"status": "running", "phase": "tier1"}))
step("runs: active with progress", "GET", "/runs/active")
step("runs: get run", "GET", f"/runs/{run_id}")
step("runs: get unknown run", "GET", "/runs/nope")
step("runs: url records of unknown run", "GET", "/runs/nope/url-records")
step("runs: cancel", "POST", f"/runs/{run_id}/cancel")
step("ingestion: delete framework releases the lock", "DELETE", "/ingestion/fw")
reset()
seed_manifest("cachefw")
step("runs: cached manifest", "POST", "/runs", json={"slug": "cachefw"})
MINIO.objects.update({
    domains.dd.ingestion.storage.keys.manifest_key("rf"): "{}", "ingestion-raw/rf/a": "x", "synth-vault/rf/b": "y", "keep/rf/c": "z",
})
# A refresh no longer wipes the stored copy at the API call: the queued run sets it aside itself when it starts (and puts it back
# if it fails), so what is stored is untouched here.
step("runs: refresh queues and leaves the stored copy for the run to set aside", "POST", "/runs", json={"slug": "rf", "refresh": True})

# ===== planner =====
reset()
seed_manifest("pf")
seed_manifest("pg")
step("planner: start without a manifest", "POST", "/planner/nomf")
step("planner: invalid mode", "POST", "/planner/pf", params={"mode": "bogus"})
step("planner: start pf", "POST", "/planner/pf")
planner_tid = val(domains.dd.planner.keys.lock_key("pf"))
step("planner: start pf again (same slug locked)", "POST", "/planner/pf")
step("planner: start pg (other planner locked)", "POST", "/planner/pg")
step("planner: active pf", "GET", "/planner/pf/active")
step("planner: active unknown slug", "GET", "/planner/unknown/active")
rs.set(domains.dd.synth.keys.lock_key("sx"), "docs-distiller/study/sx/aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa", ex=600)
step("planner: start pg (synth running)", "POST", "/planner/pg")
step("pipeline: active locks", "GET", "/pipeline/active")
step("planner: cancel", "POST", f"/planner/{planner_tid}/cancel")
step("planner: resume", "POST", f"/planner/{planner_tid}/resume")
step("planner: wipe", "DELETE", "/planner/pf/wipe")
rs.delete(domains.dd.synth.keys.lock_key("sx"))
step("planner: start with a given thread id", "POST", "/planner/pg", params={"thread_id": "docs-distiller/pg/fixed"})
step("planner: wipe pg (frees the lock)", "DELETE", "/planner/pg/wipe")
step("planner: wipe with a slash", "DELETE", "/planner/a%2Fb/wipe")

# ===== synth =====
reset()
seed_plan("sf")
seed_plan("sg")
step("synth: invalid mode", "POST", "/synth/sf", params={"mode": "bogus"})
step("synth: study without a plan", "POST", "/synth/nopl")
step("synth: start study sf", "POST", "/synth/sf")
study_tid = val(domains.dd.synth.keys.lock_key("sf"))
step("synth: start study sf again (same slug locked)", "POST", "/synth/sf")
step("synth: start study sg (other synth locked)", "POST", "/synth/sg")
step("synth: active sf (study)", "GET", "/synth/sf/active")
step("synth: active unknown", "GET", "/synth/unknown/active")
rs.sadd(domains.dd.synth.keys.active_chapters_key(study_tid), "docs-distiller/synth/sf/bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb")
rs.rpush("coelhonexus:dd:synth:docs-distiller/synth/sf/cccccccc-cccc-cccc-cccc-cccccccccccc:events:snapshot", "{}")
step("synth: wipe one chapter while a study runs", "DELETE", "/synth/sf/wipe", params={"chapter_id": "ch-1"})
step("synth: cancel study (propagates)", "POST", f"/synth/{study_tid}/cancel")
step("synth: resume", "POST", f"/synth/{study_tid}/resume")
step("synth: wipe sf", "DELETE", "/synth/sf/wipe")
rs.set(domains.dd.planner.keys.lock_key("pl"), "docs-distiller/pl/dddddddd-dddd-dddd-dddd-dddddddddddd", ex=600)
step("synth: start study sg (planner running)", "POST", "/synth/sg")
step("synth: start chapter sg ch-1 (planner running)", "POST", "/synth/sg", params={"chapter_id": "ch-1"})
reset()
seed_plan("sf")
step("synth: chapter not in plan", "POST", "/synth/sf", params={"chapter_id": "ch-9"})
step("synth: start chapter ch-1", "POST", "/synth/sf", params={"chapter_id": "ch-1"})
chapter_tid = val(domains.dd.synth.keys.lock_key("sf"))
step("synth: active sf (single chapter)", "GET", "/synth/sf/active")
step("synth: start chapter ch-2 (slug locked)", "POST", "/synth/sf", params={"chapter_id": "ch-2"})
step("synth: wipe one chapter while a chapter runs", "DELETE", "/synth/sf/wipe", params={"chapter_id": "ch-1"})
step("synth: cancel chapter thread", "POST", f"/synth/{chapter_tid}/cancel")
rs.delete(domains.dd.synth.keys.lock_key("sf"))
MINIO.objects["synth/sf/ch-1/render-latest.json"] = "{}"
step("synth: wipe one chapter when idle", "DELETE", "/synth/sf/wipe", params={"chapter_id": "ch-1"})
step("synth: wipe one chapter, bad id", "DELETE", "/synth/sf/wipe", params={"chapter_id": "x"})

# ---- compare / update ----------------------------------------------------------------------------------------------
if "--update" in sys.argv:
    GOLDEN.parent.mkdir(exist_ok=True)
    GOLDEN.write_text(json.dumps(TRANSCRIPT, indent=1, sort_keys=True, ensure_ascii=False) + "\n")
    print(f"wrote {GOLDEN} ({len(TRANSCRIPT)} steps)")
    sys.exit(0)

golden = json.loads(GOLDEN.read_text())
actual = json.loads(json.dumps(TRANSCRIPT, sort_keys=True))
assert len(golden) == len(actual), f"{len(golden)} golden steps vs {len(actual)}"
bad = [a["step"] for g, a in zip(golden, actual) if g != a]
if bad:
    for g, a in zip(golden, actual):
        if g != a:
            for part in ("status", "json", "celery", "redis", "minio"):
                if g[part] != a[part]:
                    print(f"DIFF {a['step']} [{part}]\n  golden: {json.dumps(g[part], sort_keys=True)[:400]}\n  actual: {json.dumps(a[part], sort_keys=True)[:400]}")
            break
    raise SystemExit(f"FAIL {len(bad)} step(s) differ: {bad}")
print(f"ok  {len(actual)} steps match the golden transcript")
print("ALL OK")
