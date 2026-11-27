from __future__ import annotations

import unittest

import ai, domains


class DistillFailureContextTests(unittest.TestCase):
    def test_chapter_transient_retries_use_one_graph_retry_boundary(self) -> None:
        policy = ai.bd.graphs.distill.book_distillation.service.provider_retry_policy()

        self.assertEqual(
            domains.bd.distill.ai.params.CHAPTER_SYNTHESIS_PROVIDER_ATTEMPTS,
            1,
        )
        self.assertEqual(policy.max_attempts, domains.bd.distill.ai.params.TRANSIENT_NODE_RETRY_ATTEMPTS)

    def test_failure_context_records_failed_node_and_safe_provider_metadata(self) -> None:
        error = domains.settings.chat.errors.ChatError("upstream body with private details")
        error.status_code = 504
        progress = {
            "current_node": "verify_unit",
            "current_task": "Reviewing another unit",
            "active_tasks": {"verify_unit": "Reviewing another unit"},
            "node_status": {"verify_unit": "done", "synthesize_chapter": "failed"},
        }

        result = domains.bd.distill.ai.domain.failure_context(
            error,
            progress,
            provider_kind = domains.settings.chat.domain.classify_error(error),
            failed_at = 1_800_000_000.0,
        )

        failure = domains.bd.distill.ai.schemas.DistillFailureContext.model_validate(result)
        self.assertEqual(failure.node, "synthesize_chapter")
        self.assertIsNone(failure.task)
        self.assertEqual(failure.error_type, "ChatError")
        self.assertEqual(failure.provider_kind, "server_error")
        self.assertEqual(failure.status_code, 504)
        self.assertNotIn("private details", repr(result))

        message = domains.bd.distill.ai.service._failure_message(error)
        self.assertIn("HTTP 504", message)
        self.assertIn("resume this run", message)

    def test_stale_celery_delivery_returns_for_a_terminal_failed_run(self) -> None:
        result = domains.bd.distill.ai.task._terminal_task_result({
            "book_id": "book-1",
            "run_id": "run-1",
            "status": "failed",
            "error": "saved failure",
            "report_key": None,
        })

        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["error"], "saved failure")
        self.assertIsNone(domains.bd.distill.ai.task._terminal_task_result({
            "book_id": "book-1",
            "run_id": "run-1",
            "status": "queued",
        }))


if __name__ == "__main__":
    unittest.main()
