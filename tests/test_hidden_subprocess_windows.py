import os
import subprocess
import sys
from pathlib import Path
from unittest import TestCase, main, skipUnless
from unittest.mock import Mock, patch

import numpy as np


ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
sys.path.insert(0, str(BACKEND))

import tts_engine  # noqa: E402
import voice_pipeline  # noqa: E402


@skipUnless(os.name == "nt" and hasattr(subprocess, "CREATE_NO_WINDOW"), "Windows-only console window behavior")
class HiddenSubprocessWindowsTest(TestCase):
    def test_ffmpeg_decode_hides_console_window(self):
        kwargs_seen = {}

        def fake_run(_command, **kwargs):
            kwargs_seen.update(kwargs)
            return Mock(stdout=np.array([0.0], dtype=np.float32).tobytes())

        with patch.object(voice_pipeline.subprocess, "run", side_effect=fake_run):
            voice_pipeline._decode_audio_file("answer.mp3", 16000)

        self.assertIn("creationflags", kwargs_seen)
        self.assertTrue(kwargs_seen["creationflags"] & subprocess.CREATE_NO_WINDOW)

    def test_windows_native_tts_hides_powershell_window(self):
        kwargs_seen = {}

        def fake_run(command, **kwargs):
            kwargs_seen.update(kwargs)
            return subprocess.CompletedProcess(command, 0, stdout=b"", stderr=b"")

        settings = {
            "native_voice": "Microsoft Huihui Desktop",
            "rate": "+0%",
            "volume": "+0%",
        }
        wav_path = None
        try:
            with patch.object(tts_engine.subprocess, "run", side_effect=fake_run):
                wav_path = tts_engine._synthesize_windows_native("test", settings)
        finally:
            if wav_path:
                try:
                    os.unlink(wav_path)
                except OSError:
                    pass

        self.assertIn("creationflags", kwargs_seen)
        self.assertTrue(kwargs_seen["creationflags"] & subprocess.CREATE_NO_WINDOW)


if __name__ == "__main__":
    main()
