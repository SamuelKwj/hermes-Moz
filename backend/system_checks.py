"""Local runtime checks for productized startup and diagnostics."""
from __future__ import annotations

import subprocess
import shutil
from typing import Any

import sounddevice as sd

from app_runtime import get_app_dir, get_logs_dir, get_model_cache_dir
from hermes_client import choose_hermes_model, parse_model_ids
from settings import SETTINGS_PATH


def list_audio_devices() -> dict[str, list[dict[str, Any]]]:
    devices = sd.query_devices()
    inputs: list[dict[str, Any]] = []
    outputs: list[dict[str, Any]] = []

    for index, device in enumerate(devices):
        item = {
            "index": index,
            "name": str(device.get("name", f"Device {index}")),
            "hostapi": int(device.get("hostapi", -1)),
            "default_samplerate": float(device.get("default_samplerate", 0)),
            "max_input_channels": int(device.get("max_input_channels", 0)),
            "max_output_channels": int(device.get("max_output_channels", 0)),
        }
        if item["max_input_channels"] > 0:
            inputs.append(item)
        if item["max_output_channels"] > 0:
            outputs.append(item)

    return {"inputs": inputs, "outputs": outputs}


def dependency_status() -> dict[str, Any]:
    ffmpeg_path = shutil.which("ffmpeg")
    ffplay_path = shutil.which("ffplay")
    status: dict[str, Any] = {
        "ffmpeg": bool(ffmpeg_path),
        "ffmpeg_path": ffmpeg_path,
        "ffplay": bool(ffplay_path),
        "ffplay_path": ffplay_path,
        "sounddevice": False,
        "faster_whisper": False,
        "edge_tts": False,
        "pywebview": False,
    }

    try:
        import sounddevice  # noqa: F401

        status["sounddevice"] = True
    except Exception:
        status["sounddevice"] = False

    try:
        import faster_whisper  # noqa: F401

        status["faster_whisper"] = True
    except Exception:
        status["faster_whisper"] = False

    try:
        import edge_tts  # noqa: F401

        status["edge_tts"] = True
    except Exception:
        status["edge_tts"] = False

    try:
        import webview  # noqa: F401

        status["pywebview"] = True
    except Exception:
        status["pywebview"] = False

    return status


def accelerator_status() -> dict[str, Any]:
    nvidia_smi_path = shutil.which("nvidia-smi")
    status: dict[str, Any] = {
        "nvidia_smi_path": nvidia_smi_path,
        "nvidia_cuda_available": False,
        "vendor": None,
        "message": "未检测到 NVIDIA CUDA，GPU 档可能会自动回退 CPU。",
    }

    if not nvidia_smi_path:
        return status

    try:
        result = subprocess.run(
            [nvidia_smi_path, "--query-gpu=name,driver_version,memory.total", "--format=csv,noheader"],
            capture_output=True,
            text=True,
            timeout=3,
            check=False,
        )
    except Exception as exc:
        status["message"] = f"检测 NVIDIA CUDA 失败：{exc}"
        return status

    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "").strip()
        status["message"] = detail or "nvidia-smi 无法正常运行，GPU 档可能会自动回退 CPU。"
        return status

    first_gpu = (result.stdout or "").strip().splitlines()[0].strip() if result.stdout.strip() else ""
    status.update(
        {
            "nvidia_cuda_available": True,
            "vendor": "nvidia",
            "device": first_gpu,
            "message": "已检测到 NVIDIA CUDA，可尝试 GPU 快速响应。",
        }
    )
    return status


def runtime_status() -> dict[str, str]:
    return {
        "app_dir": str(get_app_dir()),
        "settings_path": str(SETTINGS_PATH),
        "logs_dir": str(get_logs_dir()),
        "model_cache_dir": str(get_model_cache_dir()),
    }


async def hermes_status(settings: dict[str, Any]) -> dict[str, Any]:
    import httpx

    hermes_settings = settings["hermes"]
    base_url = str(hermes_settings.get("base_url", "http://127.0.0.1:8642")).rstrip("/")
    api_key = str(hermes_settings.get("api_key", "bridge-secret-key"))
    configured_model = str(hermes_settings.get("model", "hermes"))
    result: dict[str, Any] = {
        "base_url": base_url,
        "reachable": False,
        "status_code": None,
        "health_status_code": None,
        "models_status_code": None,
        "models": [],
        "configured_model": configured_model,
        "selected_model": configured_model,
        "auth_ok": False,
        "has_hermes_agent": False,
        "problem": None,
        "message": "Hermes Gateway 尚未检测。",
    }

    try:
        async with httpx.AsyncClient(timeout=2.0) as client:
            try:
                health_response = await client.get(f"{base_url}/health")
            except Exception as exc:
                result.update(
                    {
                        "problem": "gateway_unreachable",
                        "message": f"Hermes Gateway 未启动或不可达：{exc}",
                        "error": str(exc),
                    }
                )
                return result

            result["health_status_code"] = health_response.status_code
            if health_response.status_code != 200:
                result.update(
                    {
                        "problem": "gateway_unhealthy",
                        "message": f"Hermes Gateway /health 返回 {health_response.status_code}，请确认 Hermes 已正常启动。",
                    }
                )
                return result

            try:
                models_response = await client.get(
                    f"{base_url}/v1/models",
                    headers={"Authorization": f"Bearer {api_key}"},
                )
            except Exception as exc:
                result.update(
                    {
                        "problem": "models_unreachable",
                        "message": f"Hermes Gateway 已启动，但 /v1/models 无法访问：{exc}",
                        "error": str(exc),
                    }
                )
                return result

        result["models_status_code"] = models_response.status_code
        result["status_code"] = models_response.status_code

        if models_response.status_code in (401, 403):
            result.update(
                {
                    "problem": "unauthorized",
                    "message": "Hermes Gateway 密钥错误：请确认 API key 与 Hermes 配置一致。",
                }
            )
            return result

        if not 200 <= models_response.status_code < 300:
            result.update(
                {
                    "problem": "models_error",
                    "message": f"Hermes Gateway 模型列表异常：/v1/models 返回 {models_response.status_code}。",
                }
            )
            return result

        result["auth_ok"] = True
        try:
            models = parse_model_ids(models_response.json())
        except Exception as exc:
            result.update(
                {
                    "problem": "models_invalid",
                    "message": f"Hermes Gateway 模型列表解析失败：{exc}",
                    "error": str(exc),
                }
            )
            return result

        result["models"] = models
        result["selected_model"] = choose_hermes_model(configured_model, models)
        result["has_hermes_agent"] = "hermes-agent" in models

        if not models:
            result.update(
                {
                    "problem": "models_empty",
                    "message": "Hermes Gateway 已连接，但 /v1/models 未返回可用模型。",
                }
            )
            return result

        if not result["has_hermes_agent"]:
            result.update(
                {
                    "problem": "missing_hermes_agent",
                    "message": "Hermes Gateway 已连接，但模型列表缺少 hermes-agent。",
                }
            )
            return result

        result.update(
            {
                "reachable": True,
                "message": "Hermes Gateway 正常，已检测到 hermes-agent。",
            }
        )
        return result
    except Exception as exc:
        result.update(
            {
                "problem": "diagnostic_error",
                "message": f"Hermes Gateway 诊断失败：{exc}",
                "error": str(exc),
            }
        )
        return result
