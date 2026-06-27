"""Runtime paths, logging, and packaged-resource helpers."""
from __future__ import annotations

import logging
import os
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path


APP_NAME = "HermesVoiceWidget"


def is_frozen() -> bool:
    return bool(getattr(sys, "frozen", False))


def source_root() -> Path:
    return Path(__file__).resolve().parent.parent


def bundle_root() -> Path:
    if is_frozen():
        return Path(getattr(sys, "_MEIPASS", Path(sys.executable).resolve().parent))
    return source_root()


def executable_dir() -> Path:
    if is_frozen():
        return Path(sys.executable).resolve().parent
    return source_root()


def resource_path(*parts: str) -> Path:
    return bundle_root().joinpath(*parts)


def get_app_dir() -> Path:
    base = os.getenv("LOCALAPPDATA") or str(Path.home())
    return Path(base) / APP_NAME


def get_logs_dir() -> Path:
    return get_app_dir() / "logs"


def get_model_cache_dir() -> Path:
    return get_app_dir() / "models"


def get_frontend_index() -> Path:
    return resource_path("frontend", "index.html")


def ensure_runtime_dirs() -> None:
    get_app_dir().mkdir(parents=True, exist_ok=True)
    get_logs_dir().mkdir(parents=True, exist_ok=True)
    get_model_cache_dir().mkdir(parents=True, exist_ok=True)


def configure_model_cache() -> Path:
    cache_dir = get_model_cache_dir()
    cache_dir.mkdir(parents=True, exist_ok=True)
    os.environ.setdefault("HF_HOME", str(cache_dir / "huggingface"))
    os.environ.setdefault("HUGGINGFACE_HUB_CACHE", str(cache_dir / "huggingface" / "hub"))
    os.environ.setdefault("XDG_CACHE_HOME", str(cache_dir / "xdg"))
    return cache_dir


def prepend_bundled_bin_to_path() -> None:
    path_parts = [os.environ.get("PATH", "")]
    if os.name == "nt":
        try:
            import winreg

            registry_paths = (
                (winreg.HKEY_LOCAL_MACHINE, r"SYSTEM\CurrentControlSet\Control\Session Manager\Environment"),
                (winreg.HKEY_CURRENT_USER, "Environment"),
            )
            for hive, key_path in registry_paths:
                with winreg.OpenKey(hive, key_path) as key:
                    persisted, _ = winreg.QueryValueEx(key, "Path")
                    if persisted:
                        path_parts.append(str(persisted))
        except OSError:
            pass

    bin_dir = resource_path("bin")
    if bin_dir.exists():
        path_parts.insert(0, str(bin_dir))

    os.environ["PATH"] = os.pathsep.join(part for part in path_parts if part)


def configure_logging(level: int = logging.INFO) -> Path:
    ensure_runtime_dirs()
    log_path = get_logs_dir() / "app.log"
    root = logging.getLogger()
    root.setLevel(level)

    formatter = logging.Formatter("%(asctime)s [%(levelname)s] [%(name)s] %(message)s")

    has_file_handler = any(
        isinstance(handler, RotatingFileHandler)
        and getattr(handler, "baseFilename", None) == str(log_path)
        for handler in root.handlers
    )
    if not has_file_handler:
        file_handler = RotatingFileHandler(
            log_path,
            maxBytes=2_000_000,
            backupCount=5,
            encoding="utf-8",
        )
        file_handler.setFormatter(formatter)
        root.addHandler(file_handler)

    has_console_handler = any(
        isinstance(handler, logging.StreamHandler)
        and not isinstance(handler, RotatingFileHandler)
        for handler in root.handlers
    )
    if not has_console_handler and not is_frozen():
        console_handler = logging.StreamHandler()
        console_handler.setFormatter(formatter)
        root.addHandler(console_handler)

    return log_path
