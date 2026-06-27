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

logger = logging.getLogger(__name__)

SAMPLE_RATE = 16000
CHANNELS = 1
BLOCK_DURATION = 0.05  # 50ms blocks for smooth level updates


class VoicePipeline:
    def __init__(self):
        self.hermes = HermesClient()
        self._recording = False
        self._frames: list[np.ndarray] = []
        self._level_callback = None

    def set_level_callback(self, cb):
        self._level_callback = cb

    # ── recording ──────────────────────────────────────────────────

    def start_recording(self) -> None:
        self._frames = []
        self._recording = True
        self._stream = sd.InputStream(
            samplerate=SAMPLE_RATE,
            channels=CHANNELS,
            dtype="float32",
            blocksize=int(SAMPLE_RATE * BLOCK_DURATION),
            callback=self._audio_callback,
        )
        self._stream.start()

    def stop_recording(self) -> str:
        """Stop, save WAV, return file path."""
        self._recording = False
        self._stream.stop()
        self._stream.close()

        if not self._frames:
            raise ValueError("No audio captured")

        audio = np.concatenate(self._frames)
        fd, path = tempfile.mkstemp(suffix=".wav", prefix="voice_")
        os.close(fd)
        with wave.open(path, "wb") as wf:
            wf.setnchannels(CHANNELS)
            wf.setsampwidth(2)
            wf.setframerate(SAMPLE_RATE)
            wf.writeframes((audio * 32767).astype(np.int16).tobytes())
        return path

    def _audio_callback(self, indata, frames, time_info, status):
        if status:
            logger.warning("Audio status: %s", status)
        if self._recording:
            self._frames.append(indata.copy())
            rms = float(np.sqrt(np.mean(indata**2)))
            if self._level_callback:
                self._level_callback(rms)

    # ── pipeline ───────────────────────────────────────────────────

    async def run_turn(self, wav_path: str) -> dict:
        """Full turn: transcribe → LLM → synthesize → play."""
        # 1. STT
        text = await transcribe(wav_path)
        logger.info("STT: %s", text)

        # 2. Hermes LLM
        reply = await self.hermes.chat(text)
        logger.info("LLM: %s", reply)

        # 3. TTS
        mp3_path = await synthesize(reply)
        logger.info("TTS saved: %s", mp3_path)

        # 4. Play
        await self._play_audio(mp3_path)

        return {"user": text, "assistant": reply, "audio": mp3_path}

    async def _play_audio(self, path: str) -> None:
        """Play MP3 via ffmpeg decode + sounddevice."""
        import subprocess
        import tempfile

        fd, wav_path = tempfile.mkstemp(suffix=".wav")
        os.close(fd)
        subprocess.run(
            ["ffmpeg", "-y", "-i", path, "-f", "wav", "-acodec", "pcm_s16le",
             "-ar", str(SAMPLE_RATE), "-ac", "1", wav_path],
            capture_output=True, check=True,
        )

        with wave.open(wav_path, "rb") as wf:
            data = wf.readframes(wf.getnframes())
            audio = np.frombuffer(data, dtype=np.int16).astype(np.float32) / 32767.0
            sd.play(audio, wf.getframerate())
            sd.wait()

        os.unlink(wav_path)
