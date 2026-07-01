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

    def test_package_smoke_script_verifies_portable_and_installer_artifacts(self):
        script_path = ROOT / "scripts" / "package_smoke.ps1"

        self.assertTrue(script_path.exists(), "package smoke script should exist")
        script_text = script_path.read_text(encoding="utf-8")
        self.assertIn("HermesVoice.exe", script_text)
        self.assertIn("HermesVoiceSetup.exe", script_text)
        self.assertIn("/health", script_text)
        self.assertIn("/api/status", script_text)
        self.assertIn("ffmpeg", script_text)
        self.assertIn("ffplay", script_text)
        self.assertIn("THIRD_PARTY_NOTICES.md", script_text)
        self.assertIn("UninstallString", script_text)
        self.assertIn("/LOG=", script_text)
        self.assertIn("Installer did not update", script_text)
        self.assertIn("sourceExeTimestamp", script_text)
        self.assertIn("FBC0C2F1-D8D8-4D70-8F07-4E23D2B31759", script_text)
        self.assertIn("Hermes Voice*", script_text)

    def test_third_party_notices_are_packaged(self):
        notice_path = ROOT / "THIRD_PARTY_NOTICES.md"
        spec_text = (ROOT / "packaging" / "hermes_voice.spec").read_text(encoding="utf-8")
        packaging_text = (ROOT / "docs" / "packaging.md").read_text(encoding="utf-8")

        self.assertTrue(notice_path.exists(), "third-party notices should exist")
        notice_text = notice_path.read_text(encoding="utf-8")
        self.assertIn("FFmpeg", notice_text)
        self.assertIn("Gyan", notice_text)
        self.assertIn("Inno Setup", notice_text)
        self.assertIn("THIRD_PARTY_NOTICES.md", spec_text)
        self.assertIn("THIRD_PARTY_NOTICES.md", packaging_text)

    def test_settings_ui_exposes_model_cache_help_and_refresh_feedback(self):
        frontend_text = (ROOT / "frontend" / "index.html").read_text(encoding="utf-8")
        launcher_text = (ROOT / "launcher.py").read_text(encoding="utf-8")

        self.assertIn("openModelDirBtn", frontend_text)
        self.assertIn("模型手动下载说明.txt", frontend_text)
        self.assertIn("正在检测...", frontend_text)
        self.assertIn("检测完成", frontend_text)
        self.assertIn("def open_model_dir", launcher_text)

    def test_public_release_gate_tracks_external_sale_requirements(self):
        gate_path = ROOT / "scripts" / "release_gate.ps1"
        evidence_path = ROOT / "docs" / "release_evidence_template.md"
        packaging_text = (ROOT / "docs" / "packaging.md").read_text(encoding="utf-8")

        self.assertTrue(gate_path.exists(), "public release gate script should exist")
        gate_text = gate_path.read_text(encoding="utf-8")
        self.assertIn("Get-AuthenticodeSignature", gate_text)
        self.assertIn("release_smoke.ps1", gate_text)
        self.assertIn("package_smoke.ps1", gate_text)
        self.assertIn("RequireSigned", gate_text)
        self.assertIn("RequireExternalEvidence", gate_text)
        self.assertIn("license_compliance", gate_text)
        self.assertIn("clean_windows", gate_text)
        self.assertIn("microphone", gate_text)
        self.assertIn("webview2", gate_text)

        self.assertTrue(evidence_path.exists(), "release evidence template should exist")
        evidence_text = evidence_path.read_text(encoding="utf-8")
        self.assertIn("Clean Windows", evidence_text)
        self.assertIn("Microphone", evidence_text)
        self.assertIn("License", evidence_text)
        self.assertIn("Code signing", evidence_text)
        self.assertIn("release_gate.ps1", packaging_text)

    def test_release_signing_script_signs_and_verifies_artifacts(self):
        sign_path = ROOT / "scripts" / "sign_release.ps1"
        packaging_text = (ROOT / "docs" / "packaging.md").read_text(encoding="utf-8")
        status_text = (ROOT / "docs" / "productization_status.md").read_text(encoding="utf-8")

        self.assertTrue(sign_path.exists(), "release signing script should exist")
        sign_text = sign_path.read_text(encoding="utf-8")
        self.assertIn("signtool", sign_text.lower())
        self.assertIn("HermesVoice.exe", sign_text)
        self.assertIn("HermesVoiceSetup.exe", sign_text)
        self.assertIn("TimestampUrl", sign_text)
        self.assertIn("PfxPath", sign_text)
        self.assertIn("CertificateThumbprint", sign_text)
        self.assertIn("Get-AuthenticodeSignature", sign_text)
        self.assertIn("sign_release.ps1", packaging_text)
        self.assertIn("sign_release.ps1", status_text)


if __name__ == "__main__":
    unittest.main()
