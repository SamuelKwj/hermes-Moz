import asyncio
import json
import sys
from pathlib import Path
from unittest import TestCase, main
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
sys.path.insert(0, str(BACKEND))

from hermes_client import HERMES_TOOLS, HermesClient  # noqa: E402


class FakeResponse:
    def __init__(self, payload=None):
        self.payload = payload or {"choices": [{"message": {"content": "ok"}}]}

    def raise_for_status(self):
        return None

    def json(self):
        return self.payload


class FakeStreamResponse:
    def __init__(self, lines):
        self.lines = lines

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    def raise_for_status(self):
        return None

    async def aiter_lines(self):
        for line in self.lines:
            yield line


class FakeClient:
    def __init__(self):
        self.get_url = None
        self.get_headers = None
        self.post_url = None
        self.post_headers = None
        self.post_json = None
        self.stream_url = None
        self.stream_headers = None
        self.stream_json = None
        self.models_payload = {"data": []}

    async def get(self, url, headers=None):
        self.get_url = url
        self.get_headers = headers or {}
        return FakeResponse(self.models_payload)

    async def post(self, url, json, headers=None):
        self.post_url = url
        self.post_headers = headers or {}
        self.post_json = json
        return FakeResponse()

    def stream(self, _method, url, json, headers=None):
        self.stream_url = url
        self.stream_headers = headers or {}
        self.stream_json = json
        payload = {"choices": [{"delta": {"content": "ok"}}]}
        return FakeStreamResponse(["data: " + json_module_dumps(payload), "data: [DONE]"])


def json_module_dumps(value):
    return json.dumps(value)


class HermesClientToolsTest(TestCase):
    def _client_with_fake_transport(self):
        client = HermesClient.__new__(HermesClient)
        client.base_url = "http://127.0.0.1:8642"
        client._client = FakeClient()
        return client

    def test_chat_request_declares_hermes_tools(self):
        client = self._client_with_fake_transport()

        result = asyncio.run(client.chat("hello"))

        self.assertEqual(result, "ok")
        self.assertEqual(client._client.post_json["tools"], HERMES_TOOLS)
        self.assertEqual(client._client.post_json["tool_choice"], "auto")
        self.assertIn("terminal", {tool["function"]["name"] for tool in HERMES_TOOLS})

    def test_chat_uses_configured_gateway_and_auto_selects_available_model(self):
        client = self._client_with_fake_transport()
        client._client.models_payload = {
            "data": [
                {"id": "deepseek-v4-pro"},
                {"id": "flash"},
            ]
        }
        settings = {
            "hermes": {
                "base_url": "http://192.168.1.20:8642/",
                "api_key": "new-machine-key",
                "model": "missing-model",
                "max_tokens": 512,
                "temperature": 0.2,
            }
        }

        with patch("settings.load_settings", return_value=settings):
            result = asyncio.run(client.chat("hello"))

        self.assertEqual(result, "ok")
        self.assertEqual(client._client.get_url, "http://192.168.1.20:8642/v1/models")
        self.assertEqual(client._client.post_url, "http://192.168.1.20:8642/v1/chat/completions")
        self.assertEqual(client._client.post_headers["Authorization"], "Bearer new-machine-key")
        self.assertEqual(client._client.post_json["model"], "deepseek-v4-pro")
        self.assertEqual(client._client.post_json["max_tokens"], 512)
        self.assertEqual(client._client.post_json["temperature"], 0.2)

    def test_stream_request_declares_hermes_tools(self):
        client = self._client_with_fake_transport()

        result = asyncio.run(_collect(client.chat_stream("hello")))

        self.assertEqual(result, "ok")
        self.assertTrue(client._client.stream_json["stream"])
        self.assertEqual(client._client.stream_json["tools"], HERMES_TOOLS)
        self.assertEqual(client._client.stream_json["tool_choice"], "auto")

    def test_stream_uses_configured_gateway_and_auto_selects_available_model(self):
        client = self._client_with_fake_transport()
        client._client.models_payload = {
            "data": [
                {"id": "remote-hermes"},
            ]
        }
        settings = {
            "hermes": {
                "base_url": "http://10.0.0.8:8642",
                "api_key": "stream-key",
                "model": "old-model",
                "max_tokens": 256,
                "temperature": 0.4,
            }
        }

        with patch("settings.load_settings", return_value=settings):
            result = asyncio.run(_collect(client.chat_stream("hello")))

        self.assertEqual(result, "ok")
        self.assertEqual(client._client.get_url, "http://10.0.0.8:8642/v1/models")
        self.assertEqual(client._client.stream_url, "http://10.0.0.8:8642/v1/chat/completions")
        self.assertEqual(client._client.stream_headers["Authorization"], "Bearer stream-key")
        self.assertEqual(client._client.stream_json["model"], "remote-hermes")


async def _collect(stream):
    chunks = []
    async for chunk in stream:
        chunks.append(chunk)
    return "".join(chunks)


if __name__ == "__main__":
    main()
