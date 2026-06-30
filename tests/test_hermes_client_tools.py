import asyncio
import json
import sys
from pathlib import Path
from unittest import TestCase, main


ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
sys.path.insert(0, str(BACKEND))

from hermes_client import HERMES_TOOLS, HermesClient  # noqa: E402


class FakeResponse:
    def raise_for_status(self):
        return None

    def json(self):
        return {"choices": [{"message": {"content": "ok"}}]}


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
        self.post_json = None
        self.stream_json = None

    async def post(self, _url, json):
        self.post_json = json
        return FakeResponse()

    def stream(self, _method, _url, json):
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

    def test_stream_request_declares_hermes_tools(self):
        client = self._client_with_fake_transport()

        result = asyncio.run(_collect(client.chat_stream("hello")))

        self.assertEqual(result, "ok")
        self.assertTrue(client._client.stream_json["stream"])
        self.assertEqual(client._client.stream_json["tools"], HERMES_TOOLS)
        self.assertEqual(client._client.stream_json["tool_choice"], "auto")


async def _collect(stream):
    chunks = []
    async for chunk in stream:
        chunks.append(chunk)
    return "".join(chunks)


if __name__ == "__main__":
    main()
