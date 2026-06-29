"""Persistent user settings for the voice desktop widget."""
from __future__ import annotations

import json
import os
from copy import deepcopy
from pathlib import Path
from typing import Any

from app_runtime import get_app_dir

APP_DIR = get_app_dir()
SETTINGS_PATH = APP_DIR / "settings.json"

PERFORMANCE_PROFILES: dict[str, dict[str, Any]] = {
    "lightweight": {
        "label": "轻量兼容",
        "description": "适配绝大多数机器，优先稳定和低资源占用。",
        "stt": {
            "model": "base",
            "device": "cpu",
            "compute_type": "int8",
            "beam_size": 5,
        },
        "hermes": {
            "model": "hermes",
            "max_tokens": 300,
            "temperature": 0.7,
        },
    },
    "extreme_gpu": {
        "label": "GPU 快速响应",
        "description": "面向高性能 NVIDIA GPU，优先低延迟和日常对话准确率。",
        "stt": {
            "model": "small",
            "device": "cuda",
            "compute_type": "float16",
            "beam_size": 5,
        },
        "hermes": {
            "model": "hermes",
            "max_tokens": 500,
            "temperature": 0.7,
        },
    },
}

VALID_PERFORMANCE_PROFILES = {*PERFORMANCE_PROFILES.keys(), "custom"}


DEFAULT_SETTINGS: dict[str, Any] = {
    "performance": {
        "profile": "lightweight",
    },
    "audio": {
        "input_device": None,
        "output_device": None,
        "input_gain": 1.0,
        "output_volume": 1.0,
        "vad_enabled": True,
        "vad_threshold": 0.012,
        "min_record_seconds": 0.35,
        "hands_free_silence_seconds": 0.75,
        "hands_free_wake_silence_seconds": 0.45,
        "hands_free_min_record_seconds": 1.0,
        "hands_free_wake_min_record_seconds": 0.35,
        "hands_free_max_seconds": 12.0,
        "hands_free_trigger_seconds": 0.18,
        "hands_free_resume_delay_seconds": 0.45,
    },
    "stt": {
        "model": "base",
        "language": "zh",
        "device": "cpu",
        "compute_type": "int8",
        "beam_size": 5,
    },
    "tts": {
        "mode": "edge_mp3",
        "voice": "zh-CN-XiaoxiaoNeural",
        "native_voice": "Microsoft Huihui Desktop",
        "rate": "+0%",
        "volume": "+0%",
    },
    "hermes": {
        "base_url": "http://127.0.0.1:8642",
        "api_key": "bridge-secret-key",
        "model": "hermes",
        "max_tokens": 300,
        "temperature": 0.7,
        "auto_start_gateway": True,
        "request_timeout_seconds": 90,
    },
    "ui": {
        "always_on_top": True,
        "hold_to_talk": True,
        "hands_free": False,
        "wake_word_enabled": False,
        "wake_words": "小赫,赫尔墨斯,Hermes",
        "wake_reply": "我在，大王请说。",
        "wake_window_seconds": 20.0,
        "wake_followup_seconds": 60.0,
        "hotkey": "Space",
        "global_hotkey_enabled": True,
        "global_hotkey": "Ctrl+Alt+Space",
        "edge_dock_enabled": False,
        "edge_hover_listen": False,
        "start_minimized": False,
        "launch_on_startup": False,
    },
    "setup": {
        "first_run_complete": False,
    },
}


def _deep_merge(defaults: dict[str, Any], overrides: dict[str, Any]) -> dict[str, Any]:
    merged = deepcopy(defaults)
    for key, value in overrides.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _deep_merge(merged[key], value)
        elif key in merged:
            merged[key] = value
    return merged


def _known_settings(data: dict[str, Any]) -> dict[str, Any]:
    """Keep persisted settings inside the public settings schema."""
    known: dict[str, Any] = {}
    for key, value in data.items():
        default_value = DEFAULT_SETTINGS.get(key)
        if isinstance(default_value, dict) and isinstance(value, dict):
            section = {
                item_key: item_value
                for item_key, item_value in value.items()
                if item_key in default_value
            }
            known[key] = section
        elif key in DEFAULT_SETTINGS:
            known[key] = value
    return known


def apply_performance_profile(settings: dict[str, Any], profile_name: str) -> dict[str, Any]:
    profile = PERFORMANCE_PROFILES.get(profile_name, PERFORMANCE_PROFILES["lightweight"])
    merged = _deep_merge(settings, {
        "performance": {"profile": profile_name if profile_name in PERFORMANCE_PROFILES else "custom"},
        "stt": profile["stt"],
        "hermes": profile["hermes"],
    })
    return _deep_merge(DEFAULT_SETTINGS, _known_settings(merged))


def get_port(default: int = 8765) -> int:
    raw_port = os.getenv("VOICE_WIDGET_PORT", str(default))
    try:
        port = int(raw_port)
    except ValueError:
        return default
    if 1 <= port <= 65535:
        return port
    return default


def get_host() -> str:
    return os.getenv("VOICE_WIDGET_HOST", "127.0.0.1")


def load_settings() -> dict[str, Any]:
    if not SETTINGS_PATH.exists():
        return deepcopy(DEFAULT_SETTINGS)

    try:
        data = json.loads(SETTINGS_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return deepcopy(DEFAULT_SETTINGS)

    if not isinstance(data, dict):
        return deepcopy(DEFAULT_SETTINGS)

    merged = _deep_merge(DEFAULT_SETTINGS, _known_settings(data))
    if "performance" not in data:
        merged = apply_performance_profile(merged, "lightweight")
    return merged


def save_settings(settings: dict[str, Any]) -> dict[str, Any]:
    APP_DIR.mkdir(parents=True, exist_ok=True)
    merged = _deep_merge(DEFAULT_SETTINGS, _known_settings(settings))
    SETTINGS_PATH.write_text(
        json.dumps(merged, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return merged


def patch_settings(patch: dict[str, Any]) -> dict[str, Any]:
    known_patch = _known_settings(patch)
    current = load_settings()
    merged = _deep_merge(current, known_patch)
    raw_performance = patch.get("performance") if isinstance(patch, dict) else None
    apply_profile = bool(raw_performance.get("apply_profile")) if isinstance(raw_performance, dict) else False
    profile_name = (
        known_patch.get("performance", {}).get("profile")
        if isinstance(known_patch.get("performance"), dict)
        else None
    )
    if profile_name not in VALID_PERFORMANCE_PROFILES:
        profile_name = "custom"
        merged["performance"]["profile"] = "custom"
    if apply_profile and profile_name in PERFORMANCE_PROFILES:
        merged = apply_performance_profile(merged, str(profile_name))
    return save_settings(merged)
