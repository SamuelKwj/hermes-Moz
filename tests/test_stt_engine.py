import sys
import types
from pathlib import Path
from unittest import TestCase, main
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
sys.path.insert(0, str(BACKEND))

import stt_engine  # noqa: E402


class FakeWhisperModel:
    calls = []

    def __init__(self, model, device, compute_type, local_files_only=False):
        self.__class__.calls.append(
            {
                "model": model,
                "device": device,
                "compute_type": compute_type,
                "local_files_only": local_files_only,
            }
        )
        if model == "base":
            raise RuntimeError("internet connection unavailable")
        self.model = model


class SttEngineTest(TestCase):
    def setUp(self):
        stt_engine.reset_model()
        FakeWhisperModel.calls = []
        self.previous_faster_whisper = sys.modules.get("faster_whisper")
        fake_module = types.SimpleNamespace(WhisperModel=FakeWhisperModel)
        sys.modules["faster_whisper"] = fake_module

    def tearDown(self):
        stt_engine.reset_model()
        if self.previous_faster_whisper is None:
            sys.modules.pop("faster_whisper", None)
        else:
            sys.modules["faster_whisper"] = self.previous_faster_whisper

    def test_cpu_model_download_failure_falls_back_to_tiny(self):
        settings = {
            "stt": {
                "model": "base",
                "language": "zh",
                "device": "cpu",
                "compute_type": "int8",
                "beam_size": 5,
            }
        }

        with patch("settings.load_settings", return_value=settings), patch.object(
            stt_engine,
            "_local_stt_model_path",
            return_value=None,
        ), patch.object(stt_engine.logger, "exception"):
            model = stt_engine._get_model()
            status = stt_engine.status()

        self.assertIsInstance(model, FakeWhisperModel)
        self.assertEqual(
            FakeWhisperModel.calls,
            [
                {"model": "base", "device": "cpu", "compute_type": "int8", "local_files_only": False},
                {"model": "tiny", "device": "cpu", "compute_type": "int8", "local_files_only": False},
            ],
        )
        self.assertEqual(status["model"], "tiny")
        self.assertTrue(status["fallback"])
        self.assertIn("tiny", status["fallback_reason"])


if __name__ == "__main__":
    main()
