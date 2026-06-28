"""TTS engine using edge-tts or Windows native voices."""
import asyncio
import logging
import os
import subprocess
import tempfile
from pathlib import Path

logger = logging.getLogger(__name__)

VOICE = os.getenv("VOICE_TTS_VOICE", "zh-CN-XiaoxiaoNeural")
NATIVE_VOICE = os.getenv("VOICE_TTS_NATIVE_VOICE", "Microsoft Huihui Desktop")


async def synthesize(text: str) -> str:
    from settings import load_settings

    tts_settings = load_settings()["tts"]
    mode = os.getenv("VOICE_TTS_MODE", str(tts_settings.get("mode", "edge_mp3")))
    if mode == "windows_native":
        return await asyncio.to_thread(_synthesize_windows_native, text, tts_settings)
    return await _synthesize_edge_mp3(text, tts_settings)


async def _synthesize_edge_mp3(text: str, tts_settings: dict) -> str:
    import edge_tts

    voice = os.getenv("VOICE_TTS_VOICE", tts_settings.get("voice", VOICE))
    rate = tts_settings.get("rate", "+0%")
    volume = tts_settings.get("volume", "+0%")

    fd, path = tempfile.mkstemp(suffix=".mp3", prefix="tts_")
    os.close(fd)

    communicate = edge_tts.Communicate(text, voice, rate=rate, volume=volume)
    await communicate.save(path)
    return path


def _native_rate(edge_rate: str) -> int:
    try:
        value = int(str(edge_rate).strip().replace("%", ""))
    except ValueError:
        return 0
    return max(-10, min(10, round(value / 10)))


def _native_volume(edge_volume: str) -> int:
    try:
        value = int(str(edge_volume).strip().replace("%", ""))
    except ValueError:
        return 100
    return max(0, min(100, 100 + value))


def _synthesize_windows_native(text: str, tts_settings: dict) -> str:
    voice = os.getenv("VOICE_TTS_NATIVE_VOICE", tts_settings.get("native_voice", NATIVE_VOICE))
    rate = _native_rate(str(tts_settings.get("rate", "+0%")))
    volume = _native_volume(str(tts_settings.get("volume", "+0%")))

    fd, wav_path = tempfile.mkstemp(suffix=".wav", prefix="tts_native_")
    os.close(fd)
    text_fd, text_path = tempfile.mkstemp(suffix=".txt", prefix="tts_text_")
    os.close(text_fd)
    script_fd, script_path = tempfile.mkstemp(suffix=".ps1", prefix="tts_sapi_")
    os.close(script_fd)
    Path(text_path).write_text(text, encoding="utf-8")

    script = r"""
param($TextPath, $WavPath, $Voice, $Rate, $Volume)
Add-Type -AssemblyName System.Speech
$s = New-Object System.Speech.Synthesis.SpeechSynthesizer
if ($Voice) {
  try { $s.SelectVoice($Voice) } catch {}
}
$s.Rate = [int]$Rate
$s.Volume = [int]$Volume
$s.SetOutputToWaveFile($WavPath)
$s.Speak((Get-Content -Raw -Encoding UTF8 $TextPath))
$s.Dispose()
"""
    Path(script_path).write_text(script, encoding="utf-8")
    try:
        subprocess.run(
            [
                "powershell",
                "-NoProfile",
                "-ExecutionPolicy",
                "Bypass",
                "-File",
                script_path,
                "-TextPath",
                text_path,
                "-WavPath",
                wav_path,
                "-Voice",
                str(voice),
                "-Rate",
                str(rate),
                "-Volume",
                str(volume),
            ],
            capture_output=True,
            check=True,
        )
        return wav_path
    finally:
        try:
            os.unlink(text_path)
        except OSError:
            pass
        try:
            os.unlink(script_path)
        except OSError:
            pass
