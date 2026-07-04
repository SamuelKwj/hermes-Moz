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
from voice_pipeline import VoicePipeline  # noqa: E402


class SlowHermes:
    async def chat_stream(self, text, history):
        await asyncio.sleep(0.01)
        yield "你好"
        yield "。"


class FakeContinuousPcmPlayer:
    instances = []

    def __init__(self, device, sample_rate, volume):
        self.device = device
        self.sample_rate = sample_rate
        self.volume = volume
        self.items = []
        self.finished = False
        self.ran = False
        FakeContinuousPcmPlayer.instances.append(self)

    def put(self, pcm):
        self.items.append(pcm)

    def finish(self):
        self.finished = True

    def run(self):
        self.ran = True

    def stop(self):
        pass


async def fake_synthesize_file(_text):
    fd, path = tempfile.mkstemp(suffix=".mp3", prefix="feedback_test_")
    os.close(fd)
    Path(path).write_bytes(b"audio")
    return path


class VoicePipelineFastFeedbackTest(TestCase):
    def setUp(self):
        FakeContinuousPcmPlayer.instances = []

    def test_fast_tts_chunk_config_starts_first_chunk_earlier_than_standard(self):
        standard = voice_pipeline.tts_chunk_config({"tts": {"response_mode": "standard"}})
        fast = voice_pipeline.tts_chunk_config({"tts": {"response_mode": "fast"}})
        text = "abcdefghijkl。"

        standard_chunks, standard_rest = voice_pipeline._pop_sentence_chunks(
            text,
            first_chunk=True,
            chunk_config=standard,
        )
        fast_chunks, fast_rest = voice_pipeline._pop_sentence_chunks(
            text,
            first_chunk=True,
            chunk_config=fast,
        )

        self.assertEqual(standard_chunks, [])
        self.assertEqual(standard_rest, text)
        self.assertEqual(fast_chunks, [text])
        self.assertEqual(fast_rest, "")
        self.assertLess(fast.first_chunk_chars, standard.first_chunk_chars)

    def test_fast_feedback_sound_plays_generated_tone_when_enabled(self):
        pipeline = VoicePipeline.__new__(VoicePipeline)
        settings = {
            "tts": {
                "response_mode": "fast",
                "ack_sound_enabled": True,
                "ack_sound_volume": 0.25,
            }
        }

        with (
            patch.object(voice_pipeline, "_resolve_audio_device", return_value=(3, 16000)),
            patch.object(voice_pipeline, "_ContinuousPcmPlayer", FakeContinuousPcmPlayer),
        ):
            played = asyncio.run(pipeline.play_fast_feedback(settings))

        self.assertTrue(played)
        self.assertEqual(len(FakeContinuousPcmPlayer.instances), 1)
        player = FakeContinuousPcmPlayer.instances[0]
        self.assertEqual(player.device, 3)
        self.assertEqual(player.sample_rate, 16000)
        self.assertTrue(player.finished)
        self.assertTrue(player.ran)
        self.assertEqual(len(player.items), 1)
        self.assertGreater(len(player.items[0]), 0)

    def test_fast_feedback_sound_is_skipped_when_disabled(self):
        pipeline = VoicePipeline.__new__(VoicePipeline)
        settings = {"tts": {"response_mode": "standard", "ack_sound_enabled": True}}

        with patch.object(voice_pipeline, "_ContinuousPcmPlayer", FakeContinuousPcmPlayer):
            played = asyncio.run(pipeline.play_fast_feedback(settings))

        self.assertFalse(played)
        self.assertEqual(FakeContinuousPcmPlayer.instances, [])

    def test_feedback_phrase_cache_reuses_generated_audio(self):
        synth_calls = []
        settings = {"tts": {"mode": "edge_mp3", "voice": "voice-a", "feedback_ack_text": "收到。"}}

        async def synthesize_once(text):
            synth_calls.append(text)
            return await fake_synthesize_file(text)

        with tempfile.TemporaryDirectory() as tmpdir:
            with (
                patch.object(voice_pipeline, "get_app_dir", return_value=Path(tmpdir)),
                patch.object(voice_pipeline, "synthesize", synthesize_once),
            ):
                first = asyncio.run(voice_pipeline.ensure_feedback_phrase_cached("ack", settings))
                second = asyncio.run(voice_pipeline.ensure_feedback_phrase_cached("ack", settings))

                self.assertEqual(first, second)
                self.assertTrue(first.exists())
                self.assertEqual(synth_calls, ["收到。"])

    def test_fast_feedback_prefers_cached_ack_phrase(self):
        pipeline = VoicePipeline.__new__(VoicePipeline)
        played_paths = []
        settings = {
            "audio": {"output_device": 3, "output_volume": 1.0},
            "tts": {
                "response_mode": "fast",
                "ack_sound_enabled": True,
                "feedback_phrases_enabled": True,
                "feedback_ack_text": "收到。",
            },
        }

        async def fake_play_audio(path):
            played_paths.append(path)

        with tempfile.TemporaryDirectory() as tmpdir:
            with (
                patch.object(voice_pipeline, "get_app_dir", return_value=Path(tmpdir)),
                patch.object(voice_pipeline, "synthesize", fake_synthesize_file),
            ):
                cached = asyncio.run(voice_pipeline.ensure_feedback_phrase_cached("ack", settings))
                with patch.object(pipeline, "_play_audio", fake_play_audio):
                    played = asyncio.run(pipeline.play_fast_feedback(settings))

        self.assertTrue(played)
        self.assertEqual(played_paths, [cached])
        self.assertEqual(FakeContinuousPcmPlayer.instances, [])

    def test_slow_llm_enqueues_wait_phrase_before_assistant_audio(self):
        pipeline = VoicePipeline.__new__(VoicePipeline)
        pipeline.hermes = SlowHermes()
        pipeline._active_player = None
        pipeline._playback_settings = lambda: (None, 16000, 1.0)
        events = []
        settings = {
            "tts": {
                "response_mode": "fast",
                "feedback_phrases_enabled": True,
                "feedback_wait_text": "稍等。",
                "feedback_wait_delay_ms": 0,
            }
        }

        async def on_event(event):
            events.append(event)

        def fake_decode(path, _sample_rate):
            if "wait" in str(path):
                return np.array([2.0], dtype=np.float32)
            return np.array([1.0], dtype=np.float32)

        with tempfile.TemporaryDirectory() as tmpdir:
            with (
                patch.object(voice_pipeline, "get_app_dir", return_value=Path(tmpdir)),
                patch.object(voice_pipeline, "load_settings", return_value=settings),
                patch.object(voice_pipeline, "synthesize", fake_synthesize_file),
                patch.object(voice_pipeline, "_decode_audio_file", fake_decode),
                patch.object(voice_pipeline, "_ContinuousPcmPlayer", FakeContinuousPcmPlayer),
            ):
                asyncio.run(voice_pipeline.ensure_feedback_phrase_cached("wait", settings))
                result = asyncio.run(VoicePipeline._run_llm_tts_stream(pipeline, "测试", [], on_event))

        self.assertEqual(result, {"user": "测试", "assistant": "你好。"})
        self.assertEqual(FakeContinuousPcmPlayer.instances[0].items[0].tolist(), [2.0])
        self.assertIn({"type": "assistant_done", "assistant": "你好。"}, events)


if __name__ == "__main__":
    main()
