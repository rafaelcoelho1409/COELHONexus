"""Offline check of how a Neo4j result row is made JSON-safe (`domains.ycs.query.domain.neo4j_value`).

    cd apps/server && .venv/bin/python ../../scripts/server/test_ycs_query_neo4j.py

A node or relationship whose properties hold a driver type that JSON cannot carry (a `DateTime`, a `Date`, a point) used
to leave the property raw, so the response failed to serialise (HTTP 500) and the Query tab's Graph view never loaded.
Stand-in Node / Relationship / Path classes are passed in, so no Neo4j driver or server is needed."""
import json
import os
import sys
from pathlib import Path

for name, value in {"REDIS_HOST": "localhost", "REDIS_PORT": "6379", "REDIS_PASSWORD": "x", "ENVIRONMENT": "local"}.items():
    os.environ.setdefault(name, value)
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "apps" / "server"))

import domains

neo4j_value = domains.ycs.query.domain.neo4j_value


class FakeDateTime:
    """Stands in for `neo4j.time.DateTime`: not JSON-serialisable, prints as ISO text."""
    def __str__(self) -> str:
        return "2026-10-30T14:04:00.000000000+00:00"


class FakeNode(dict):
    def __init__(self, element_id, labels, **properties):
        super().__init__(properties)
        self.element_id = element_id
        self.labels = labels


class FakeRel(dict):
    def __init__(self, element_id, rel_type, start, end, **properties):
        super().__init__(properties)
        self.element_id = element_id
        self.type = rel_type
        self.start_node = start
        self.end_node = end


class FakePath:
    def __init__(self, nodes, rels):
        self.nodes = nodes
        self.relationships = rels


KINDS = {"Node": FakeNode, "Rel": FakeRel, "Path": FakePath}


def check(label: str, got, want) -> None:
    assert got == want, f"{label}: expected {want!r}, got {got!r}"
    print(f"ok  {label}")


video = FakeNode("n1", ["Video"], title = "Redis", upload_date = FakeDateTime(), tags = ["a", FakeDateTime()])
channel = FakeNode("n2", ["Channel"], name = "Weekly")
belongs = FakeRel("r1", "BELONGS_TO", video, channel, since = FakeDateTime())

node = neo4j_value(video, **KINDS)
check("node properties keep plain values", node["properties"]["title"], "Redis")
check("node DateTime becomes text", node["properties"]["upload_date"], "2026-10-30T14:04:00.000000000+00:00")
check("a DateTime inside a list becomes text", node["properties"]["tags"], ["a", "2026-10-30T14:04:00.000000000+00:00"])

rel = neo4j_value(belongs, **KINDS)
check("relationship DateTime becomes text", rel["properties"]["since"], "2026-10-30T14:04:00.000000000+00:00")
check("relationship ends are element ids", (rel["start"], rel["end"]), ("n1", "n2"))

path = neo4j_value(FakePath([video, channel], [belongs]), **KINDS)
check("path nodes are made safe too", path["nodes"][0]["properties"]["upload_date"], "2026-10-30T14:04:00.000000000+00:00")

# The whole row must survive json.dumps — the failure was a serialisation error, not a wrong value.
row = {"v": video, "r": belongs, "c": channel, "when": FakeDateTime()}
encoded = json.dumps({k: neo4j_value(v, **KINDS) for k, v in row.items()})
check("a graph row serialises to JSON", isinstance(encoded, str) and "BELONGS_TO" in encoded, True)
print("all ok")
