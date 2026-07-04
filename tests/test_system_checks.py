import asyncio
import sys
from pathlib import Path
from unittest import TestCase, main
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
sys.path.insert(0, str(BACKEND))

from system_checks import hermes_status  # noqa: E402


class FakeResponse:
    def __init__(self, status_code=200, payload=None):
        self.status_code = status_code
        self._payload = payload if payload is not None else {}

    def json(self):
        return self._payload


class FakeAsyncClient:
    calls = []
    routes = {}
    failures = {}

    @classmethod
    def reset(cls, routes=None, failures=None):
        cls.calls = []
        cls.routes = routes or {}
        cls.failures = failures or {}

    def __init__(self, timeout):
        self.timeout = timeout

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def get(self, url, headers=None):
        FakeAsyncClient.calls.append((url, headers or {}))
        if url in FakeAsyncClient.failures:
            raise FakeAsyncClient.failures[url]
        return FakeAsyncClient.routes[url]


class SystemChecksTest(TestCase):
    def test_hermes_status_reports_available_and_selected_model(self):
        settings = {
            "hermes": {
                "base_url": "http://10.0.0.9:8642/",
                "api_key": "status-key",
                "model": "hermes-agent",
            }
        }
        FakeAsyncClient.reset(
            {
                "http://10.0.0.9:8642/health": FakeResponse(200, {"ok": True}),
                "http://10.0.0.9:8642/v1/models": FakeResponse(
                    200,
                    {"data": [{"id": "remote-hermes"}, {"id": "hermes-agent"}]},
                ),
            }
        )

        with patch("httpx.AsyncClient", FakeAsyncClient):
            result = asyncio.run(hermes_status(settings))

        self.assertEqual(FakeAsyncClient.calls[0][0], "http://10.0.0.9:8642/health")
        self.assertEqual(FakeAsyncClient.calls[1][0], "http://10.0.0.9:8642/v1/models")
        self.assertEqual(FakeAsyncClient.calls[1][1]["Authorization"], "Bearer status-key")
        self.assertTrue(result["reachable"])
        self.assertEqual(result["health_status_code"], 200)
        self.assertEqual(result["models_status_code"], 200)
        self.assertTrue(result["auth_ok"])
        self.assertTrue(result["has_hermes_agent"])
        self.assertIsNone(result["problem"])
        self.assertEqual(result["models"], ["remote-hermes", "hermes-agent"])
        self.assertEqual(result["configured_model"], "hermes-agent")
        self.assertEqual(result["selected_model"], "hermes-agent")

    def test_hermes_status_reports_gateway_unreachable_before_models(self):
        settings = {
            "hermes": {
                "base_url": "http://10.0.0.9:8642",
                "api_key": "status-key",
                "model": "hermes-agent",
            }
        }
        FakeAsyncClient.reset(
            failures={"http://10.0.0.9:8642/health": OSError("connection refused")}
        )

        with patch("httpx.AsyncClient", FakeAsyncClient):
            result = asyncio.run(hermes_status(settings))

        self.assertEqual(len(FakeAsyncClient.calls), 1)
        self.assertFalse(result["reachable"])
        self.assertIsNone(result["health_status_code"])
        self.assertIsNone(result["models_status_code"])
        self.assertFalse(result["auth_ok"])
        self.assertFalse(result["has_hermes_agent"])
        self.assertEqual(result["problem"], "gateway_unreachable")
        self.assertIn("Hermes Gateway 未启动", result["message"])

    def test_hermes_status_reports_unauthorized_models(self):
        settings = {
            "hermes": {
                "base_url": "http://10.0.0.9:8642",
                "api_key": "wrong-key",
                "model": "hermes-agent",
            }
        }
        FakeAsyncClient.reset(
            {
                "http://10.0.0.9:8642/health": FakeResponse(200, {"ok": True}),
                "http://10.0.0.9:8642/v1/models": FakeResponse(401, {"error": "bad key"}),
            }
        )

        with patch("httpx.AsyncClient", FakeAsyncClient):
            result = asyncio.run(hermes_status(settings))

        self.assertFalse(result["reachable"])
        self.assertEqual(result["health_status_code"], 200)
        self.assertEqual(result["models_status_code"], 401)
        self.assertFalse(result["auth_ok"])
        self.assertFalse(result["has_hermes_agent"])
        self.assertEqual(result["problem"], "unauthorized")
        self.assertIn("密钥", result["message"])

    def test_hermes_status_reports_missing_hermes_agent_model(self):
        settings = {
            "hermes": {
                "base_url": "http://10.0.0.9:8642",
                "api_key": "status-key",
                "model": "hermes-agent",
            }
        }
        FakeAsyncClient.reset(
            {
                "http://10.0.0.9:8642/health": FakeResponse(200, {"ok": True}),
                "http://10.0.0.9:8642/v1/models": FakeResponse(
                    200,
                    {"data": [{"id": "remote-hermes"}, {"id": "flash"}]},
                ),
            }
        )

        with patch("httpx.AsyncClient", FakeAsyncClient):
            result = asyncio.run(hermes_status(settings))

        self.assertFalse(result["reachable"])
        self.assertEqual(result["health_status_code"], 200)
        self.assertEqual(result["models_status_code"], 200)
        self.assertTrue(result["auth_ok"])
        self.assertFalse(result["has_hermes_agent"])
        self.assertEqual(result["problem"], "missing_hermes_agent")
        self.assertIn("hermes-agent", result["message"])
        self.assertEqual(result["selected_model"], "remote-hermes")


if __name__ == "__main__":
    main()
