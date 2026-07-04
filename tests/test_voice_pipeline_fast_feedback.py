import asyncio
import sys
from pathlib import Path
from unittest import TestCase, main
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
sys.path.insert(0, str(BACKEND))

import voice_pipeline  # noqa: E402
from voice_pipeline import VoicePipeline  # noqa: E402


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


if __name__ == "__main__":
    main()
