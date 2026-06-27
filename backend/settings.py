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


DEFAULT_SETTINGS: dict[str, Any] = {
    "audio": {
        "input_device": None,
        "output_device": None,
        "input_gain": 1.0,
        "output_volume": 1.0,
        "vad_enabled": True,
        "vad_threshold": 0.012,
        "min_record_seconds": 0.35,
    },
    "stt": {
        "model": "base",
        "language": "zh",
        "device": "auto",
        "compute_type": "auto",
    },
    "tts": {
        "voice": "zh-CN-XiaoxiaoNeural",
        "rate": "+0%",
        "volume": "+0%",
    },
    "hermes": {
        "base_url": "http://127.0.0.1:8642",
        "api_key": "bridge-secret-key",
        "auto_start_gateway": True,
        "request_timeout_seconds": 90,
    },
    "ui": {
        "always_on_top": True,
        "hold_to_talk": True,
        "hotkey": "Space",
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

    return _deep_merge(DEFAULT_SETTINGS, _known_settings(data))


def save_settings(settings: dict[str, Any]) -> dict[str, Any]:
    APP_DIR.mkdir(parents=True, exist_ok=True)
    merged = _deep_merge(DEFAULT_SETTINGS, _known_settings(settings))
    SETTINGS_PATH.write_text(
        json.dumps(merged, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return merged


def patch_settings(patch: dict[str, Any]) -> dict[str, Any]:
    return save_settings(_deep_merge(load_settings(), _known_settings(patch)))
