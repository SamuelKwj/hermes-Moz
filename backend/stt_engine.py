"""STT engine using faster-whisper (local, no API key)."""
import asyncio
import logging
import os

logger = logging.getLogger(__name__)

_MODEL = None
_MODEL_KEY = None


def _get_model():
    global _MODEL, _MODEL_KEY
    from app_runtime import configure_model_cache
    from settings import load_settings

    model_cache_dir = configure_model_cache()
    stt_settings = load_settings()["stt"]
    model_size = os.getenv("VOICE_STT_MODEL", stt_settings.get("model", "base"))
    requested_device = os.getenv("VOICE_USE_DEVICE", stt_settings.get("device", "auto"))
    device = "cuda" if requested_device == "cuda" or os.getenv("VOICE_USE_CUDA") == "1" else "cpu"
    compute = stt_settings.get("compute_type", "auto")
    if compute == "auto":
        compute = "int8" if device == "cpu" else "float16"

    model_key = (model_size, device, compute)
    if _MODEL is None or _MODEL_KEY != model_key:
        from faster_whisper import WhisperModel

        logger.info("Loading faster-whisper %s on %s/%s ...", model_size, device, compute)
        _MODEL = WhisperModel(
            model_size,
            device=device,
            compute_type=compute,
            download_root=str(model_cache_dir / "faster-whisper"),
        )
        _MODEL_KEY = model_key
    return _MODEL


async def transcribe(wav_path: str) -> str:
    from settings import load_settings

    language = os.getenv("VOICE_STT_LANG", load_settings()["stt"].get("language", "zh"))
    model = await asyncio.to_thread(_get_model)
    segments, _info = await asyncio.to_thread(
        model.transcribe, wav_path, beam_size=5, language=language
    )
    text = " ".join(seg.text.strip() for seg in segments)
    if not text:
        raise RuntimeError("No speech detected")
    return text
