"""TTS engine using edge-tts (free, no API key)."""
import asyncio
import logging
import os
import tempfile

logger = logging.getLogger(__name__)

VOICE = os.getenv("VOICE_TTS_VOICE", "zh-CN-XiaoxiaoNeural")


async def synthesize(text: str) -> str:
    import edge_tts
    from settings import load_settings

    tts_settings = load_settings()["tts"]
    voice = os.getenv("VOICE_TTS_VOICE", tts_settings.get("voice", VOICE))
    rate = tts_settings.get("rate", "+0%")
    volume = tts_settings.get("volume", "+0%")

    fd, path = tempfile.mkstemp(suffix=".mp3", prefix="tts_")
    os.close(fd)

    communicate = edge_tts.Communicate(text, voice, rate=rate, volume=volume)
    await communicate.save(path)
    return path
