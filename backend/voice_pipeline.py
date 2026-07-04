"""Voice pipeline orchestrator -- record → STT → Hermes LLM → TTS → play."""
import asyncio
from collections import deque
from dataclasses import dataclass
import logging
import os
import queue
import re
import subprocess
import tempfile
import threading
import time
import wave
from pathlib import Path

import numpy as np
import sounddevice as sd

from stt_engine import transcribe
from tts_engine import synthesize
from hermes_client import HermesClient
from settings import load_settings
from subprocess_utils import hidden_window_kwargs

logger = logging.getLogger(__name__)

SAMPLE_RATE = 16000
CHANNELS = 1
BLOCK_DURATION = 0.05  # 50ms blocks for smooth level updates
TRIM_FRAME_SECONDS = 0.02
TRIM_PADDING_SECONDS = 0.12
HARD_SENTENCE_RE = re.compile(r"^(.+?[。！？!?；;\n])", re.S)
SOFT_BREAKS = "，,、 "
FIRST_TTS_MIN_CHARS = 20
NEXT_TTS_MIN_CHARS = 36
MAX_TTS_CHARS = 80
FAST_FIRST_TTS_MIN_CHARS = 12
FAST_NEXT_TTS_MIN_CHARS = 28
FAST_MAX_TTS_CHARS = 70
_LAST_TURN_LATENCY: dict[str, int | None] | None = None


@dataclass(frozen=True)
class TtsChunkConfig:
    first_chunk_chars: int
    next_chunk_chars: int
    max_chunk_chars: int


STANDARD_TTS_CHUNK_CONFIG = TtsChunkConfig(FIRST_TTS_MIN_CHARS, NEXT_TTS_MIN_CHARS, MAX_TTS_CHARS)
FAST_TTS_CHUNK_CONFIG = TtsChunkConfig(FAST_FIRST_TTS_MIN_CHARS, FAST_NEXT_TTS_MIN_CHARS, FAST_MAX_TTS_CHARS)


def _bounded_int(value, fallback: int, minimum: int, maximum: int) -> int:
    try:
        numeric = int(value)
    except (TypeError, ValueError):
        return fallback
    return max(minimum, min(maximum, numeric))


def tts_chunk_config(settings: dict | None = None) -> TtsChunkConfig:
    settings = load_settings() if settings is None else settings
    tts_settings = settings.get("tts", {}) if isinstance(settings, dict) else {}
    if str(tts_settings.get("response_mode", "fast")) != "fast":
        return STANDARD_TTS_CHUNK_CONFIG
    first_chars = _bounded_int(tts_settings.get("fast_first_chunk_chars"), FAST_FIRST_TTS_MIN_CHARS, 8, 30)
    next_chars = _bounded_int(tts_settings.get("fast_next_chunk_chars"), FAST_NEXT_TTS_MIN_CHARS, first_chars, 48)
    max_chars = _bounded_int(tts_settings.get("fast_max_chunk_chars"), FAST_MAX_TTS_CHARS, next_chars, 120)
    return TtsChunkConfig(first_chars, next_chars, max_chars)


def _bounded_float(value, fallback: float, minimum: float, maximum: float) -> float:
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return fallback
    return max(minimum, min(maximum, numeric))


def reset_last_turn_latency() -> None:
    global _LAST_TURN_LATENCY
    _LAST_TURN_LATENCY = None


def last_turn_latency() -> dict[str, int | None] | None:
    if _LAST_TURN_LATENCY is None:
        return None
    return dict(_LAST_TURN_LATENCY)


def _elapsed_ms(started_at: float, marker: float | None) -> int | None:
    if marker is None:
        return None
    return max(0, int(round((marker - started_at) * 1000)))


def _record_last_turn_latency(
    started_at: float,
    stt_done_at: float | None,
    llm_first_token_at: float | None,
    tts_first_chunk_at: float | None,
    playback_start_at: float | None,
    finished_at: float,
) -> None:
    global _LAST_TURN_LATENCY
    latency = {
        "stt_ms": _elapsed_ms(started_at, stt_done_at),
        "llm_first_token_ms": _elapsed_ms(started_at, llm_first_token_at),
        "tts_first_chunk_ms": _elapsed_ms(started_at, tts_first_chunk_at),
        "playback_start_ms": _elapsed_ms(started_at, playback_start_at),
        "total_ms": _elapsed_ms(started_at, finished_at),
    }
    _LAST_TURN_LATENCY = latency
    logger.info(
        "Turn latency: STT %sms / first token %sms / first audio %sms / playback %sms / total %sms",
        latency["stt_ms"],
        latency["llm_first_token_ms"],
        latency["tts_first_chunk_ms"],
        latency["playback_start_ms"],
        latency["total_ms"],
    )


def _normalize_wake_text(text: str) -> str:
    return re.sub(r"[\s,，。.!！?？、:：;；\"'“”‘’（）()\-_/\\]+", "", text).lower()


def _wake_words_from_settings(settings: dict) -> list[str]:
    raw = str(settings.get("ui", {}).get("wake_words", "小赫,赫尔墨斯,Hermes"))
    words = [word.strip() for word in re.split(r"[,，\n;；]+", raw) if word.strip()]
    return words or ["小赫", "赫尔墨斯", "Hermes"]


def _apply_wake_gate(text: str, settings: dict) -> tuple[bool, str, bool]:
    normalized = _normalize_wake_text(text)
    aliases = {
        "小赫": [
            "小赫", "小河", "小和", "小何", "小鹤", "小核", "小盒", "小贺", "小賀",
            "小黑", "小嘿", "小嗨", "小海", "小孩", "小禾", "晓赫", "小裤", "小褲",
            "和", "河", "何", "赫", "鹤",
        ],
        "赫尔墨斯": ["赫尔墨斯", "荷尔墨斯", "赫尔莫斯"],
        "hermes": ["hermes"],
    }
    candidates: list[tuple[str, bool]] = []
    for word in _wake_words_from_settings(settings):
        candidates.append((word, False))
        candidates.extend((alias, len(_normalize_wake_text(alias)) <= 1) for alias in aliases.get(_normalize_wake_text(word), []))

    matched = False
    matched_word = ""
    for word, leading_only in candidates:
        wake = _normalize_wake_text(word)
        if not wake:
            continue
        if leading_only and not normalized.startswith(wake):
            continue
        if leading_only and len(normalized) > len(wake) * 2:
            continue
        if leading_only or wake in normalized:
            matched = True
            matched_word = word
            break

    if not matched:
        return False, text, False

    stripped = text
    for word, leading_only in candidates:
        wake = _normalize_wake_text(word)
        if not wake:
            continue
        while True:
            stripped_next = re.sub(re.escape(word), "", stripped, count=1, flags=re.IGNORECASE).strip()
            if stripped_next == stripped and leading_only and _normalize_wake_text(stripped).startswith(wake):
                stripped_next = stripped[1:].strip()
            if stripped_next == stripped:
                break
            stripped = stripped_next
            if not _normalize_wake_text(stripped).startswith(wake):
                break

    if _normalize_wake_text(stripped) == _normalize_wake_text(matched_word):
        stripped = ""
    command = stripped.strip(" ，,。.!！?？、")
    return True, command, not bool(command)


def _is_stt_hallucination(text: str) -> bool:
    normalized = _normalize_wake_text(text)
    if not normalized:
        return True
    hallucinations = (
        "字幕",
        "amaraorg",
        "志愿者",
        "志願者",
        "社群提供",
        "谢谢观看",
        "謝謝觀看",
        "感谢观看",
        "感謝觀看",
        "请不吝点赞订阅转发打赏支持明镜与点点栏目",
    )
    return any(item in normalized for item in hallucinations)


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


def _pop_sentence_chunks(
    buffer: str,
    force: bool = False,
    first_chunk: bool = False,
    chunk_config: TtsChunkConfig | None = None,
) -> tuple[list[str], str]:
    chunks: list[str] = []
    text = buffer
    pending = ""
    chunk_config = chunk_config or STANDARD_TTS_CHUNK_CONFIG
    min_chars = chunk_config.first_chunk_chars if first_chunk else chunk_config.next_chunk_chars
    while text:
        match = HARD_SENTENCE_RE.match(text)
        if match:
            chunk = match.group(1).strip()
            pending = (pending + chunk).strip()
            if len(pending) >= min_chars:
                chunks.append(pending)
                pending = ""
            text = text[len(match.group(1)):].lstrip()
            continue

        candidate = (pending + text).strip()
        if len(candidate) >= chunk_config.max_chunk_chars:
            split_at = -1
            search_limit = min(len(candidate), chunk_config.max_chunk_chars)
            for mark in "。！？!?；;\n":
                split_at = max(split_at, candidate.rfind(mark, 0, search_limit))
            if split_at < min_chars:
                for mark in SOFT_BREAKS:
                    split_at = max(split_at, candidate.rfind(mark, min_chars, search_limit))
            if split_at >= min_chars:
                chunks.append(candidate[:split_at + 1].strip())
                text = candidate[split_at + 1:].lstrip()
                pending = ""
                continue

        break

    remainder = (pending + text).strip()
    if force and remainder:
        if chunks and len(remainder) < chunk_config.next_chunk_chars // 2:
            chunks[-1] = (chunks[-1] + remainder).strip()
        else:
            chunks.append(remainder)
        text = ""
        pending = ""
    return chunks, (pending + text).strip()


def _decode_audio_file(path: str, sample_rate: int) -> np.ndarray:
    """Decode any TTS output format to mono float32 PCM for continuous playback."""
    result = subprocess.run(
        [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-i",
            path,
            "-f",
            "f32le",
            "-acodec",
            "pcm_f32le",
            "-ar",
            str(sample_rate),
            "-ac",
            "1",
            "pipe:1",
        ],
        capture_output=True,
        check=True,
        **hidden_window_kwargs(),
    )
    return np.frombuffer(result.stdout, dtype=np.float32).copy()


def _ack_tone(sample_rate: int, volume: float) -> np.ndarray:
    duration_seconds = 0.09
    frequency_hz = 880.0
    samples = max(1, int(sample_rate * duration_seconds))
    timeline = np.arange(samples, dtype=np.float32) / float(sample_rate)
    tone = np.sin(2 * np.pi * frequency_hz * timeline).astype(np.float32)
    fade_samples = min(samples // 2, max(1, int(sample_rate * 0.012)))
    envelope = np.ones(samples, dtype=np.float32)
    envelope[:fade_samples] = np.linspace(0.0, 1.0, fade_samples, dtype=np.float32)
    envelope[-fade_samples:] = np.linspace(1.0, 0.0, fade_samples, dtype=np.float32)
    return np.clip(tone * envelope * float(volume), -1.0, 1.0).astype(np.float32)


class _ContinuousPcmPlayer:
    def __init__(self, device: int, sample_rate: int, volume: float):
        self.device = device
        self.sample_rate = sample_rate
        self.volume = volume
        self._queue: queue.Queue[np.ndarray | None] = queue.Queue(maxsize=16)
        self._done = threading.Event()
        self._current = np.empty(0, dtype=np.float32)
        self._position = 0
        self._playback_started = False
        self.on_playback_start = None

    def put(self, audio: np.ndarray) -> None:
        if self._done.is_set():
            return
        if self.volume != 1.0:
            audio = np.clip(audio * self.volume, -1.0, 1.0)
        audio = audio.astype(np.float32, copy=False)
        while not self._done.is_set():
            try:
                self._queue.put(audio, timeout=0.1)
                return
            except queue.Full:
                continue

    def finish(self) -> None:
        if self._done.is_set():
            return
        while not self._done.is_set():
            try:
                self._queue.put(None, timeout=0.1)
                return
            except queue.Full:
                continue

    def stop(self) -> None:
        self._done.set()
        try:
            while True:
                self._queue.get_nowait()
        except queue.Empty:
            pass

    def run(self) -> None:
        blocksize = max(256, int(self.sample_rate * 0.02))
        with sd.OutputStream(
            samplerate=self.sample_rate,
            channels=CHANNELS,
            dtype="float32",
            blocksize=blocksize,
            device=self.device,
            callback=self._callback,
        ):
            self._done.wait()

    def _callback(self, outdata, frames, time_info, status):
        if status:
            logger.warning("Output status: %s", status)
        if self._done.is_set():
            outdata[:] = np.zeros((frames, 1), dtype=np.float32)
            return

        output = np.zeros(frames, dtype=np.float32)
        filled = 0
        while filled < frames:
            if self._position >= len(self._current):
                try:
                    item = self._queue.get_nowait()
                except queue.Empty:
                    break
                if item is None:
                    self._done.set()
                    break
                self._current = item
                self._position = 0

            remaining = len(self._current) - self._position
            take = min(frames - filled, remaining)
            if take <= 0:
                break
            if not self._playback_started:
                self._playback_started = True
                if self.on_playback_start is not None:
                    self.on_playback_start()
            output[filled:filled + take] = self._current[self._position:self._position + take]
            self._position += take
            filled += take

        outdata[:] = output.reshape(-1, 1)


class VoicePipeline:
    def __init__(self):
        self.hermes = HermesClient()
        self._recording = False
        self._frames: list[np.ndarray] = []
        self._level_callback = None
        self._stream = None
        self._record_sample_rate = SAMPLE_RATE
        self._active_player: _ContinuousPcmPlayer | None = None
        self._hands_free_stream = None
        self._hands_free_active = False
        self._hands_free_sample_rate = SAMPLE_RATE
        self._hands_free_frames: list[np.ndarray] = []
        self._hands_free_preroll = deque()
        self._hands_free_silence_seconds = 0.0
        self._hands_free_voice_seconds = 0.0
        self._hands_free_started_at = 0.0
        self._hands_free_paused_until = 0.0
        self._hands_free_audio_settings = {}
        self._hands_free_ui_settings = {}
        self._hands_free_on_submit = None
        self._hands_free_on_speech_start = None
        self._hands_free_on_state = None

    def set_level_callback(self, cb):
        self._level_callback = cb

    # ── recording ──────────────────────────────────────────────────

    def start_recording(self) -> None:
        if self._recording:
            raise RuntimeError("Already recording")
        if self._hands_free_stream is not None:
            raise RuntimeError("免按键监听已开启，请先关闭后再按住录音。")

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

    def interrupt_playback(self) -> None:
        player = self._active_player
        if player is not None:
            player.stop()

    async def play_fast_feedback(self, settings: dict | None = None) -> bool:
        settings = load_settings() if settings is None else settings
        tts_settings = settings.get("tts", {})
        if str(tts_settings.get("response_mode", "fast")) != "fast":
            return False
        if not bool(tts_settings.get("ack_sound_enabled", True)):
            return False

        audio_settings = settings.get("audio", {})
        output_device, output_sample_rate = _resolve_audio_device("output", audio_settings.get("output_device"))
        if output_device is None:
            logger.warning("Fast feedback skipped: no output device available.")
            return False

        sample_rate = output_sample_rate or SAMPLE_RATE
        output_volume = _bounded_float(audio_settings.get("output_volume"), 1.0, 0.0, 2.0)
        ack_volume = _bounded_float(tts_settings.get("ack_sound_volume"), 0.25, 0.0, 1.0)
        player = _ContinuousPcmPlayer(output_device, sample_rate, output_volume)
        self._active_player = player
        try:
            await asyncio.to_thread(player.put, _ack_tone(sample_rate, ack_volume))
            player.finish()
            await asyncio.to_thread(player.run)
            return True
        except Exception:
            logger.exception("Fast feedback sound failed.")
            return False
        finally:
            if self._active_player is player:
                self._active_player = None

    def pause_hands_free(self, seconds: float = 0.5, replace: bool = False) -> None:
        if self._hands_free_stream is None:
            return
        paused_until = time.monotonic() + seconds
        if replace:
            self._hands_free_paused_until = paused_until
        else:
            self._hands_free_paused_until = max(self._hands_free_paused_until, paused_until)
        self._hands_free_reset()

    def update_hands_free_settings(self, settings: dict | None = None) -> None:
        settings = settings or load_settings()
        self._hands_free_audio_settings = dict(settings.get("audio", {}))
        self._hands_free_ui_settings = dict(settings.get("ui", {}))

    def start_hands_free(self, on_submit, on_speech_start=None, on_state=None) -> None:
        if self._hands_free_stream is not None:
            return
        if self._recording:
            raise RuntimeError("正在手动录音，无法开启免按键监听。")

        settings = load_settings()
        audio_settings = settings["audio"]
        self.update_hands_free_settings(settings)
        self._hands_free_on_submit = on_submit
        self._hands_free_on_speech_start = on_speech_start
        self._hands_free_on_state = on_state
        self._hands_free_reset()
        last_error = None

        for input_device in _audio_device_candidates("input", audio_settings.get("input_device")):
            sample_rate = _valid_sample_rate(input_device, "input")
            if sample_rate is None:
                continue
            stream = None
            try:
                self._hands_free_sample_rate = sample_rate
                stream = _create_input_stream(input_device, sample_rate, self._hands_free_audio_callback)
                stream.start()
                self._hands_free_stream = stream
                logger.info("Hands-free listening from input device %s at %s Hz.", input_device, sample_rate)
                if on_state:
                    on_state("listening")
                return
            except Exception as exc:
                last_error = exc
                logger.warning("Hands-free input device %s failed to open: %s", input_device, exc)
                if stream is not None:
                    try:
                        stream.close()
                    except Exception:
                        pass

        self._hands_free_clear_callbacks()
        if last_error is not None:
            raise RuntimeError(f"免按键监听打开失败，已尝试可用输入设备: {last_error}")
        raise RuntimeError("没有检测到可用麦克风，无法开启免按键监听。")

    def stop_hands_free(self) -> None:
        if self._hands_free_stream is not None:
            try:
                self._hands_free_stream.stop()
            finally:
                self._hands_free_stream.close()
        self._hands_free_stream = None
        self._hands_free_reset()
        self._hands_free_clear_callbacks()

    def _hands_free_clear_callbacks(self) -> None:
        self._hands_free_on_submit = None
        self._hands_free_on_speech_start = None
        self._hands_free_on_state = None

    def _hands_free_reset(self) -> None:
        self._hands_free_active = False
        self._hands_free_frames = []
        self._hands_free_preroll.clear()
        self._hands_free_silence_seconds = 0.0
        self._hands_free_voice_seconds = 0.0
        self._hands_free_started_at = 0.0

    def _hands_free_audio_callback(self, indata, frames, time_info, status):
        if status:
            logger.warning("Hands-free audio status: %s", status)

        frame = indata.copy()
        rms = float(np.sqrt(np.mean(frame**2)))
        if self._level_callback:
            self._level_callback(rms)

        audio_settings = self._hands_free_audio_settings
        if time.monotonic() < self._hands_free_paused_until:
            self._hands_free_reset()
            return

        ui_settings = self._hands_free_ui_settings
        threshold = float(audio_settings.get("vad_threshold", 0.012))
        silence_key = "hands_free_wake_silence_seconds" if ui_settings.get("wake_word_enabled") else "hands_free_silence_seconds"
        silence_seconds = float(audio_settings.get(silence_key, 0.45 if silence_key == "hands_free_wake_silence_seconds" else 0.75))
        trigger_seconds = float(audio_settings.get("hands_free_trigger_seconds", 0.18))
        max_seconds = float(audio_settings.get("hands_free_max_seconds", 12.0))
        frame_seconds = frames / self._hands_free_sample_rate
        is_voice = rms >= threshold

        if not self._hands_free_active:
            self._hands_free_preroll.append(frame)
            preroll_frames = max(1, int(0.35 / max(frame_seconds, 0.01)))
            while len(self._hands_free_preroll) > preroll_frames:
                self._hands_free_preroll.popleft()
            if not is_voice:
                self._hands_free_voice_seconds = 0.0
                return
            self._hands_free_voice_seconds += frame_seconds
            if self._hands_free_voice_seconds < trigger_seconds:
                return

            self._hands_free_active = True
            self._hands_free_frames = list(self._hands_free_preroll)
            self._hands_free_preroll.clear()
            self._hands_free_silence_seconds = 0.0
            self._hands_free_started_at = time.monotonic()
            if self._hands_free_on_speech_start:
                self._hands_free_on_speech_start()
            if self._hands_free_on_state:
                self._hands_free_on_state("recording")

        self._hands_free_frames.append(frame)
        if is_voice:
            self._hands_free_silence_seconds = 0.0
        else:
            self._hands_free_silence_seconds += frame_seconds

        duration = time.monotonic() - self._hands_free_started_at
        if self._hands_free_silence_seconds >= silence_seconds or duration >= max_seconds:
            self._submit_hands_free_audio(audio_settings)

    def _submit_hands_free_audio(self, audio_settings: dict) -> bool:
        if not self._hands_free_frames:
            self._hands_free_reset()
            return False

        frames = self._hands_free_frames
        self._hands_free_reset()
        try:
            audio = np.concatenate(frames)
            gain = float(audio_settings.get("input_gain", 1.0))
            if gain != 1.0:
                audio = np.clip(audio * gain, -1.0, 1.0)
            threshold = float(audio_settings.get("vad_threshold", 0.012))
            audio = _trim_silence(audio, self._hands_free_sample_rate, threshold * 0.55)
            ui_settings = self._hands_free_ui_settings
            min_key = "hands_free_wake_min_record_seconds" if ui_settings.get("wake_word_enabled") else "hands_free_min_record_seconds"
            min_seconds = float(audio_settings.get(min_key, 0.35 if min_key == "hands_free_wake_min_record_seconds" else 1.0))
            if len(audio) / self._hands_free_sample_rate < min_seconds:
                if self._hands_free_on_state:
                    self._hands_free_on_state("listening")
                return False

            fd, path = tempfile.mkstemp(suffix=".wav", prefix="voice_auto_")
            os.close(fd)
            with wave.open(path, "wb") as wf:
                wf.setnchannels(CHANNELS)
                wf.setsampwidth(2)
                wf.setframerate(self._hands_free_sample_rate)
                wf.writeframes((audio * 32767).astype(np.int16).tobytes())
            logger.info("Hands-free recording submitted: %.3fs.", len(audio) / self._hands_free_sample_rate)
            if self._hands_free_on_state:
                self._hands_free_on_state("processing")
            if self._hands_free_on_submit:
                self._hands_free_on_submit(path)
            return True
        except Exception:
            logger.exception("Hands-free submit failed.")
            try:
                if "path" in locals():
                    os.unlink(path)
            except OSError:
                pass
            if self._hands_free_on_state:
                self._hands_free_on_state("listening")
            return False

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
        turn_started_at = time.perf_counter()
        playback_start_at = None

        def mark_playback_start() -> None:
            nonlocal playback_start_at
            if playback_start_at is None:
                playback_start_at = time.perf_counter()

        # 1. STT
        text = await transcribe(wav_path)
        stt_done_at = time.perf_counter()
        logger.info("STT: %s", text)

        # 2. Hermes LLM
        reply = await self.hermes.chat(text, history)
        llm_first_token_at = time.perf_counter()
        logger.info("LLM: %s", reply)

        # 3. TTS
        mp3_path = await synthesize(reply)
        tts_first_chunk_at = time.perf_counter()
        logger.info("TTS saved: %s", mp3_path)

        # 4. Play
        try:
            await self._play_audio(mp3_path, on_playback_start=mark_playback_start)
        finally:
            try:
                os.unlink(mp3_path)
            except OSError:
                pass

        _record_last_turn_latency(
            turn_started_at,
            stt_done_at,
            llm_first_token_at,
            tts_first_chunk_at,
            playback_start_at,
            time.perf_counter(),
        )
        return {"user": text, "assistant": reply}

    async def run_turn_stream(self, wav_path: str, history: list = None, on_event=None, require_wake: bool = False) -> dict:
        """Full turn with streaming LLM text and sentence-by-sentence TTS playback."""
        history = history or []
        if on_event is None:
            async def on_event(_event):
                return None

        turn_started_at = time.perf_counter()
        try:
            text = await transcribe(wav_path)
        except RuntimeError as exc:
            if "No speech detected" in str(exc):
                logger.info("Ignoring hands-free audio with no speech detected.")
                await on_event({"type": "wake_ignored", "text": ""})
                return {"user": "", "assistant": "", "ignored": True}
            raise
        stt_done_at = time.perf_counter()
        logger.info("STT: %s", text)
        if require_wake:
            if _is_stt_hallucination(text):
                logger.info("Ignoring likely STT hallucination in hands-free mode: %s", text)
                await on_event({"type": "wake_ignored", "text": text})
                return {"user": text, "assistant": "", "ignored": True}
            matched, gated_text, wake_only = _apply_wake_gate(text, load_settings())
            if not matched:
                logger.info("Wake word not detected; ignoring hands-free transcript: %s", text)
                await on_event({"type": "wake_ignored", "text": text})
                return {"user": text, "assistant": "", "ignored": True}
            if wake_only:
                logger.info("Wake word detected; waiting for command.")
                await on_event({"type": "wake_prompt", "text": text})
                return {"user": text, "assistant": "", "wake_prompt": True}
            text = gated_text
            logger.info("Wake word accepted; command: %s", text)
        await on_event({"type": "user", "text": text})
        return await self._run_llm_tts_stream(
            text,
            history,
            on_event,
            turn_started_at=turn_started_at,
            stt_done_at=stt_done_at,
        )

    async def run_text_turn_stream(self, text: str, history: list = None, on_event=None) -> dict:
        """Text turn that reuses the same streaming LLM and TTS playback path."""
        history = history or []
        text = str(text or "").strip()
        if on_event is None:
            async def on_event(_event):
                return None
        if not text:
            return {"user": "", "assistant": "", "ignored": True}
        turn_started_at = time.perf_counter()
        logger.info("Text input: %s", text)
        await on_event({"type": "user", "text": text})
        return await self._run_llm_tts_stream(
            text,
            history,
            on_event,
            turn_started_at=turn_started_at,
            stt_done_at=turn_started_at,
        )

    async def _run_llm_tts_stream(
        self,
        text: str,
        history: list,
        on_event,
        turn_started_at: float | None = None,
        stt_done_at: float | None = None,
    ) -> dict:
        turn_started_at = turn_started_at or time.perf_counter()
        llm_first_token_at = None
        tts_first_chunk_at = None
        playback_start_at = None

        def mark_first_token() -> None:
            nonlocal llm_first_token_at
            if llm_first_token_at is None:
                llm_first_token_at = time.perf_counter()

        def mark_first_tts_chunk() -> None:
            nonlocal tts_first_chunk_at
            if tts_first_chunk_at is None:
                tts_first_chunk_at = time.perf_counter()

        def mark_playback_start() -> None:
            nonlocal playback_start_at
            if playback_start_at is None:
                playback_start_at = time.perf_counter()

        output_device, output_sample_rate, output_volume = self._playback_settings()
        player = _ContinuousPcmPlayer(output_device, output_sample_rate, output_volume)
        player.on_playback_start = mark_playback_start
        self._active_player = player
        chunk_config = tts_chunk_config()
        tts_queue: asyncio.Queue[str | None] = asyncio.Queue()
        synth_task = asyncio.create_task(self._tts_synth_worker(tts_queue, player, on_first_chunk=mark_first_tts_chunk))
        player_task = asyncio.create_task(self._tts_audio_player(player))
        reply_parts: list[str] = []
        sentence_buffer = ""
        tts_chunks_sent = 0

        try:
            async for delta in self.hermes.chat_stream(text, history):
                if delta:
                    mark_first_token()
                reply_parts.append(delta)
                sentence_buffer += delta
                await on_event({"type": "assistant_delta", "delta": delta})

                chunks, sentence_buffer = _pop_sentence_chunks(
                    sentence_buffer,
                    first_chunk=tts_chunks_sent == 0,
                    chunk_config=chunk_config,
                )
                for chunk in chunks:
                    await tts_queue.put(chunk)
                    tts_chunks_sent += 1

            chunks, sentence_buffer = _pop_sentence_chunks(sentence_buffer, force=True, chunk_config=chunk_config)
            for chunk in chunks:
                await tts_queue.put(chunk)
                tts_chunks_sent += 1

            reply = "".join(reply_parts).strip() or "我刚才没组织好回复。"
            logger.info("LLM: %s", reply)
            await on_event({"type": "assistant_done", "assistant": reply})
            await tts_queue.put(None)
            await synth_task
            await player_task
            _record_last_turn_latency(
                turn_started_at,
                stt_done_at,
                llm_first_token_at,
                tts_first_chunk_at,
                playback_start_at,
                time.perf_counter(),
            )
            return {"user": text, "assistant": reply}
        except asyncio.CancelledError:
            player.stop()
            synth_task.cancel()
            player_task.cancel()
            await asyncio.gather(synth_task, player_task, return_exceptions=True)
            raise
        except Exception:
            player.stop()
            synth_task.cancel()
            player_task.cancel()
            await asyncio.gather(synth_task, player_task, return_exceptions=True)
            raise
        finally:
            if self._active_player is player:
                self._active_player = None

    def _playback_settings(self) -> tuple[int, int, float]:
        settings = load_settings()
        audio_settings = settings["audio"]
        output_device, output_sample_rate = _resolve_audio_device("output", audio_settings.get("output_device"))
        if output_device is None:
            raise RuntimeError("没有检测到可用扬声器，请在 Windows 声音设置里启用输出设备。")
        return output_device, output_sample_rate or SAMPLE_RATE, float(audio_settings.get("output_volume", 1.0))

    async def _tts_synth_worker(self, text_queue: asyncio.Queue, player: _ContinuousPcmPlayer, on_first_chunk=None) -> None:
        first_chunk_reported = False
        try:
            while True:
                text = await text_queue.get()
                if text is None:
                    player.finish()
                    return
                mp3_path = await synthesize(text)
                logger.info("TTS chunk saved: %s", mp3_path)
                try:
                    pcm = await asyncio.to_thread(_decode_audio_file, mp3_path, player.sample_rate)
                    if not first_chunk_reported and on_first_chunk is not None:
                        first_chunk_reported = True
                        on_first_chunk()
                    await asyncio.to_thread(player.put, pcm)
                finally:
                    try:
                        os.unlink(mp3_path)
                    except OSError:
                        pass
        except asyncio.CancelledError:
            player.stop()
            raise
        except Exception:
            player.finish()
            raise

    async def _tts_audio_player(self, player: _ContinuousPcmPlayer) -> None:
        await asyncio.to_thread(player.run)

    async def _play_audio(self, path: str, on_playback_start=None) -> None:
        """Play MP3 via ffmpeg decode + sounddevice."""
        output_device, output_sample_rate, output_volume = self._playback_settings()
        player = _ContinuousPcmPlayer(output_device, output_sample_rate, output_volume)
        player.on_playback_start = on_playback_start
        self._active_player = player
        try:
            pcm = await asyncio.to_thread(_decode_audio_file, path, output_sample_rate)
            await asyncio.to_thread(player.put, pcm)
            player.finish()
            await asyncio.to_thread(player.run)
        except asyncio.CancelledError:
            player.stop()
            raise
        finally:
            if self._active_player is player:
                self._active_player = None

