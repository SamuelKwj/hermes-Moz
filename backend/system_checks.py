"""Local runtime checks for productized startup and diagnostics."""
from __future__ import annotations

import subprocess
import shutil
from typing import Any

import sounddevice as sd

from app_runtime import get_app_dir, get_logs_dir, get_model_cache_dir
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

    try:
        async with httpx.AsyncClient(timeout=2.0) as client:
            response = await client.get(
                f"{base_url}/v1/models",
                headers={"Authorization": f"Bearer {api_key}"},
            )
        reachable = response.status_code < 500
        return {
            "base_url": base_url,
            "reachable": reachable,
            "status_code": response.status_code,
        }
    except Exception as exc:
        return {
            "base_url": base_url,
            "reachable": False,
            "error": str(exc),
        }
