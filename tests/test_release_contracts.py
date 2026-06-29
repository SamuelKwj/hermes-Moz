from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]


class ReleaseContractsTest(unittest.TestCase):
    def test_installer_packages_pyinstaller_dist(self):
        installer_text = (ROOT / "installer" / "inno" / "HermesVoice.iss").read_text(encoding="utf-8")

        self.assertIn(r'Source: "..\..\dist\HermesVoice\*"', installer_text)

    def test_pyinstaller_bundles_required_assets_and_optional_bin(self):
        spec_text = (ROOT / "packaging" / "hermes_voice.spec").read_text(encoding="utf-8")

        self.assertIn('(str(ROOT / "frontend"), "frontend")', spec_text)
        self.assertIn('(str(ROOT / "assets"), "assets")', spec_text)
        self.assertIn('ROOT / "bin"', spec_text)

    def test_release_smoke_script_checks_required_gates(self):
        script_path = ROOT / "scripts" / "release_smoke.ps1"

        self.assertTrue(script_path.exists(), "release smoke script should exist")
        script_text = script_path.read_text(encoding="utf-8")
        self.assertIn("compileall", script_text)
        self.assertIn("/health", script_text)
        self.assertIn("Invoke-Native", script_text)
        self.assertIn("LASTEXITCODE", script_text)
        self.assertIn("Wait-For-StatusReady", script_text)
        self.assertIn("stt_ready", script_text)
        self.assertIn("tts_ready", script_text)
        self.assertIn("build_pyinstaller.ps1", script_text)

    def test_release_build_prepares_bundled_ffmpeg_without_tracking_binaries(self):
        gitignore_text = (ROOT / ".gitignore").read_text(encoding="utf-8")
        build_text = (ROOT / "scripts" / "build_pyinstaller.ps1").read_text(encoding="utf-8")
        prepare_path = ROOT / "scripts" / "prepare_release_bin.ps1"

        self.assertIn("bin/", gitignore_text)
        self.assertIn("prepare_release_bin.ps1", build_text)
        self.assertTrue(prepare_path.exists(), "release bin preparation script should exist")
        prepare_text = prepare_path.read_text(encoding="utf-8")
        self.assertIn("ffmpeg", prepare_text)
        self.assertIn("ffplay", prepare_text)
        self.assertIn("Copy-Item", prepare_text)
        self.assertIn("Required", prepare_text)


if __name__ == "__main__":
    unittest.main()
