"""Desktop launcher -- starts backend server + pywebview floating window."""
import ctypes
import ctypes.wintypes
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

from app_runtime import configure_logging, configure_model_cache, get_icon_file, prepend_bundled_bin_to_path
from settings import get_host, get_port, load_settings

prepend_bundled_bin_to_path()
configure_model_cache()
configure_logging()
logger = logging.getLogger("launcher")
_tray_icon = None
_edge_dock_controller = None


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

    def show_main_window(self):
        if self._window is None:
            return {"ok": False, "message": "Window is not ready."}
        self._window.show()
        if _edge_dock_controller:
            _edge_dock_controller.show_main_window()
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

    icon_path = get_icon_file("app-icon-256.png")
    if icon_path.exists():
        with Image.open(icon_path) as icon_image:
            image = icon_image.convert("RGBA").resize((64, 64), Image.Resampling.LANCZOS)
    else:
        image = Image.new("RGBA", (64, 64), (10, 10, 15, 0))
        draw = ImageDraw.Draw(image)
        draw.ellipse((7, 7, 57, 57), fill=(5, 5, 5, 255))
        draw.line([(23, 50), (49, 24)], fill=(245, 242, 234, 255), width=4)
        draw.rounded_rectangle((20, 17, 45, 52), radius=4, outline=(255, 255, 255, 255), width=4)
        draw.ellipse((41, 15, 53, 27), fill=(243, 154, 66, 255))

    def show_window(icon, item):
        window.show()
        if _edge_dock_controller:
            _edge_dock_controller.show_main_window()

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


def _evaluate_js(window, script: str) -> None:
    try:
        window.evaluate_js(script)
    except Exception:
        logger.debug("Window JavaScript call failed.", exc_info=True)


def _window_hwnd(window) -> int | None:
    native = getattr(window, "native", None)
    handle = getattr(native, "Handle", None)
    if handle is None:
        return None
    try:
        if hasattr(handle, "ToInt64"):
            return int(handle.ToInt64())
        if hasattr(handle, "ToInt32"):
            return int(handle.ToInt32())
        return int(handle)
    except Exception:
        logger.debug("Could not resolve native window handle.", exc_info=True)
        return None


def _set_window_circle_region(window) -> None:
    if os.name != "nt":
        return
    hwnd = _window_hwnd(window)
    if not hwnd:
        return

    user32 = ctypes.windll.user32
    gdi32 = ctypes.windll.gdi32
    rect = ctypes.wintypes.RECT()
    user32.GetClientRect.argtypes = [ctypes.wintypes.HWND, ctypes.POINTER(ctypes.wintypes.RECT)]
    user32.GetClientRect.restype = ctypes.wintypes.BOOL
    user32.SetWindowRgn.argtypes = [ctypes.wintypes.HWND, ctypes.wintypes.HRGN, ctypes.wintypes.BOOL]
    user32.SetWindowRgn.restype = ctypes.c_int
    gdi32.CreateEllipticRgn.argtypes = [ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int]
    gdi32.CreateEllipticRgn.restype = ctypes.wintypes.HRGN
    gdi32.DeleteObject.argtypes = [ctypes.wintypes.HGDIOBJ]
    gdi32.DeleteObject.restype = ctypes.wintypes.BOOL

    if not user32.GetClientRect(hwnd, ctypes.byref(rect)):
        return
    width = max(1, int(rect.right - rect.left))
    height = max(1, int(rect.bottom - rect.top))
    region = gdi32.CreateEllipticRgn(0, 0, width + 1, height + 1)
    if not region:
        return
    if user32.SetWindowRgn(hwnd, region, True) == 0:
        gdi32.DeleteObject(region)


def _clear_window_region(window) -> None:
    if os.name != "nt":
        return
    hwnd = _window_hwnd(window)
    if not hwnd:
        return
    user32 = ctypes.windll.user32
    user32.SetWindowRgn.argtypes = [ctypes.wintypes.HWND, ctypes.wintypes.HRGN, ctypes.wintypes.BOOL]
    user32.SetWindowRgn.restype = ctypes.c_int
    user32.SetWindowRgn(hwnd, None, True)


class GlobalHotkeyController(threading.Thread):
    """Windows-only Ctrl+Alt+Space hold-to-talk hotkey."""

    VK_SPACE = 0x20
    VK_CONTROL = 0x11
    VK_MENU = 0x12
    WH_KEYBOARD_LL = 13
    WM_KEYDOWN = 0x0100
    WM_KEYUP = 0x0101
    WM_SYSKEYDOWN = 0x0104
    WM_SYSKEYUP = 0x0105
    WM_QUIT = 0x0012

    class KBDLLHOOKSTRUCT(ctypes.Structure):
        _fields_ = [
            ("vkCode", ctypes.wintypes.DWORD),
            ("scanCode", ctypes.wintypes.DWORD),
            ("flags", ctypes.wintypes.DWORD),
            ("time", ctypes.wintypes.DWORD),
            ("dwExtraInfo", ctypes.c_void_p),
        ]

    def __init__(self, window):
        super().__init__(daemon=True)
        self.window = window
        self._running = threading.Event()
        self._running.set()
        self._pressed = False
        self._hook = None
        self._thread_id = 0
        self._callback = None

    def stop(self):
        self._running.clear()
        if os.name == "nt" and self._thread_id:
            ctypes.windll.user32.PostThreadMessageW(self._thread_id, self.WM_QUIT, 0, 0)

    def _enabled(self) -> bool:
        try:
            return bool(load_settings()["ui"].get("global_hotkey_enabled", True))
        except Exception:
            return True

    def _modifiers_down(self) -> bool:
        user32 = ctypes.windll.user32
        ctrl_down = bool(user32.GetAsyncKeyState(self.VK_CONTROL) & 0x8000)
        alt_down = bool(user32.GetAsyncKeyState(self.VK_MENU) & 0x8000)
        return ctrl_down and alt_down

    def _handle(self, n_code, w_param, l_param):
        if n_code == 0:
            data = ctypes.cast(ctypes.c_void_p(l_param), ctypes.POINTER(self.KBDLLHOOKSTRUCT)).contents
            is_space = data.vkCode == self.VK_SPACE
            is_modifier = data.vkCode in (self.VK_CONTROL, self.VK_MENU)
            enabled = self._enabled()
            modifiers_down = self._modifiers_down()
            if is_space and enabled and (modifiers_down or self._pressed):
                if w_param in (self.WM_KEYDOWN, self.WM_SYSKEYDOWN) and not self._pressed:
                    self._pressed = True
                    _evaluate_js(self.window, "window.hermesGlobalHotkeyDown && window.hermesGlobalHotkeyDown();")
                    return 1
                if w_param in (self.WM_KEYUP, self.WM_SYSKEYUP):
                    self._pressed = False
                    _evaluate_js(self.window, "window.hermesGlobalHotkeyUp && window.hermesGlobalHotkeyUp();")
                    return 1
            if is_modifier and self._pressed and w_param in (self.WM_KEYUP, self.WM_SYSKEYUP):
                self._pressed = False
                _evaluate_js(self.window, "window.hermesGlobalHotkeyUp && window.hermesGlobalHotkeyUp();")
        return ctypes.windll.user32.CallNextHookEx(self._hook, n_code, w_param, l_param)

    def run(self):
        if os.name != "nt":
            logger.info("Global hotkey is only supported on Windows.")
            return

        user32 = ctypes.windll.user32
        kernel32 = ctypes.windll.kernel32
        lresult = ctypes.c_ssize_t
        hhook = ctypes.c_void_p
        self._thread_id = kernel32.GetCurrentThreadId()
        callback_type = ctypes.WINFUNCTYPE(
            lresult,
            ctypes.c_int,
            ctypes.wintypes.WPARAM,
            ctypes.wintypes.LPARAM,
        )
        user32.SetWindowsHookExW.argtypes = [
            ctypes.c_int,
            callback_type,
            ctypes.wintypes.HINSTANCE,
            ctypes.wintypes.DWORD,
        ]
        user32.SetWindowsHookExW.restype = hhook
        user32.CallNextHookEx.argtypes = [
            hhook,
            ctypes.c_int,
            ctypes.wintypes.WPARAM,
            ctypes.wintypes.LPARAM,
        ]
        user32.CallNextHookEx.restype = lresult
        user32.UnhookWindowsHookEx.argtypes = [hhook]
        user32.UnhookWindowsHookEx.restype = ctypes.wintypes.BOOL
        user32.PostThreadMessageW.argtypes = [
            ctypes.wintypes.DWORD,
            ctypes.wintypes.UINT,
            ctypes.wintypes.WPARAM,
            ctypes.wintypes.LPARAM,
        ]
        user32.PostThreadMessageW.restype = ctypes.wintypes.BOOL
        self._callback = callback_type(self._handle)
        self._hook = user32.SetWindowsHookExW(self.WH_KEYBOARD_LL, self._callback, None, 0)
        if not self._hook:
            logger.warning("Global hotkey hook could not be installed.")
            return

        msg = ctypes.wintypes.MSG()
        try:
            while self._running.is_set() and user32.GetMessageW(ctypes.byref(msg), None, 0, 0) != 0:
                user32.TranslateMessage(ctypes.byref(msg))
                user32.DispatchMessageW(ctypes.byref(msg))
        finally:
            if self._hook:
                user32.UnhookWindowsHookEx(self._hook)
                self._hook = None


class EdgeDockController(threading.Thread):
    APP_WIDTH = 360
    APP_HEIGHT = 560
    ORB_SIZE = 88
    ORB_VISIBLE_WIDTH = 24
    EDGE_GAP = 0
    HOVER_LISTEN_DELAY_SECONDS = 1.0
    COLLAPSE_GRACE_SECONDS = 1.0
    HOVER_SLOP = 18
    DOCK_MOVE_TOLERANCE = 28

    def __init__(self, window):
        super().__init__(daemon=True)
        self.window = window
        self._running = threading.Event()
        self._running.set()
        self._main_requested = threading.Event()
        self._active = False
        self._main_mode = False
        self._revealed = False
        self._last_inside_at = 0.0
        self._main_hold_until = 0.0
        self._orb_dock_x = None
        self._main_dock_x = None
        self._hover_started_at = 0.0
        self._hover_listening = False

    def stop(self):
        self._running.clear()

    def show_main_window(self):
        self._main_requested.set()

    def _screen_size(self) -> tuple[int, int]:
        if os.name == "nt":
            user32 = ctypes.windll.user32
            return int(user32.GetSystemMetrics(0)), int(user32.GetSystemMetrics(1))
        return 1920, 1080

    def _cursor_pos(self) -> tuple[int, int]:
        point = ctypes.wintypes.POINT()
        ctypes.windll.user32.GetCursorPos(ctypes.byref(point))
        return int(point.x), int(point.y)

    def _target_y(self, height: int) -> int:
        screen_width, screen_height = self._screen_size()
        del screen_width
        current_y = self.window.y if isinstance(self.window.y, int) else None
        if current_y is None or current_y < 0 or current_y > screen_height - 80:
            current_y = max(40, (screen_height - height) // 2)
        return max(0, min(current_y, max(0, screen_height - height)))

    def _collapse(self):
        screen_width, _ = self._screen_size()
        y = self._target_y(self.ORB_SIZE)
        dock_x = screen_width - self.ORB_VISIBLE_WIDTH
        self.window.resize(self.ORB_SIZE, self.ORB_SIZE)
        self.window.move(dock_x, y)
        _set_window_circle_region(self.window)
        self._set_hover_listening(False)
        _evaluate_js(self.window, "window.hermesSetDocked && window.hermesSetDocked(true);")
        self._main_mode = False
        self._revealed = False
        self._orb_dock_x = dock_x
        self._hover_started_at = 0.0
        self._active = True

    def _reveal(self):
        screen_width, _ = self._screen_size()
        y = self._target_y(self.ORB_SIZE)
        dock_x = screen_width - self.ORB_SIZE - self.EDGE_GAP
        self.window.resize(self.ORB_SIZE, self.ORB_SIZE)
        self.window.move(dock_x, y)
        _set_window_circle_region(self.window)
        _evaluate_js(self.window, "window.hermesSetDocked && window.hermesSetDocked(true);")
        self._revealed = True
        self._orb_dock_x = dock_x
        self._hover_started_at = time.time()
        self._active = True

    def _show_main(self, dock_to_edge: bool = True):
        screen_width, _ = self._screen_size()
        y = self._target_y(self.APP_HEIGHT)
        current_x = self.window.x if isinstance(self.window.x, int) else screen_width - self.APP_WIDTH
        if dock_to_edge:
            main_x = screen_width - self.APP_WIDTH - self.EDGE_GAP
        else:
            main_x = max(0, min(current_x, max(0, screen_width - self.APP_WIDTH)))
        self._set_hover_listening(False)
        _clear_window_region(self.window)
        _evaluate_js(self.window, "window.hermesSetDocked && window.hermesSetDocked(false);")
        self.window.resize(self.APP_WIDTH, self.APP_HEIGHT)
        self.window.move(main_x, y)
        _clear_window_region(self.window)
        self.window.show()
        self._main_mode = True
        self._revealed = False
        self._orb_dock_x = None
        self._main_dock_x = main_x if dock_to_edge else None
        self._hover_started_at = 0.0
        now = time.time()
        self._last_inside_at = now
        self._main_hold_until = now + 6.0
        self._active = True

    def _set_hover_listening(self, enabled: bool):
        if self._hover_listening == enabled:
            return
        self._hover_listening = enabled
        script_value = "true" if enabled else "false"
        _evaluate_js(
            self.window,
            f"window.hermesSetHoverListening && window.hermesSetHoverListening({script_value});",
        )

    def _inside_window(self) -> bool:
        x, y = self._cursor_pos()
        left = self.window.x if isinstance(self.window.x, int) else 0
        top = self.window.y if isinstance(self.window.y, int) else 0
        width = self.window.width or self.ORB_SIZE
        height = self.window.height or self.ORB_SIZE
        if not self._revealed:
            screen_width, _ = self._screen_size()
            trigger_left = max(0, screen_width - self.ORB_VISIBLE_WIDTH - 10)
            return trigger_left <= x <= screen_width and top <= y <= top + height
        screen_width, _ = self._screen_size()
        return (
            left - self.HOVER_SLOP <= x <= screen_width
            and top - self.HOVER_SLOP <= y <= top + height + self.HOVER_SLOP
        )

    def _inside_main_window(self) -> bool:
        x, y = self._cursor_pos()
        left = self.window.x if isinstance(self.window.x, int) else 0
        top = self.window.y if isinstance(self.window.y, int) else 0
        width = self.window.width or self.APP_WIDTH
        height = self.window.height or self.APP_HEIGHT
        return left <= x <= left + width and top <= y <= top + height

    def _orb_detached_from_dock(self) -> bool:
        if self._orb_dock_x is None:
            return False
        left = self.window.x if isinstance(self.window.x, int) else 0
        return abs(left - self._orb_dock_x) > self.DOCK_MOVE_TOLERANCE

    def _main_attached_to_dock(self) -> bool:
        if self._main_dock_x is None:
            return False
        left = self.window.x if isinstance(self.window.x, int) else self._main_dock_x
        return abs(left - self._main_dock_x) <= self.DOCK_MOVE_TOLERANCE

    def _settings(self) -> tuple[bool, bool]:
        try:
            ui = load_settings()["ui"]
            return bool(ui.get("edge_dock_enabled", False)), bool(ui.get("edge_hover_listen", False))
        except Exception:
            return False, False

    def run(self):
        time.sleep(1.0)
        while self._running.is_set():
            enabled, hover_listen = self._settings()
            if not enabled:
                if self._main_requested.is_set():
                    self._main_requested.clear()
                    self._show_main(dock_to_edge=True)
                if self._active:
                    screen_width, _ = self._screen_size()
                    y = self._target_y(self.APP_HEIGHT)
                    self._set_hover_listening(False)
                    _clear_window_region(self.window)
                    _evaluate_js(self.window, "window.hermesSetDocked && window.hermesSetDocked(false);")
                    self.window.resize(self.APP_WIDTH, self.APP_HEIGHT)
                    self.window.move(screen_width - self.APP_WIDTH - self.EDGE_GAP, y)
                    _clear_window_region(self.window)
                    self._active = False
                    self._main_mode = False
                    self._revealed = False
                    self._orb_dock_x = None
                    self._main_dock_x = None
                    self._hover_started_at = 0.0
                time.sleep(0.5)
                continue

            if self._main_requested.is_set():
                self._main_requested.clear()
                self._show_main(dock_to_edge=True)

            if self._main_mode:
                now = time.time()
                if self._inside_main_window():
                    self._last_inside_at = now
                elif (
                    self._main_attached_to_dock()
                    and now > self._main_hold_until
                    and now - self._last_inside_at > 1.2
                ):
                    self._collapse()
                time.sleep(0.12)
                continue

            if not self._active:
                self._collapse()

            if self._orb_detached_from_dock():
                self._show_main(dock_to_edge=False)
                time.sleep(0.12)
                continue

            inside = self._inside_window()
            now = time.time()
            if inside:
                self._last_inside_at = now
                if not self._revealed:
                    self._reveal()
                elif not hover_listen and self._hover_listening:
                    self._set_hover_listening(False)
                elif hover_listen and not self._hover_listening and now - self._hover_started_at >= self.HOVER_LISTEN_DELAY_SECONDS:
                    self._set_hover_listening(True)
            elif self._revealed and now - self._last_inside_at > self.COLLAPSE_GRACE_SECONDS:
                self._collapse()

            time.sleep(0.12)


def main():
    global _edge_dock_controller
    host = get_host()
    port = get_port()
    settings = load_settings()

    logger.info("Starting backend server...")
    server_thread = UvicornThread(host, port)
    server_thread.start()
    wait_for_backend(host, port)

    logger.info("Launching desktop widget...")
    desktop_api = DesktopApi()
    hotkey_controller = None
    edge_dock_controller = None
    window = webview.create_window(
        title="Voice Assistant",
        url=f"http://{host}:{port}",
        width=360,
        height=560,
        frameless=True,
        on_top=bool(settings["ui"].get("always_on_top", True)),
        resizable=False,
        min_size=(80, 80),
        shadow=False,
        background_color="#0b0f24",
        transparent=False,
        easy_drag=False,
        draggable=True,
        js_api=desktop_api,
    )
    desktop_api._bind_window(window)

    def on_started():
        nonlocal hotkey_controller, edge_dock_controller
        global _tray_icon, _edge_dock_controller
        hotkey_controller = GlobalHotkeyController(window)
        hotkey_controller.start()
        edge_dock_controller = EdgeDockController(window)
        _edge_dock_controller = edge_dock_controller
        edge_dock_controller.start()
        _tray_icon = _create_tray_icon(window)
        if _tray_icon:
            _tray_icon.run_detached()
        if settings["ui"].get("start_minimized", False):
            window.hide()

    try:
        webview.start(on_started, gui="edgechromium", debug=False)
    finally:
        if hotkey_controller:
            hotkey_controller.stop()
        if edge_dock_controller:
            edge_dock_controller.stop()
            _edge_dock_controller = None
        if _tray_icon:
            _tray_icon.stop()
    server_thread.stop()
    logger.info("Shutdown complete.")


if __name__ == "__main__":
    main()
