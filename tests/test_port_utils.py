import sys
from pathlib import Path
from unittest import TestCase, main
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
sys.path.insert(0, str(BACKEND))

import port_utils  # noqa: E402
import settings  # noqa: E402


class PortUtilsTest(TestCase):
    def test_voice_desktop_default_port_is_fixed_8765(self):
        with patch.dict("os.environ", {}, clear=True):
            self.assertEqual(settings.get_port(), 8765)

    def test_windows_port_owner_message_names_process_and_pid(self):
        def fake_run(command, **_kwargs):
            class Result:
                stdout = ""

            result = Result()
            if command[0] == "netstat":
                result.stdout = "  TCP    127.0.0.1:8765    0.0.0.0:0    LISTENING    4242\r\n"
            elif command[0] == "tasklist":
                result.stdout = '"python.exe","4242","Console","1","10,000 K"\r\n'
            return result

        with patch.object(port_utils.os, "name", "nt"), patch.object(port_utils.subprocess, "run", side_effect=fake_run):
            owners = port_utils.find_port_owners(8765)

        self.assertEqual(owners, ["python.exe(PID 4242)"])
        message = port_utils.format_port_in_use_message("hermes-voice-desktop", "127.0.0.1", 8765, owners)
        self.assertIn("端口 8765 已被占用", message)
        self.assertIn("python.exe(PID 4242)", message)
        self.assertIn("不会自动切换端口", message)


if __name__ == "__main__":
    main()