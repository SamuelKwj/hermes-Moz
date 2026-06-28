"""STT engine using faster-whisper (local, no API key)."""
import asyncio
import logging
import os

logger = logging.getLogger(__name__)

_MODEL = None
_MODEL_CONFIG = None
_ACTIVE_CONFIG = None
_LAST_ERROR = None
_FORCE_LIGHTWEIGHT_FALLBACK = False
# 国内HuggingFace镜像加速
os.environ.setdefault("HF_ENDPOINT", "https://hf-mirror.com")


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


def _lightweight_fallback(language: str, beam_size: int) -> dict:
    return {
        "model": "base",
        "language": language,
        "device": "cpu",
        "compute_type": "int8",
        "beam_size": beam_size,
        "fallback": True,
        "fallback_reason": "GPU 运行库不可用，已自动回退轻量模式。",
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


def _get_model():
    global _MODEL, _MODEL_CONFIG, _ACTIVE_CONFIG, _LAST_ERROR
    config = _stt_settings()
    model_config = (config["model"], config["device"], config["compute_type"])
    if _MODEL is None or _MODEL_CONFIG != model_config:
        from faster_whisper import WhisperModel

        logger.info(
            "Loading faster-whisper %s on %s/%s ...",
            config["model"],
            config["device"],
            config["compute_type"],
        )
        try:
            _MODEL = WhisperModel(
                config["model"],
                device=config["device"],
                compute_type=config["compute_type"],
                local_files_only=False,
            )
            _MODEL_CONFIG = model_config
            _ACTIVE_CONFIG = {**config, "fallback": False}
            _LAST_ERROR = None
        except Exception as exc:
            _LAST_ERROR = str(exc)
            if config["device"] != "cuda":
                raise

            fallback = _lightweight_fallback(config["language"], config["beam_size"])
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
    }
