"""Desktop launcher -- starts backend server + pywebview floating window."""
import logging
import os
import sys
import threading
import time
import urllib.request
from pathlib import Path

SOURCE_ROOT = Path(__file__).resolve().parent
VENV_PYTHON = SOURCE_ROOT / ".venv" / "Scripts" / "python.exe"

if not getattr(sys, "frozen", False) and VENV_PYTHON.exists():
    try:
        current_python = Path(sys.executable).resolve()
        venv_python = VENV_PYTHON.resolve()
    except OSError:
        current_python = Path(sys.executable)
        venv_python = VENV_PYTHON
    if current_python != venv_python:
        os.execv(str(venv_python), [str(venv_python), str(Path(__file__).resolve()), *sys.argv[1:]])

import uvicorn
import webview

ROOT = Path(getattr(sys, "_MEIPASS", SOURCE_ROOT))
BACKEND_DIR = ROOT / "backend"

if BACKEND_DIR.exists():
    os.chdir(str(BACKEND_DIR))
    sys.path.insert(0, str(BACKEND_DIR))

from app_runtime import configure_logging, configure_model_cache, prepend_bundled_bin_to_path
from settings import get_host, get_port, load_settings

prepend_bundled_bin_to_path()
configure_model_cache()
configure_logging()
logger = logging.getLogger("launcher")
_tray_icon = None


class DesktopApi:
    def __init__(self):
        self._window = None

    def _bind_window(self, window):
        self._window = window

    def hide_window(self):
        if self._window is None:
            return {"ok": False, "message": "Window is not ready."}
        self._window.hide()
        return {"ok": True}


class UvicornThread(threading.Thread):
    def __init__(self, host: str, port: int):
        super().__init__(daemon=True)
        self.host = host
        self.port = port
        self._config = uvicorn.Config(
            "server:app",
            host=host,
            port=port,
            log_level="info",
        )
        self._server = uvicorn.Server(self._config)

    def run(self):
        self._server.run()

    def stop(self):
        self._server.should_exit = True


def wait_for_backend(host: str, port: int, timeout_seconds=30):
    deadline = time.time() + timeout_seconds
    last_error = None

    while time.time() < deadline:
        try:
            with urllib.request.urlopen(f"http://{host}:{port}/health", timeout=1) as response:
                if response.status == 200:
                    return
        except Exception as exc:
            last_error = exc
            time.sleep(0.25)

    raise RuntimeError(f"Backend did not become ready: {last_error}")


def _create_tray_icon(window):
    try:
        import pystray
        from PIL import Image, ImageDraw
    except Exception:
        logger.exception("System tray dependencies are unavailable.")
        return None

    image = Image.new("RGBA", (64, 64), (10, 10, 15, 0))
    draw = ImageDraw.Draw(image)
    draw.ellipse((7, 7, 57, 57), fill=(108, 92, 231, 255))
    draw.ellipse((22, 14, 42, 40), fill=(224, 224, 240, 255))
    draw.rounded_rectangle((18, 38, 46, 46), radius=5, fill=(224, 224, 240, 255))

    def show_window(icon, item):
        window.show()

    def hide_window(icon, item):
        window.hide()

    def exit_app(icon, item):
        icon.stop()
        window.destroy()

    return pystray.Icon(
        "Hermes Voice",
        image,
        "Hermes Voice",
        pystray.Menu(
            pystray.MenuItem("显示", show_window, default=True),
            pystray.MenuItem("隐藏", hide_window),
            pystray.MenuItem("退出", exit_app),
        ),
    )


def main():
    host = get_host()
    port = get_port()
    settings = load_settings()

    logger.info("Starting backend server...")
    server_thread = UvicornThread(host, port)
    server_thread.start()
    wait_for_backend(host, port)

    logger.info("Launching desktop widget...")
    desktop_api = DesktopApi()
    window = webview.create_window(
        title="Voice Assistant",
        url=f"http://{host}:{port}",
        width=360,
        height=560,
        frameless=True,
        on_top=bool(settings["ui"].get("always_on_top", True)),
        resizable=False,
        easy_drag=False,
        draggable=True,
        js_api=desktop_api,
    )
    desktop_api._bind_window(window)

    def on_started():
        global _tray_icon
        _tray_icon = _create_tray_icon(window)
        if _tray_icon:
            _tray_icon.run_detached()
        if settings["ui"].get("start_minimized", False):
            window.hide()

    try:
        webview.start(on_started, gui="edgechromium", debug=False)
    finally:
        if _tray_icon:
            _tray_icon.stop()
    server_thread.stop()
    logger.info("Shutdown complete.")


if __name__ == "__main__":
    main()
