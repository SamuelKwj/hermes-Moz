"""Voice pipeline orchestrator -- record → STT → Hermes LLM → TTS → play."""
import asyncio
import logging
import os
import tempfile
import time
import wave
from pathlib import Path

import numpy as np
import sounddevice as sd

from stt_engine import transcribe
from tts_engine import synthesize
from hermes_client import HermesClient
from settings import load_settings

logger = logging.getLogger(__name__)

SAMPLE_RATE = 16000
CHANNELS = 1
BLOCK_DURATION = 0.05  # 50ms blocks for smooth level updates


def _coerce_device_index(value) -> int | None:
    if value is None or value == "":
        return None
    try:
        index = int(value)
    except (TypeError, ValueError):
        return None
    return index if index >= 0 else None


def _device_has_channels(index: int, kind: str) -> bool:
    channel_key = "max_input_channels" if kind == "input" else "max_output_channels"
    try:
        device = sd.query_devices(index)
    except Exception:
        return False
    return int(device.get(channel_key, 0)) > 0


def _first_available_device(kind: str) -> int | None:
    channel_key = "max_input_channels" if kind == "input" else "max_output_channels"
    try:
        devices = sd.query_devices()
    except Exception:
        logger.exception("Failed to query audio devices.")
        return None

    for index, device in enumerate(devices):
        if int(device.get(channel_key, 0)) > 0:
            return index
    return None


def _valid_sample_rate(device_index: int, kind: str) -> int | None:
    try:
        device = sd.query_devices(device_index)
    except Exception:
        return None

    candidate_rates = [SAMPLE_RATE]
    default_rate = int(float(device.get("default_samplerate", 0) or 0))
    if default_rate and default_rate not in candidate_rates:
        candidate_rates.append(default_rate)

    check_settings = sd.check_input_settings if kind == "input" else sd.check_output_settings
    for sample_rate in candidate_rates:
        try:
            check_settings(
                device=device_index,
                channels=CHANNELS,
                dtype="float32",
                samplerate=sample_rate,
            )
            return sample_rate
        except Exception:
            continue
    return None


def _resolve_audio_device(kind: str, configured_device) -> tuple[int, int] | tuple[None, None]:
    configured_index = _coerce_device_index(configured_device)
    if configured_index is not None:
        sample_rate = _valid_sample_rate(configured_index, kind)
        if sample_rate is not None:
            return configured_index, sample_rate
        logger.warning("Configured %s audio device %s is unavailable.", kind, configured_index)

    default_position = 0 if kind == "input" else 1
    try:
        default_index = _coerce_device_index(sd.default.device[default_position])
    except Exception:
        default_index = None
    if default_index is not None:
        sample_rate = _valid_sample_rate(default_index, kind)
        if sample_rate is not None:
            return default_index, sample_rate

    while True:
        fallback_index = _first_available_device(kind)
        if fallback_index is None:
            return None, None
        sample_rate = _valid_sample_rate(fallback_index, kind)
        if sample_rate is not None:
            logger.info("Using fallback %s audio device %s at %s Hz.", kind, fallback_index, sample_rate)
            return fallback_index, sample_rate
        return None, None


class VoicePipeline:
    def __init__(self):
        self.hermes = HermesClient()
        self._recording = False
        self._frames: list[np.ndarray] = []
        self._level_callback = None
        self._stream = None
        self._record_sample_rate = SAMPLE_RATE

    def set_level_callback(self, cb):
        self._level_callback = cb

    # ── recording ──────────────────────────────────────────────────

    def start_recording(self) -> None:
        if self._recording:
            raise RuntimeError("Already recording")

        settings = load_settings()
        audio_settings = settings["audio"]
        input_device, sample_rate = _resolve_audio_device("input", audio_settings.get("input_device"))
        if input_device is None:
            raise RuntimeError("没有检测到可用麦克风，请在 Windows 声音设置里启用输入设备。")
        self._record_sample_rate = sample_rate or SAMPLE_RATE

        self._frames = []
        self._stream = sd.InputStream(
            samplerate=self._record_sample_rate,
            channels=CHANNELS,
            dtype="float32",
            blocksize=int(self._record_sample_rate * BLOCK_DURATION),
            device=input_device,
            callback=self._audio_callback,
        )
        try:
            self._stream.start()
            self._recording = True
        except Exception:
            self._stream.close()
            self._stream = None
            self._frames = []
            self._recording = False
            raise

    def stop_recording(self) -> str:
        """Stop, save WAV, return file path."""
        if not self._recording and self._stream is None:
            raise ValueError("No active recording")

        self._recording = False
        if self._stream is not None:
            try:
                self._stream.stop()
            finally:
                self._stream.close()
                self._stream = None

        if not self._frames:
            raise ValueError("No audio captured")

        settings = load_settings()
        audio_settings = settings["audio"]
        audio = np.concatenate(self._frames)
        gain = float(audio_settings.get("input_gain", 1.0))
        if gain != 1.0:
            audio = np.clip(audio * gain, -1.0, 1.0)

        if audio_settings.get("vad_enabled", True):
            rms = float(np.sqrt(np.mean(audio**2)))
            threshold = float(audio_settings.get("vad_threshold", 0.012))
            min_seconds = float(audio_settings.get("min_record_seconds", 0.35))
            duration = len(audio) / self._record_sample_rate
            if duration < min_seconds:
                raise ValueError("录音太短，请按住说完再松开。")
            if rms < threshold:
                raise ValueError("没有检测到清晰语音，请靠近麦克风或调低 VAD 阈值。")

        fd, path = tempfile.mkstemp(suffix=".wav", prefix="voice_")
        os.close(fd)
        with wave.open(path, "wb") as wf:
            wf.setnchannels(CHANNELS)
            wf.setsampwidth(2)
            wf.setframerate(self._record_sample_rate)
            wf.writeframes((audio * 32767).astype(np.int16).tobytes())
        return path

    def cancel_recording(self) -> None:
        self._recording = False
        if self._stream is not None:
            try:
                self._stream.stop()
            finally:
                self._stream.close()
                self._stream = None
        self._frames = []

    def _audio_callback(self, indata, frames, time_info, status):
        if status:
            logger.warning("Audio status: %s", status)
        if self._recording:
            self._frames.append(indata.copy())
            rms = float(np.sqrt(np.mean(indata**2)))
            if self._level_callback:
                self._level_callback(rms)

    # ── pipeline ───────────────────────────────────────────────────

    async def run_turn(self, wav_path: str, history: list = None) -> dict:
        """Full turn: transcribe → LLM → synthesize → play."""
        history = history or []
        # 1. STT
        text = await transcribe(wav_path)
        logger.info("STT: %s", text)

        # 2. Hermes LLM
        reply = await self.hermes.chat(text, history)
        logger.info("LLM: %s", reply)

        # 3. TTS
        mp3_path = await synthesize(reply)
        logger.info("TTS saved: %s", mp3_path)

        # 4. Play
        try:
            await self._play_audio(mp3_path)
        finally:
            try:
                os.unlink(mp3_path)
            except OSError:
                pass

        return {"user": text, "assistant": reply}

    async def _play_audio(self, path: str) -> None:
        """Play MP3 via ffmpeg decode + sounddevice."""
        import subprocess
        import tempfile

        settings = load_settings()
        audio_settings = settings["audio"]
        output_device, output_sample_rate = _resolve_audio_device("output", audio_settings.get("output_device"))
        if output_device is None:
            raise RuntimeError("没有检测到可用扬声器，请在 Windows 声音设置里启用输出设备。")
        output_volume = float(audio_settings.get("output_volume", 1.0))

        fd, wav_path = tempfile.mkstemp(suffix=".wav")
        os.close(fd)
        try:
            subprocess.run(
                ["ffmpeg", "-y", "-i", path, "-f", "wav", "-acodec", "pcm_s16le",
                 "-ar", str(output_sample_rate or SAMPLE_RATE), "-ac", "1", wav_path],
                capture_output=True, check=True,
            )

            with wave.open(wav_path, "rb") as wf:
                data = wf.readframes(wf.getnframes())
                audio = np.frombuffer(data, dtype=np.int16).astype(np.float32) / 32767.0
                if output_volume != 1.0:
                    audio = np.clip(audio * output_volume, -1.0, 1.0)
                sd.play(audio, wf.getframerate(), device=output_device)
                sd.wait()
        finally:
            try:
                os.unlink(wav_path)
            except OSError:
                pass

