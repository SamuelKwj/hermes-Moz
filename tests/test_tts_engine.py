import asyncio
import sys
from pathlib import Path
from unittest import TestCase, main
from unittest.mock import Mock, patch


ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
sys.path.insert(0, str(BACKEND))

import tts_engine  # noqa: E402


class TtsEngineTest(TestCase):
    def test_edge_tts_falls_back_to_windows_native_after_two_failures(self):
        saves = []

        class FailingCommunicate:
            def __init__(self, text, voice, rate="+0%", volume="+0%"):
                self.text = text
                self.voice = voice
                self.rate = rate
                self.volume = volume

            async def save(self, path):
                saves.append(path)
                raise RuntimeError("edge tts unavailable")

        settings = {
            "voice": "zh-CN-XiaoxiaoNeural",
            "native_voice": "Microsoft Huihui Desktop",
            "rate": "+0%",
            "volume": "+0%",
        }

        native = Mock(return_value="fallback.wav")
        with patch("edge_tts.Communicate", FailingCommunicate), patch.object(
            tts_engine,
            "_synthesize_windows_native",
            native,
        ), patch.object(tts_engine.logger, "warning") as warning:
            result = asyncio.run(tts_engine._synthesize_edge_mp3("我在，大王请说。", settings))

        self.assertEqual(result, "fallback.wav")
        self.assertEqual(len(saves), 2)
        native.assert_called_once_with("我在，大王请说。", settings)
        self.assertEqual(warning.call_count, 2)


if __name__ == "__main__":
    main()
