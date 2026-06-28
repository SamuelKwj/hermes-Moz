"""STT engine using faster-whisper (local, no API key)."""
import asyncio
import logging
import os
import sys
from pathlib import Path

logger = logging.getLogger(__name__)

_MODEL = None
_MODEL_CONFIG = None
_ACTIVE_CONFIG = None
_LAST_ERROR = None
_FORCE_LIGHTWEIGHT_FALLBACK = False
_DLL_DIRECTORY_HANDLES = []
_CUDA_DLL_DIRS = []
# 国内HuggingFace镜像加速
os.environ.setdefault("HF_ENDPOINT", "https://hf-mirror.com")


def _candidate_cuda_dll_dirs() -> list[Path]:
    candidates: list[Path] = []
    for item in os.getenv("VOICE_CUDA_DLL_DIRS", "").split(os.pathsep):
        if item.strip():
            candidates.append(Path(item.strip()))

    cuda_path = os.getenv("CUDA_PATH")
    if cuda_path:
        candidates.append(Path(cuda_path) / "bin")

    candidates.append(Path(sys.prefix) / "Lib" / "site-packages" / "ctranslate2")

    user_profile = Path.home()
    local_app_data = Path(os.getenv("LOCALAPPDATA", user_profile / "AppData" / "Local"))
    candidates.extend(
        [
            user_profile
            / ".lmstudio"
            / "extensions"
            / "backends"
            / "vendor"
            / "win-llama-cuda12-vendor-v2",
            local_app_data
            / "LM Studio"
            / "extensions"
            / "backends"
            / "vendor"
            / "win-llama-cuda12-vendor-v2",
        ]
    )
    return candidates


def _add_cuda_dll_directories() -> list[str]:
    if os.name != "nt":
        return []

    added: list[str] = []
    seen = {str(path).lower() for path in _CUDA_DLL_DIRS}
    for path in _candidate_cuda_dll_dirs():
        if not path.exists() or not path.is_dir():
            continue
        has_cuda_dll = any((path / name).exists() for name in ("cublas64_12.dll", "cudnn64_9.dll"))
        if not has_cuda_dll:
            continue
        key = str(path).lower()
        if key in seen:
            continue
        try:
            handle = os.add_dll_directory(str(path))
        except OSError:
            logger.exception("Failed to add CUDA DLL directory: %s", path)
            continue
        _DLL_DIRECTORY_HANDLES.append(handle)
        _CUDA_DLL_DIRS.append(path)
        seen.add(key)
        added.append(str(path))
        logger.info("Added CUDA DLL directory: %s", path)
    return added


def _local_stt_model_path(model_name: str) -> Path | None:
    local_names = {
        "small": "faster-whisper-small",
        "faster-whisper-small": "faster-whisper-small",
        "large-v3": "faster-whisper-large-v3",
        "faster-whisper-large-v3": "faster-whisper-large-v3",
    }
    dirname = local_names.get(model_name)
    if not dirname:
        return None

    try:
        from app_runtime import get_model_cache_dir
    except Exception:
        return None

    path = get_model_cache_dir() / dirname
    if (path / "model.bin").exists() and (path / "config.json").exists():
        return path
    return None


def _stt_settings() -> dict:
    from settings import load_settings

    settings = load_settings()["stt"]
    model_size = os.getenv("VOICE_STT_MODEL", str(settings.get("model", "base")))
    language = os.getenv("VOICE_STT_LANG", str(settings.get("language", "zh")))
    device = os.getenv("VOICE_STT_DEVICE", str(settings.get("device", "cpu")))
    if os.getenv("VOICE_USE_CUDA") == "1":
        device = "cuda"
    compute_type = os.getenv("VOICE_STT_COMPUTE_TYPE", str(settings.get("compute_type", "int8")))
    beam_size = int(settings.get("beam_size", 5))
    if device == "auto":
        device = "cuda" if os.getenv("VOICE_USE_CUDA") == "1" else "cpu"
    if compute_type == "auto":
        compute_type = "float16" if device == "cuda" else "int8"
    if _FORCE_LIGHTWEIGHT_FALLBACK:
        return _lightweight_fallback(language, beam_size)
    return {
        "model": model_size,
        "language": language,
        "device": device,
        "compute_type": compute_type,
        "beam_size": beam_size,
    }


def _lightweight_fallback(language: str, beam_size: int, reason: str | None = None) -> dict:
    return {
        "model": "base",
        "language": language,
        "device": "cpu",
        "compute_type": "int8",
        "beam_size": beam_size,
        "fallback": True,
        "fallback_reason": reason or "GPU 模式暂不可用，已自动回退轻量模式。",
    }


def reset_model() -> None:
    global _MODEL, _MODEL_CONFIG, _ACTIVE_CONFIG, _LAST_ERROR, _FORCE_LIGHTWEIGHT_FALLBACK
    _MODEL = None
    _MODEL_CONFIG = None
    _ACTIVE_CONFIG = None
    _LAST_ERROR = None
    _FORCE_LIGHTWEIGHT_FALLBACK = False


def _force_lightweight_fallback(reason: str) -> None:
    global _MODEL, _MODEL_CONFIG, _ACTIVE_CONFIG, _LAST_ERROR, _FORCE_LIGHTWEIGHT_FALLBACK
    _MODEL = None
    _MODEL_CONFIG = None
    _ACTIVE_CONFIG = None
    _LAST_ERROR = reason
    _FORCE_LIGHTWEIGHT_FALLBACK = True


def _gpu_fallback_reason(error_message: str) -> str:
    message = error_message.lower()
    if "cublas" in message or "cudnn" in message or ".dll" in message:
        return "GPU 运行库不可用，已自动回退轻量模式。"
    if "hub" in message or "cache" in message or "connection" in message or "internet" in message:
        return "GPU STT 模型未下载成功，已自动回退轻量模式。"
    return "GPU 模式启动失败，已自动回退轻量模式。"


def _get_model():
    global _MODEL, _MODEL_CONFIG, _ACTIVE_CONFIG, _LAST_ERROR
    config = _stt_settings()
    local_model_path = _local_stt_model_path(config["model"])
    model_target = str(local_model_path) if local_model_path else config["model"]
    model_config = (model_target, config["device"], config["compute_type"])
    if _MODEL is None or _MODEL_CONFIG != model_config:
        if config["device"] == "cuda":
            _add_cuda_dll_directories()
        from faster_whisper import WhisperModel

        logger.info(
            "Loading faster-whisper %s on %s/%s ...",
            model_target,
            config["device"],
            config["compute_type"],
        )
        try:
            _MODEL = WhisperModel(
                model_target,
                device=config["device"],
                compute_type=config["compute_type"],
                local_files_only=False,
            )
            _MODEL_CONFIG = model_config
            active_config = {**config, "fallback": False}
            if local_model_path:
                active_config["model_path"] = str(local_model_path)
            _ACTIVE_CONFIG = active_config
            _LAST_ERROR = None
        except Exception as exc:
            _LAST_ERROR = str(exc)
            if config["device"] != "cuda":
                raise

            fallback = _lightweight_fallback(
                config["language"],
                config["beam_size"],
                _gpu_fallback_reason(_LAST_ERROR),
            )
            logger.exception("GPU STT model failed to load. Falling back to lightweight STT.")
            _MODEL = WhisperModel(
                fallback["model"],
                device=fallback["device"],
                compute_type=fallback["compute_type"],
                local_files_only=False,
            )
            _MODEL_CONFIG = (fallback["model"], fallback["device"], fallback["compute_type"])
            _ACTIVE_CONFIG = fallback
    return _MODEL


async def transcribe(wav_path: str) -> str:
    config = _stt_settings()
    model = await asyncio.to_thread(_get_model)
    active_config = _ACTIVE_CONFIG or config
    try:
        segments, _info = await asyncio.to_thread(
            model.transcribe,
            wav_path,
            beam_size=active_config["beam_size"],
            language=active_config["language"],
        )
    except RuntimeError as exc:
        message = str(exc)
        if active_config.get("device") != "cuda" or "cublas" not in message.lower():
            raise
        logger.exception("GPU STT failed during transcription. Retrying with lightweight STT.")
        _force_lightweight_fallback("GPU 运行库不可用，已自动回退轻量模式。")
        model = await asyncio.to_thread(_get_model)
        active_config = _ACTIVE_CONFIG or _stt_settings()
        segments, _info = await asyncio.to_thread(
            model.transcribe,
            wav_path,
            beam_size=active_config["beam_size"],
            language=active_config["language"],
        )
    text = " ".join(seg.text.strip() for seg in segments)
    if not text:
        raise RuntimeError("No speech detected")
    return text


def status() -> dict:
    config = _stt_settings()
    active = _ACTIVE_CONFIG or config
    return {
        **active,
        "configured": config,
        "loaded": _MODEL is not None,
        "last_error": _LAST_ERROR,
        "cuda_dll_dirs": [str(path) for path in _CUDA_DLL_DIRS],
    }
