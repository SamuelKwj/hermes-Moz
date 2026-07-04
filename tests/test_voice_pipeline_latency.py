import asyncio
import os
import sys
import tempfile
from pathlib import Path
from unittest import TestCase, main
from unittest.mock import patch

import numpy as np


ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
sys.path.insert(0, str(BACKEND))

import voice_pipeline  # noqa: E402
from voice_pipeline import VoicePipeline, last_turn_latency, reset_last_turn_latency  # noqa: E402


class FakeHermes:
    async def chat_stream(self, text, history):
        yield "你好"
        yield "。"


class FakeContinuousPcmPlayer:
    def __init__(self, device, sample_rate, volume):
        self.device = device
        self.sample_rate = sample_rate
        self.volume = volume
        self.finished = False
        self.stopped = False
        self.items = []
        self.playback_started = False

    def put(self, pcm):
        self.items.append(pcm)
        if not self.playback_started and getattr(self, "on_playback_start", None):
            self.playback_started = True
            self.on_playback_start()

    def finish(self):
        self.finished = True

    def run(self):
        return None

    def stop(self):
        self.stopped = True


async def fake_transcribe(_path):
    return "测试一下"


async def fake_synthesize(_text):
    fd, path = tempfile.mkstemp(suffix=".mp3", prefix="latency_test_")
    os.close(fd)
    return path


def fake_decode_audio_file(_path, _sample_rate):
    return np.zeros(16, dtype=np.float32)


class VoicePipelineLatencyTest(TestCase):
    def tearDown(self):
        reset_last_turn_latency()

    def test_last_turn_latency_is_empty_until_a_turn_runs(self):
        reset_last_turn_latency()

        self.assertIsNone(last_turn_latency())

    def test_streaming_voice_turn_records_last_latency(self):
        pipeline = VoicePipeline.__new__(VoicePipeline)
        pipeline.hermes = FakeHermes()
        pipeline._active_player = None
        pipeline._playback_settings = lambda: (None, 16000, 1.0)
        events = []

        async def on_event(event):
            events.append(event)

        with (
            patch.object(voice_pipeline, "transcribe", fake_transcribe),
            patch.object(voice_pipeline, "synthesize", fake_synthesize),
            patch.object(voice_pipeline, "_decode_audio_file", fake_decode_audio_file),
            patch.object(voice_pipeline, "_ContinuousPcmPlayer", FakeContinuousPcmPlayer),
        ):
            reset_last_turn_latency()
            result = asyncio.run(pipeline.run_turn_stream("sample.wav", [], on_event))

        latency = last_turn_latency()
        self.assertEqual(result, {"user": "测试一下", "assistant": "你好。"})
        self.assertIn({"type": "assistant_done", "assistant": "你好。"}, events)
        self.assertIsNotNone(latency)
        for key in (
            "stt_ms",
            "llm_first_token_ms",
            "tts_first_chunk_ms",
            "playback_start_ms",
            "total_ms",
        ):
            self.assertIn(key, latency)
            self.assertIsInstance(latency[key], int)
            self.assertGreaterEqual(latency[key], 0)


if __name__ == "__main__":
    main()
