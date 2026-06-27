"""Hermes Gateway client -- sends prompts to local Hermes LLM."""
import asyncio
import logging
import os

import httpx

logger = logging.getLogger(__name__)

class HermesClient:
    def __init__(self):
        self._client = httpx.AsyncClient(timeout=120.0)

    async def chat(self, message: str) -> str:
        """Send a single-turn message to Hermes gateway."""
        from settings import load_settings

        hermes_settings = load_settings()["hermes"]
        base_url = os.getenv("HERMES_GATEWAY_URL", hermes_settings.get("base_url", "http://127.0.0.1:8642"))
        api_key = os.getenv("API_SERVER_KEY", hermes_settings.get("api_key", "bridge-secret-key"))
        timeout = float(hermes_settings.get("request_timeout_seconds", 90))
        try:
            resp = await self._client.post(
                f"{base_url}/v1/chat/completions",
                headers={"Authorization": f"Bearer {api_key}"},
                timeout=timeout,
                json={
                    "model": "hermes",
                    "messages": [
                        {
                            "role": "system",
                            "content": (
                                "你是一个语音助手。请始终用中文回复。"
                                "回复要简洁口语化，控制在3句话以内。"
                                "不要用表情符号，像真人聊天一样自然说话。"
                                "不要调用任何工具，直接文字回复即可。"
                            ),
                        },
                        {"role": "user", "content": message},
                    ],
                    "max_tokens": 200,
                    "temperature": 0.7,
                },
            )
            resp.raise_for_status()
            data = resp.json()
            return data["choices"][0]["message"]["content"]
        except httpx.ConnectError:
            logger.warning("Hermes gateway not reachable at %s", base_url)
            return "Hermes 网关没启动，请先启动它。"
        except Exception:
            logger.exception("Hermes gateway call failed")
            return "抱歉，AI 后端出了点问题。"
