from __future__ import annotations

import asyncio
import unittest
from types import SimpleNamespace

import infra


class CheckpointHistory:
    def __init__(self, snapshots: list[SimpleNamespace]) -> None:
        self.snapshots = snapshots

    async def aget_state_history(self, config: dict):
        for snapshot in self.snapshots:
            yield snapshot


class DistillReplayCheckpointTests(unittest.TestCase):
    def test_replay_accepts_a_checkpoint_with_same_node_fanout_tasks(self) -> None:
        checkpoint = {"configurable": {"thread_id": "run", "checkpoint_id": "before-node"}}
        graph = CheckpointHistory([SimpleNamespace(next = ("synthesize_chapter", "synthesize_chapter"), config = checkpoint)])

        result = asyncio.run(infra.postgres.service.checkpoint_before_node(
            graph,
            {"configurable": {"thread_id": "run"}},
            "synthesize_chapter",
        ))

        self.assertEqual(result, checkpoint)

    def test_replay_rejects_a_checkpoint_mixed_with_another_node(self) -> None:
        graph = CheckpointHistory([SimpleNamespace(
            next = ("synthesize_chapter", "synthesize_book"),
            config = {"configurable": {"thread_id": "run", "checkpoint_id": "mixed"}},
        )])

        with self.assertRaisesRegex(ValueError, "cannot be replayed independently"):
            asyncio.run(infra.postgres.service.checkpoint_before_node(
                graph,
                {"configurable": {"thread_id": "run"}},
                "synthesize_chapter",
            ))


if __name__ == "__main__":
    unittest.main()
