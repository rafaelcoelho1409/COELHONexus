from __future__ import annotations

import asyncio
import unittest
from datetime import datetime, timezone
from email.utils import format_datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import domains


class ChatRetryResilienceTests(unittest.TestCase):
    def test_classifies_gateway_status_codes_directly(self) -> None:
        error = domains.settings.chat.errors.ChatError("upstream response")
        error.status_code = 504

        self.assertEqual(domains.settings.chat.domain.classify_error(error), "server_error")
        self.assertTrue(domains.settings.chat.domain.is_transient_provider_error(error))

    def test_reads_retry_after_delta_and_http_date(self) -> None:
        delta_error = RuntimeError("provider busy")
        delta_error.response = SimpleNamespace(headers = {"retry-after": "12.5"})
        self.assertEqual(domains.settings.chat.domain.retry_after_s(delta_error), 12.5)

        now = 1_000_000.0
        date_error = RuntimeError("provider busy")
        date_error.response = SimpleNamespace(headers = {
            "retry-after": format_datetime(datetime.fromtimestamp(now + 20, timezone.utc), usegmt = True),
        })
        self.assertEqual(domains.settings.chat.domain.retry_after_s(date_error, now_s = now), 20.0)

    def test_unsettled_rotator_pool_returns_a_typed_non_provider_failure(self) -> None:
        error = domains.settings.chat.domain.unsettled_pool_error(
            {"reason": "cap_reached", "waited_s": 130.0, "cooling": 13, "pool": 38},
            max_cooling_frac = 0.25,
        )

        self.assertIsInstance(error, domains.settings.chat.errors.ChatPoolUnsettledError)
        self.assertFalse(domains.settings.chat.domain.is_transient_provider_error(error))
        self.assertIsNone(domains.settings.chat.domain.unsettled_pool_error(
            {"reason": "pool_recovered", "waited_s": 12.0, "cooling": 4, "pool": 38},
            max_cooling_frac = 0.25,
        ))

    def test_resilient_invoke_uses_bounded_exponential_jitter_and_retry_after(self) -> None:
        first_error = domains.settings.chat.errors.ChatError("temporary server error")
        first_error.status_code = 503
        first_error.response = SimpleNamespace(headers = {"retry-after": "4"})
        second_error = domains.settings.chat.errors.ChatError("temporary server error")
        second_error.status_code = 504
        invoke = AsyncMock(side_effect = [first_error, second_error, "recovered"])
        sleep = AsyncMock()

        with (
            patch.object(domains.settings.chat.service.asyncio, "sleep", new = sleep),
            patch.object(domains.settings.chat.service.random, "uniform", side_effect = [2.0, 3.0]),
        ):
            result = asyncio.run(domains.settings.chat.service.resilient_ainvoke(
                invoke,
                attempts = 3,
                wait_base_s = 5.0,
                max_wait_s = 8.0,
            ))

        self.assertEqual(result, "recovered")
        self.assertEqual(invoke.await_count, 3)
        self.assertEqual([call.args[0] for call in sleep.await_args_list], [4.0, 3.0])

    def test_resilient_invoke_caps_retry_after(self) -> None:
        error = domains.settings.chat.errors.ChatError("temporary server error")
        error.status_code = 503
        error.response = SimpleNamespace(headers = {"retry-after": "120"})
        invoke = AsyncMock(side_effect = [error, "recovered"])
        sleep = AsyncMock()

        with (
            patch.object(domains.settings.chat.service.asyncio, "sleep", new = sleep),
            patch.object(domains.settings.chat.service.random, "uniform", return_value = 1.0),
        ):
            asyncio.run(domains.settings.chat.service.resilient_ainvoke(
                invoke,
                attempts = 2,
                wait_base_s = 5.0,
                max_wait_s = 8.0,
            ))

        sleep.assert_awaited_once_with(8.0)


if __name__ == "__main__":
    unittest.main()
