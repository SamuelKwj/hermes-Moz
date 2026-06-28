"""Voice pipeline orchestrator -- record → STT → Hermes LLM → TTS → play."""
import asyncio
import logging
import os
import re
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
TRIM_FRAME_SECONDS = 0.02
TRIM_PADDING_SECONDS = 0.12
HARD_SENTENCE_RE = re.compile(r"^(.+?[。！？!?；;\n])", re.S)
SOFT_BREAKS = "，,、 "
MIN_TTS_CHARS = 18
MAX_TTS_CHARS = 72


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


def _hostapi_name(index: int) -> str:
    try:
        device = sd.query_devices(index)
        hostapi_index = int(device.get("hostapi", -1))
        return str(sd.query_hostapis(hostapi_index).get("name", ""))
    except Exception:
        return ""


def _is_mapper_device(index: int) -> bool:
    try:
        name = str(sd.query_devices(index).get("name", "")).lower()
    except Exception:
        return True
    mapper_terms = ("mapper", "映射器", "主声音")
    return any(term in name for term in mapper_terms)


def _audio_device_candidates(kind: str, configured_device) -> list[int]:
    channel_key = "max_input_channels" if kind == "input" else "max_output_channels"
    candidates: list[int] = []

    def add(index: int | None):
        if index is None or index in candidates:
            return
        if not _device_has_channels(index, kind):
            return
        candidates.append(index)

    add(_coerce_device_index(configured_device))

    default_position = 0 if kind == "input" else 1
    try:
        add(_coerce_device_index(sd.default.device[default_position]))
    except Exception:
        pass

    try:
        devices = sd.query_devices()
    except Exception:
        logger.exception("Failed to query audio devices.")
        return candidates

    discovered = []
    for index, device in enumerate(devices):
        if int(device.get(channel_key, 0)) <= 0:
            continue
        if _is_mapper_device(index) and configured_device is None:
            continue
        hostapi = _hostapi_name(index)
        if "WASAPI" in hostapi:
            priority = 0
        elif "WDM-KS" in hostapi:
            priority = 1
        elif "DirectSound" in hostapi:
            priority = 2
        else:
            priority = 3
        discovered.append((priority, index))

    for _priority, index in sorted(discovered):
        add(index)
    return candidates


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


def _create_input_stream(device_index: int, sample_rate: int, callback):
    return sd.InputStream(
        samplerate=sample_rate,
        channels=CHANNELS,
        dtype="float32",
        blocksize=int(sample_rate * BLOCK_DURATION),
        device=device_index,
        callback=callback,
    )


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


def _trim_silence(audio: np.ndarray, sample_rate: int, threshold: float) -> np.ndarray:
    """Trim leading and trailing silence using short-window RMS."""
    if audio.size == 0:
        return audio

    frame_size = max(1, int(sample_rate * TRIM_FRAME_SECONDS))
    padding = int(sample_rate * TRIM_PADDING_SECONDS)
    active = []
    for start in range(0, len(audio), frame_size):
        frame = audio[start:start + frame_size]
        if frame.size == 0:
            continue
        rms = float(np.sqrt(np.mean(frame**2)))
        if rms >= threshold:
            active.append((start, min(start + frame_size, len(audio))))

    if not active:
        return audio

    start = max(0, active[0][0] - padding)
    end = min(len(audio), active[-1][1] + padding)
    trimmed = audio[start:end]
    logger.info(
        "Trimmed recording from %.3fs to %.3fs.",
        len(audio) / sample_rate,
        len(trimmed) / sample_rate,
    )
    return trimmed


def _pop_sentence_chunks(buffer: str, force: bool = False) -> tuple[list[str], str]:
    chunks: list[str] = []
    text = buffer
    pending = ""
    while text:
        match = HARD_SENTENCE_RE.match(text)
        if match:
            chunk = match.group(1).strip()
            pending = (pending + chunk).strip()
            if len(pending) >= MIN_TTS_CHARS:
                chunks.append(pending)
                pending = ""
            text = text[len(match.group(1)):].lstrip()
            continue

        candidate = (pending + text).strip()
        if len(candidate) >= MAX_TTS_CHARS:
            split_at = -1
            search_limit = min(len(candidate), MAX_TTS_CHARS)
            for mark in "。！？!?；;\n":
                split_at = max(split_at, candidate.rfind(mark, 0, search_limit))
            if split_at < MIN_TTS_CHARS:
                for mark in SOFT_BREAKS:
                    split_at = max(split_at, candidate.rfind(mark, MIN_TTS_CHARS, search_limit))
            if split_at >= MIN_TTS_CHARS:
                chunks.append(candidate[:split_at + 1].strip())
                text = candidate[split_at + 1:].lstrip()
                pending = ""
                continue

        break

    remainder = (pending + text).strip()
    if force and remainder:
        chunks.append(remainder)
        text = ""
        pending = ""
    return chunks, (pending + text).strip()


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
        self._frames = []
        last_error = None
        for input_device in _audio_device_candidates("input", audio_settings.get("input_device")):
            sample_rate = _valid_sample_rate(input_device, "input")
            if sample_rate is None:
                continue
            stream = None
            try:
                stream = _create_input_stream(input_device, sample_rate, self._audio_callback)
                stream.start()
                self._stream = stream
                self._record_sample_rate = sample_rate
                self._recording = True
                logger.info("Recording from input device %s at %s Hz.", input_device, sample_rate)
                return
            except Exception as exc:
                last_error = exc
                logger.warning("Input device %s failed to open: %s", input_device, exc)
                if stream is not None:
                    try:
                        stream.close()
                    except Exception:
                        pass

        self._stream = None
        self._frames = []
        self._recording = False
        if last_error is not None:
            raise RuntimeError(f"麦克风打开失败，已尝试可用输入设备: {last_error}")
        raise RuntimeError("没有检测到可用麦克风，请在 Windows 声音设置里启用输入设备。")

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
            audio = _trim_silence(audio, self._record_sample_rate, threshold * 0.55)
            trimmed_duration = len(audio) / self._record_sample_rate
            if trimmed_duration < min_seconds:
                raise ValueError("有效语音太短，请按住说完再松开。")

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

    async def run_turn_stream(self, wav_path: str, history: list = None, on_event=None) -> dict:
        """Full turn with streaming LLM text and sentence-by-sentence TTS playback."""
        history = history or []
        if on_event is None:
            async def on_event(_event):
                return None

        text = await transcribe(wav_path)
        logger.info("STT: %s", text)
        await on_event({"type": "user", "text": text})

        tts_queue: asyncio.Queue[str | None] = asyncio.Queue()
        audio_queue: asyncio.Queue[str | None] = asyncio.Queue()
        synth_task = asyncio.create_task(self._tts_synth_worker(tts_queue, audio_queue))
        player_task = asyncio.create_task(self._tts_audio_player(audio_queue))
        reply_parts: list[str] = []
        sentence_buffer = ""

        try:
            async for delta in self.hermes.chat_stream(text, history):
                reply_parts.append(delta)
                sentence_buffer += delta
                await on_event({"type": "assistant_delta", "delta": delta})

                chunks, sentence_buffer = _pop_sentence_chunks(sentence_buffer)
                for chunk in chunks:
                    await tts_queue.put(chunk)

            chunks, sentence_buffer = _pop_sentence_chunks(sentence_buffer, force=True)
            for chunk in chunks:
                await tts_queue.put(chunk)

            reply = "".join(reply_parts).strip() or "我刚才没组织好回复。"
            logger.info("LLM: %s", reply)
            await on_event({"type": "assistant_done", "assistant": reply})
            await tts_queue.put(None)
            await synth_task
            await player_task
            return {"user": text, "assistant": reply}
        except Exception:
            synth_task.cancel()
            player_task.cancel()
            raise

    async def _tts_synth_worker(self, text_queue: asyncio.Queue, audio_queue: asyncio.Queue) -> None:
        try:
            while True:
                text = await text_queue.get()
                if text is None:
                    await audio_queue.put(None)
                    return
                mp3_path = await synthesize(text)
                logger.info("TTS chunk saved: %s", mp3_path)
                await audio_queue.put(mp3_path)
        except Exception:
            await audio_queue.put(None)
            raise

    async def _tts_audio_player(self, queue: asyncio.Queue) -> None:
        while True:
            mp3_path = await queue.get()
            if mp3_path is None:
                return
            try:
                await self._play_audio(mp3_path)
            finally:
                try:
                    os.unlink(mp3_path)
                except OSError:
                    pass

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

