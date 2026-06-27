"""STT engine using faster-whisper (local, no API key)."""
import asyncio
import logging
import os

logger = logging.getLogger(__name__)

_MODEL = None
_MODEL_CONFIG = None
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
    return {
        "model": model_size,
        "language": language,
        "device": device,
        "compute_type": compute_type,
        "beam_size": beam_size,
    }


def reset_model() -> None:
    global _MODEL, _MODEL_CONFIG
    _MODEL = None
    _MODEL_CONFIG = None


def _get_model():
    global _MODEL, _MODEL_CONFIG
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
        _MODEL = WhisperModel(
            config["model"],
            device=config["device"],
            compute_type=config["compute_type"],
            local_files_only=False,
        )
        _MODEL_CONFIG = model_config
    return _MODEL


async def transcribe(wav_path: str) -> str:
    config = _stt_settings()
    model = await asyncio.to_thread(_get_model)
    segments, _info = await asyncio.to_thread(
        model.transcribe,
        wav_path,
        beam_size=config["beam_size"],
        language=config["language"],
    )
    text = " ".join(seg.text.strip() for seg in segments)
    if not text:
        raise RuntimeError("No speech detected")
    return text


def status() -> dict:
    config = _stt_settings()
    return {
        **config,
        "loaded": _MODEL is not None,
    }
