"""Exercise authenticated HTTP polling with real worker scheduling and files."""
from __future__ import annotations

import asyncio
import sys
import threading
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import anyio.to_thread
import httpx

SERVICE_ROOT = Path(__file__).resolve().parents[1]
if str(SERVICE_ROOT) not in sys.path:
    sys.path.insert(0, str(SERVICE_ROOT))

from api import main
from recommendation.pipeline import PipelineDependencyError


class RunPollingTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp_dir = TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        for patcher in (
            patch.dict("os.environ", {"INTERNAL_API_TOKEN": "poll-test-token"}),
            patch("api.main._output_root", return_value=Path(self.temp_dir.name)),
            patch("api.main._service_config", return_value=main.ServiceConfig(
                quarter="20261", source="files", llm_mode="offline", limit=5,
                max_concurrent=1, request_timeout_s=0.1,
            )),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)
        self.client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=main.app), base_url="http://test",
            headers={"X-Internal-Token": "poll-test-token"},
        )
        self.addAsyncCleanup(self.client.aclose)
        self.payload = {
            "request_id": "poll-request", "region": {"sigungu": "송파구"},
            "industry_code": "CS100010", "limit": 3,
        }

    @staticmethod
    def result():
        return {
            "summary": {"candidate_count": 0}, "candidates": [],
            "explanations": {}, "input_interpretation": {"resolved_industry_code": "CS100010"},
        }

    async def wait_for_slot_release(self):
        async def wait_until_idle():
            while main._ACTIVE_RECOMMENDATIONS:
                await asyncio.sleep(0.01)

        await asyncio.wait_for(wait_until_idle(), timeout=3)

    async def test_timeout_worker_remains_pollable_and_releases_capacity(self):
        for fails in (False, True):
            with self.subTest(fails=fails):
                release = threading.Event()
                started = asyncio.Event()
                loop = asyncio.get_running_loop()

                def pipeline(*args, **kwargs):
                    loop.call_soon_threadsafe(started.set)
                    if not release.wait(3):
                        raise RuntimeError("test worker was not released")
                    if fails:
                        raise PipelineDependencyError("postgresql://private/password-secret")
                    return self.result()

                with patch("api.main.run_pipeline", side_effect=pipeline):
                    try:
                        response = await self.client.post("/internal/recommendations", json=self.payload)
                        self.assertEqual(response.status_code, 504)
                        self.assertNotIn("retry-after", response.headers)
                        await asyncio.wait_for(started.wait(), 1)
                        status_url = response.json()["detail"]["status_url"]
                        pending = await self.client.get(status_url)
                        self.assertEqual(pending.status_code, 202)
                        self.assertEqual(pending.json()["status"], "running")
                        capacity = await self.client.post("/internal/recommendations", json=self.payload)
                        self.assertEqual(capacity.status_code, 429)
                        self.assertEqual(capacity.headers["retry-after"], "10")
                    finally:
                        release.set()
                        await self.wait_for_slot_release()
                    terminal = await self.client.get(status_url)
                    if fails:
                        self.assertEqual(terminal.status_code, 503)
                        self.assertEqual(terminal.json()["detail"]["code"], "dependency_unavailable")
                        self.assertNotIn("password-secret", terminal.text)
                    else:
                        self.assertEqual(terminal.status_code, 200)
                        self.assertEqual(terminal.json()["request_id"], "poll-request")
                        self.assertEqual(terminal.json()["request"]["limit"], 3)
                        self.assertEqual(terminal.json()["run_id"], response.json()["detail"]["run_id"])

    async def test_timeout_while_waiting_for_threadpool_eventually_completes(self):
        limiter = anyio.to_thread.current_default_thread_limiter()
        original_tokens = limiter.total_tokens
        limiter.total_tokens = 1
        release = threading.Event()
        occupied = asyncio.Event()
        loop = asyncio.get_running_loop()

        def occupy_pool():
            loop.call_soon_threadsafe(occupied.set)
            release.wait(3)

        # Isolate worker queueing from the synchronous auth dependency, which
        # shares the same limiter. Authentication is exercised separately above.
        async def authenticated():
            main.verify_internal_token("poll-test-token")

        main.app.dependency_overrides[main.verify_internal_token] = authenticated
        blocker = asyncio.create_task(main.run_in_threadpool(occupy_pool))
        with patch("api.main.run_pipeline", return_value=self.result()):
            try:
                await asyncio.wait_for(occupied.wait(), 1)
                response = await self.client.post("/internal/recommendations", json=self.payload)
                self.assertEqual(response.status_code, 504)
                release.set()
                await blocker
                await self.wait_for_slot_release()
                terminal = await self.client.get(response.json()["detail"]["status_url"])
                self.assertEqual(terminal.status_code, 200)
            finally:
                release.set()
                await blocker
                limiter.total_tokens = original_tokens
                main.app.dependency_overrides.pop(main.verify_internal_token, None)
                # A failing regression must not contaminate other tests.
                main._ACTIVE_RECOMMENDATIONS = 0

    async def test_completed_response_is_recovered_if_final_status_write_fails(self):
        write_artifact = main._write_json_artifact

        def fail_completed_marker(path, payload):
            if path.name == "run-status.json" and payload.get("status") == "completed":
                raise OSError("simulated status write failure")
            write_artifact(path, payload)

        with patch("api.main.run_pipeline", return_value=self.result()), patch(
            "api.main._write_json_artifact", side_effect=fail_completed_marker,
        ), self.assertLogs("api.main", level="ERROR"):
            response = await self.client.post("/internal/recommendations", json=self.payload)
        self.assertEqual(response.status_code, 200)
        terminal = await self.client.get(f"/internal/recommendations/{response.json()['run_id']}")
        self.assertEqual(terminal.status_code, 200)
        self.assertEqual(terminal.json(), response.json())

    async def test_polling_requires_authentication_and_unknown_runs_return_404(self):
        url = "/internal/recommendations/20260905T000000Z-0123456789ab"
        for token in ("", "wrong"):
            response = await self.client.get(url, headers={"X-Internal-Token": token})
            self.assertEqual(response.status_code, 401)
        self.assertEqual((await self.client.get(url)).status_code, 404)
        self.assertEqual((await self.client.get("/internal/recommendations/invalid-run")).status_code, 404)
