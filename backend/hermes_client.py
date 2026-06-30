"""Hermes Gateway client -- sends prompts to local Hermes LLM."""
import asyncio
import json
import logging
import os

import httpx

logger = logging.getLogger(__name__)

HERMES_BASE = os.getenv("HERMES_GATEWAY_URL", "http://127.0.0.1:8642")
API_KEY = os.getenv("API_SERVER_KEY", "bridge-secret-key")

# Tool definitions passed to Hermes API so the model can execute actions
# (terminal, file operations, web search) instead of saying "I don't have tools".
# Hermes API server handles the full tool-calling loop internally.
HERMES_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "terminal",
            "description": "在电脑上执行命令行操作，如创建文件夹、打开程序、管理系统文件等",
            "parameters": {
                "type": "object",
                "properties": {
                    "command": {
                        "type": "string",
                        "description": "要执行的命令"
                    }
                },
                "required": ["command"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": "读取电脑上的文件内容",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "文件路径"
                    }
                },
                "required": ["path"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "write_file",
            "description": "创建或覆盖写入文件",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "文件路径"
                    },
                    "content": {
                        "type": "string",
                        "description": "文件内容"
                    }
                },
                "required": ["path", "content"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "web_search",
            "description": "在网上搜索信息",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "搜索关键词"
                    }
                },
                "required": ["query"]
            }
        }
    }
]


class HermesClient:
    def __init__(self):
        self.base_url = HERMES_BASE
        self._client = httpx.AsyncClient(
            timeout=120.0,
            headers={"Authorization": f"Bearer {API_KEY}"},
        )

    async def chat(self, message: str, history: list = None) -> str:
        """Send a single-turn or multi-turn message to Hermes gateway."""
        messages, model, max_tokens, temperature = self._build_payload(message, history)
        try:
            resp = await self._client.post(
                f"{self.base_url}/v1/chat/completions",
                json={
                    "model": model,
                    "messages": messages,
                    "max_tokens": max_tokens,
                    "temperature": temperature,
                    "tools": HERMES_TOOLS,
                    "tool_choice": "auto",
                },
            )
            resp.raise_for_status()
            data = resp.json()
            return data["choices"][0]["message"]["content"]
        except httpx.ConnectError:
            logger.warning("Hermes gateway not reachable at %s", self.base_url)
            return "Hermes 网关没启动，请先启动它。"
        except Exception:
            logger.exception("Hermes gateway call failed")
            return "抱歉，AI 后端出了点问题。"

    def _build_payload(self, message: str, history: list = None) -> tuple[list, str, int, float]:
        from settings import load_settings

        history = history or []
        hermes_settings = load_settings()["hermes"]
        model = os.getenv("HERMES_MODEL", str(hermes_settings.get("model", "hermes")))
        max_tokens = int(hermes_settings.get("max_tokens", 300))
        temperature = float(hermes_settings.get("temperature", 0.7))
        messages = [
            {
                "role": "system",
                "content": (
                    "你是一个中文桌面语音助手。用户输入来自语音识别，可能有错字、漏字、同音字、断句错误或没有标点。"
                    "请优先理解用户的真实口语意图，不要机械纠正文字。回答要自然、简短、适合直接朗读，通常控制在1到3句话。"
                    "如果用户是在闲聊，就像真人一样轻松回应。如果用户是在询问操作或需要建议，请直接给出最有用的下一步。"
                    "如果用户意图不清楚，先用一句简短的话追问确认。不要编造不确定的信息。"
                    "不要输出表情符号、Markdown、编号列表、代码块或复杂格式，除非用户明确要求。"
                ),
            }
        ]
        for msg in history:
            messages.append({"role": msg["role"], "content": msg["content"]})
        messages.append({"role": "user", "content": message})
        return messages, model, max_tokens, temperature

    async def chat_stream(self, message: str, history: list = None):
        """Yield content deltas from an OpenAI-compatible streaming endpoint."""
        messages, model, max_tokens, temperature = self._build_payload(message, history)
        try:
            async with self._client.stream(
                "POST",
                f"{self.base_url}/v1/chat/completions",
                json={
                    "model": model,
                    "messages": messages,
                    "max_tokens": max_tokens,
                    "temperature": temperature,
                    "stream": True,
                    "tools": HERMES_TOOLS,
                    "tool_choice": "auto",
                },
            ) as resp:
                resp.raise_for_status()
                async for line in resp.aiter_lines():
                    if not line:
                        continue
                    if line.startswith("data:"):
                        line = line[5:].strip()
                    if line == "[DONE]":
                        break
                    try:
                        data = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    choice = (data.get("choices") or [{}])[0]
                    delta = choice.get("delta") or {}
                    content = delta.get("content")
                    if content is None:
                        content = (choice.get("message") or {}).get("content")
                    if content:
                        yield content
        except httpx.ConnectError:
            logger.warning("Hermes gateway not reachable at %s", self.base_url)
            yield "Hermes 网关没启动，请先启动它。"
        except Exception:
            logger.exception("Hermes streaming failed; falling back to non-streaming chat.")
            yield await self.chat(message, history)
