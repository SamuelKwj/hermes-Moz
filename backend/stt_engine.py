"""STT engine using faster-whisper (local, no API key)."""
import asyncio
import logging
import os
from pathlib import Path

logger = logging.getLogger(__name__)

_MODEL = None
_MODEL_SIZE = os.getenv("VOICE_STT_MODEL", "base")
_LANGUAGE = os.getenv("VOICE_STT_LANG", "zh")
# 国内HuggingFace镜像加速
os.environ["HF_ENDPOINT"] = "https://hf-mirror.com"

def _get_model():
    global _MODEL
    if _MODEL is None:
        from faster_whisper import WhisperModel
        device = "cuda" if os.getenv("VOICE_USE_CUDA") == "1" else "cpu"
        compute = "int8" if device == "cpu" else "float16"
        logger.info("Loading faster-whisper %s on %s/%s ...", _MODEL_SIZE, device, compute)
        _MODEL = WhisperModel(_MODEL_SIZE, device=device, compute_type=compute, local_files_only=False)
    return _MODEL


async def transcribe(wav_path: str) -> str:
    model = await asyncio.to_thread(_get_model)
    segments, _info = await asyncio.to_thread(
        model.transcribe, wav_path, beam_size=5, language=_LANGUAGE
    )
    text = " ".join(seg.text.strip() for seg in segments)
    if not text:
        raise RuntimeError("No speech detected")
    return text
