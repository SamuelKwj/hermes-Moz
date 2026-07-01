import os
import sys
import tempfile
from pathlib import Path
from unittest import TestCase, main
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
sys.path.insert(0, str(BACKEND))

import app_runtime  # noqa: E402


class AppRuntimeTest(TestCase):
    def test_configure_model_cache_creates_manual_download_guide(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch.dict(os.environ, {"LOCALAPPDATA": tmpdir}, clear=False):
                cache_dir = app_runtime.configure_model_cache()

            guide_path = cache_dir / "模型手动下载说明.txt"
            guide_text = guide_path.read_text(encoding="utf-8")

            self.assertTrue(guide_path.exists())
            self.assertIn("faster-whisper-base", guide_text)
            self.assertIn(str(cache_dir), guide_text)
            self.assertIn("Hugging Face", guide_text)


if __name__ == "__main__":
    main()
