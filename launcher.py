"""Desktop launcher -- starts backend server + pywebview floating window."""
import asyncio
import logging
import os
import sys
import threading
import time
from pathlib import Path

import uvicorn
import webview

ROOT = Path(__file__).resolve().parent
BACKEND_DIR = ROOT / "backend"
FRONTEND_DIR = ROOT / "frontend"

os.chdir(str(BACKEND_DIR))
sys.path.insert(0, str(BACKEND_DIR))

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("launcher")


class UvicornThread(threading.Thread):
    def __init__(self):
        super().__init__(daemon=True)
        self._config = uvicorn.Config(
            "server:app",
            host="127.0.0.1",
            port=8765,
            log_level="info",
        )
        self._server = uvicorn.Server(self._config)

    def run(self):
        self._server.run()

    def stop(self):
        self._server.should_exit = True


def main():
    logger.info("Starting backend server...")
    server_thread = UvicornThread()
    server_thread.start()
    time.sleep(2)

    logger.info("Launching desktop widget...")
    window = webview.create_window(
        title="Voice Assistant",
        url="http://127.0.0.1:8765",
        width=360,
        height=560,
        frameless=True,
        on_top=True,
        resizable=False,
        easy_drag=False,
    )

    webview.start(gui="edgechromium", debug=False)
    server_thread.stop()
    logger.info("Shutdown complete.")


if __name__ == "__main__":
    main()
