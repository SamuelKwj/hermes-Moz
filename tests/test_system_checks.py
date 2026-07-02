import asyncio
import sys
from pathlib import Path
from unittest import TestCase, main
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
sys.path.insert(0, str(BACKEND))

from system_checks import hermes_status  # noqa: E402


class FakeModelsResponse:
    status_code = 200

    def json(self):
        return {"data": [{"id": "remote-hermes"}, {"id": "flash"}]}


class FakeAsyncClient:
    last_get_url = None
    last_headers = None

    def __init__(self, timeout):
        self.timeout = timeout

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def get(self, url, headers=None):
        FakeAsyncClient.last_get_url = url
        FakeAsyncClient.last_headers = headers or {}
        return FakeModelsResponse()


class SystemChecksTest(TestCase):
    def test_hermes_status_reports_available_and_selected_model(self):
        settings = {
            "hermes": {
                "base_url": "http://10.0.0.9:8642/",
                "api_key": "status-key",
                "model": "missing-model",
            }
        }

        with patch("httpx.AsyncClient", FakeAsyncClient):
            result = asyncio.run(hermes_status(settings))

        self.assertEqual(FakeAsyncClient.last_get_url, "http://10.0.0.9:8642/v1/models")
        self.assertEqual(FakeAsyncClient.last_headers["Authorization"], "Bearer status-key")
        self.assertTrue(result["reachable"])
        self.assertEqual(result["models"], ["remote-hermes", "flash"])
        self.assertEqual(result["configured_model"], "missing-model")
        self.assertEqual(result["selected_model"], "remote-hermes")


if __name__ == "__main__":
    main()
