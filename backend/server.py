"""FastAPI + WebSocket server for voice desktop widget."""
import asyncio
import logging
import os
import sys
import time
from pathlib import Path

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, HTMLResponse

sys.path.insert(0, str(Path(__file__).resolve().parent))

from app_runtime import (
    configure_logging,
    configure_model_cache,
    get_icon_file,
    get_frontend_index,
    prepend_bundled_bin_to_path,
)
from voice_pipeline import VoicePipeline
from tts_engine import synthesize
from settings import PERFORMANCE_PROFILES, get_host, get_port, load_settings, patch_settings
from system_checks import accelerator_status, dependency_status, hermes_status, list_audio_devices, runtime_status

prepend_bundled_bin_to_path()
configure_model_cache()
configure_logging()
logger = logging.getLogger("server")

app = FastAPI(title="Voice Desktop Widget")

pipeline = VoicePipeline()
active_ws: WebSocket | None = None
_event_loop: asyncio.AbstractEventLoop | None = None
_stt_ready = False
_tts_ready = False
_last_error: str | None = None


@app.on_event("startup")
async def startup():
    """Warm STT in the background so the HTTP server can listen immediately."""
    asyncio.create_task(_warm_stt_model())
    asyncio.create_task(_warm_tts_engine())


async def _warm_stt_model():
    global _stt_ready, _last_error
    try:
        logger.info("Pre-loading STT model...")
        from stt_engine import _get_model

        await asyncio.to_thread(_get_model)
        _stt_ready = True
        _last_error = None
        logger.info("STT model ready.")
    except Exception:
        _last_error = "STT model warmup failed."
        logger.exception("STT model warmup failed.")


async def _warm_tts_engine():
    global _tts_ready, _last_error
    try:
        logger.info("Pre-warming TTS engine...")
        from tts_engine import synthesize

        path = await synthesize("语音助手已启动。")
        try:
            os.unlink(path)
        except OSError:
            pass
        _tts_ready = True
        logger.info("TTS engine ready.")
    except Exception:
        _last_error = "TTS engine warmup failed."
        logger.exception("TTS engine warmup failed.")


@app.get("/")
async def root():
    return FileResponse(get_frontend_index())


@app.get("/favicon.ico", include_in_schema=False)
async def favicon():
    return FileResponse(get_icon_file())


@app.get("/api/config.js")
async def frontend_config():
    from fastapi.responses import Response

    script = (
        "window.VOICE_WIDGET_CONFIG = "
        f"{{ host: {get_host()!r}, port: {get_port()} }};"
    )
    return Response(content=script, media_type="application/javascript")


@app.get("/health")
async def health():
    return {"ok": True, "stt_ready": _stt_ready, "tts_ready": _tts_ready, "last_error": _last_error}


@app.get("/tts-voices")
async def tts_voices_page():
    page = """
<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Edge TTS Voices</title>
  <style>
    body { margin: 0; background: #101117; color: #f4f1ff; font: 14px/1.5 system-ui, sans-serif; }
    main { max-width: 980px; margin: 0 auto; padding: 24px; }
    h1 { font-size: 22px; margin: 0 0 12px; }
    .bar { display: flex; gap: 8px; flex-wrap: wrap; margin-bottom: 16px; }
    button { border: 1px solid #343548; border-radius: 8px; background: #191b25; color: #f4f1ff; padding: 8px 12px; cursor: pointer; }
    button.active { border-color: #a29bfe; background: #2d265e; }
    input { min-width: 240px; flex: 1; border: 1px solid #343548; border-radius: 8px; background: #151720; color: #f4f1ff; padding: 8px 10px; }
    .count { color: #a9a6bf; margin: 10px 0; }
    table { width: 100%; border-collapse: collapse; background: #151720; border-radius: 8px; overflow: hidden; }
    th, td { border-bottom: 1px solid #292b3a; padding: 9px 10px; text-align: left; vertical-align: top; }
    th { color: #c9c3ff; background: #1d2030; font-weight: 600; }
    code { color: #9ee7d8; }
  </style>
</head>
<body>
<main>
  <h1>Edge TTS 音色</h1>
  <div class="bar">
    <button data-locale="zh" class="active">中文</button>
    <button data-locale="zh-CN">大陆中文</button>
    <button data-locale="zh-HK">粤语/香港</button>
    <button data-locale="zh-TW">台湾中文</button>
    <button data-locale="all">全部</button>
    <input id="q" placeholder="搜索 ShortName / FriendlyName / Locale">
  </div>
  <div class="count" id="count">加载中...</div>
  <table>
    <thead><tr><th>ShortName</th><th>Locale</th><th>Gender</th><th>FriendlyName</th></tr></thead>
    <tbody id="rows"></tbody>
  </table>
</main>
<script>
let voices = [];
let locale = "zh";
const rows = document.getElementById("rows");
const count = document.getElementById("count");
const q = document.getElementById("q");

function escapeHtml(text) {
  return String(text || "").replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

async function loadVoices(nextLocale) {
  locale = nextLocale;
  document.querySelectorAll("button[data-locale]").forEach(btn => btn.classList.toggle("active", btn.dataset.locale === locale));
  count.textContent = "加载中...";
  const res = await fetch("/api/tts/voices?locale=" + encodeURIComponent(locale), { cache: "no-store" });
  const data = await res.json();
  voices = data.voices || [];
  render();
}

function render() {
  const needle = q.value.trim().toLowerCase();
  const filtered = voices.filter(v => !needle || [v.short_name, v.friendly_name, v.locale, v.gender].join(" ").toLowerCase().includes(needle));
  count.textContent = "显示 " + filtered.length + " / " + voices.length + " 个音色";
  rows.innerHTML = filtered.map(v => "<tr><td><code>" + escapeHtml(v.short_name) + "</code></td><td>" + escapeHtml(v.locale) + "</td><td>" + escapeHtml(v.gender) + "</td><td>" + escapeHtml(v.friendly_name) + "</td></tr>").join("");
}

document.querySelectorAll("button[data-locale]").forEach(btn => btn.addEventListener("click", () => loadVoices(btn.dataset.locale)));
q.addEventListener("input", render);
loadVoices("zh");
</script>
</body>
</html>
"""
    return HTMLResponse(page)


@app.get("/api/tts/voices")
async def tts_voices(locale: str = "zh"):
    from edge_tts import list_voices

    voices = await list_voices()
    locale = (locale or "zh").strip()
    if locale and locale != "all":
        if locale == "zh":
            voices = [voice for voice in voices if str(voice.get("Locale", "")).startswith("zh")]
        else:
            voices = [voice for voice in voices if voice.get("Locale") == locale]

    simplified = [
        {
            "short_name": voice.get("ShortName"),
            "friendly_name": voice.get("FriendlyName"),
            "locale": voice.get("Locale"),
            "gender": voice.get("Gender"),
        }
        for voice in voices
    ]
    simplified.sort(key=lambda item: (str(item.get("locale")), str(item.get("short_name"))))
    return {"count": len(simplified), "voices": simplified}


@app.get("/api/status")
async def status():
    settings = load_settings()
    return {
        "ok": True,
        "stt_ready": _stt_ready,
        "tts_ready": _tts_ready,
        "last_error": _last_error,
        "dependencies": dependency_status(),
        "accelerator": accelerator_status(),
        "runtime": runtime_status(),
        "hermes": await hermes_status(settings),
        "performance_profiles": PERFORMANCE_PROFILES,
        "stt": _stt_status(),
        "stt_models": _stt_models(),
        "settings": settings,
    }


@app.get("/api/settings")
async def get_settings():
    return load_settings()


@app.post("/api/settings")
async def update_settings(payload: dict):
    global _stt_ready, _last_error
    before = load_settings()["stt"]
    saved = patch_settings(payload)
    pipeline.update_hands_free_settings(saved)
    after = saved["stt"]
    if before != after:
        from stt_engine import reset_model

        reset_model()
        _stt_ready = False
        _last_error = None
        asyncio.create_task(_warm_stt_model())
    return saved


@app.post("/api/setup/complete")
async def complete_setup():
    return patch_settings({"setup": {"first_run_complete": True}})


@app.get("/api/devices")
async def devices():
    return list_audio_devices()


@app.websocket("/ws")
async def websocket_endpoint(ws: WebSocket):
    global active_ws, _event_loop
    await ws.accept()
    active_ws = ws
    _event_loop = asyncio.get_running_loop()
    logger.info("WebSocket connected")
    send_lock = asyncio.Lock()
    turn_task: asyncio.Task | None = None
    wake_prompt_task: asyncio.Task | None = None
    turn_generation = 0
    hands_free_enabled = False
    awake_until = 0.0
    hands_free_history: list[dict] = []

    async def send_json(payload: dict):
        async with send_lock:
            await ws.send_json(payload)

    def level_cb(rms: float):
        if active_ws is ws and _event_loop and _event_loop.is_running():
            asyncio.run_coroutine_threadsafe(
                send_json({"type": "level", "rms": rms}),
                _event_loop,
            )

    pipeline.set_level_callback(level_cb)

    def wake_enabled() -> bool:
        return bool(load_settings()["ui"].get("wake_word_enabled", False))

    def is_awake() -> bool:
        return (not wake_enabled()) or time.monotonic() < awake_until

    def extend_awake(seconds: float) -> None:
        nonlocal awake_until
        awake_until = time.monotonic() + max(0.0, seconds)

    def interrupt_turn() -> None:
        nonlocal turn_task, wake_prompt_task, turn_generation
        turn_generation += 1
        pipeline.interrupt_playback()
        if wake_prompt_task and not wake_prompt_task.done():
            wake_prompt_task.cancel()
        wake_prompt_task = None
        if turn_task and not turn_task.done():
            turn_task.cancel()
        turn_task = None

    def begin_pipeline_turn(
        wav_path: str,
        history: list | None = None,
        require_wake: bool = False,
        hands_free_turn: bool = False,
    ) -> None:
        nonlocal turn_task, turn_generation
        interrupt_turn()
        pipeline.pause_hands_free(9999.0)
        turn_generation += 1
        turn_task = asyncio.create_task(
            run_pipeline_turn(wav_path, history or [], turn_generation, require_wake, hands_free_turn)
        )

    def begin_text_turn(text: str, history: list | None = None) -> None:
        nonlocal turn_task, turn_generation
        interrupt_turn()
        pipeline.pause_hands_free(9999.0)
        turn_generation += 1
        turn_task = asyncio.create_task(
            run_text_pipeline_turn(text, history or [], turn_generation)
        )

    async def send_hands_free_state(state: str) -> None:
        if state == "listening":
            await send_json({"type": "status", "state": "listening"})
        elif state == "recording":
            await send_json({"type": "status", "state": "recording"})
        elif state == "processing":
            await send_json({"type": "status", "state": "processing"})

    def start_hands_free_if_needed() -> bool:
        nonlocal hands_free_enabled
        if hands_free_enabled:
            return False
        loop = asyncio.get_running_loop()

        def on_submit(wav_path: str):
            settings = load_settings()
            require_wake = bool(settings["ui"].get("wake_word_enabled", False)) and not is_awake()
            history = [] if require_wake else hands_free_history[-8:]
            loop.call_soon_threadsafe(begin_pipeline_turn, wav_path, history, require_wake, True)

        def on_speech_start():
            loop.call_soon_threadsafe(interrupt_turn)

        def on_state(state: str):
            asyncio.run_coroutine_threadsafe(send_hands_free_state(state), loop)

        pipeline.start_hands_free(on_submit, on_speech_start, on_state)
        hands_free_enabled = True
        return True

    def stop_hands_free_if_needed() -> None:
        nonlocal hands_free_enabled
        if not hands_free_enabled:
            return
        pipeline.stop_hands_free()
        hands_free_enabled = False

    async def play_wake_prompt(generation: int) -> None:
        settings = load_settings()
        reply = str(settings["ui"].get("wake_reply", "我在，大王请说。"))
        path = None
        try:
            pipeline.pause_hands_free(9999.0)
            path = await synthesize(reply)
            if generation != turn_generation:
                return
            await pipeline._play_audio(path)
        except asyncio.CancelledError:
            logger.info("Wake prompt interrupted.")
            raise
        except Exception:
            logger.exception("Wake prompt TTS failed.")
        finally:
            if path:
                try:
                    os.unlink(path)
                except OSError:
                    pass
            if generation == turn_generation and hands_free_enabled:
                resume_delay = float(load_settings()["audio"].get("hands_free_resume_delay_seconds", 0.45))
                pipeline.pause_hands_free(resume_delay, replace=True)
        await send_json({"type": "wake_prompt", "text": reply})

    async def run_pipeline_turn(
        wav_path: str,
        history: list,
        generation: int,
        require_wake: bool = False,
        hands_free_turn: bool = False,
    ) -> None:
        nonlocal turn_task, awake_until
        try:
            async def send_pipeline_event(event: dict):
                nonlocal awake_until, wake_prompt_task
                if generation != turn_generation:
                    return
                event_type = event.get("type")
                if event_type == "user":
                    await send_json({"type": "user_transcript", "text": event.get("text", "")})
                elif event_type == "assistant_delta":
                    await send_json({"type": "assistant_delta", "delta": event.get("delta", "")})
                elif event_type == "assistant_done":
                    if hands_free_turn and load_settings()["ui"].get("wake_word_enabled", False):
                        settings = load_settings()
                        extend_awake(float(settings["ui"].get("wake_followup_seconds", 30.0)))
                    await send_json({"type": "assistant_done", "assistant": event.get("assistant", "")})
                elif event_type == "wake_ignored":
                    await send_json({"type": "wake_ignored"})
                elif event_type == "wake_prompt":
                    settings = load_settings()
                    hands_free_history.clear()
                    extend_awake(float(settings["ui"].get("wake_window_seconds", 8.0)))
                    if wake_prompt_task and not wake_prompt_task.done():
                        wake_prompt_task.cancel()
                    wake_prompt_task = asyncio.create_task(play_wake_prompt(generation))

            result = await pipeline.run_turn_stream(wav_path, history, send_pipeline_event, require_wake=require_wake)
            if hands_free_turn and result and not result.get("ignored") and not result.get("wake_prompt"):
                user_text = str(result.get("user", "")).strip()
                assistant_text = str(result.get("assistant", "")).strip()
                if user_text:
                    hands_free_history.append({"role": "user", "content": user_text})
                if assistant_text:
                    hands_free_history.append({"role": "assistant", "content": assistant_text})
                del hands_free_history[:-8]
                if load_settings()["ui"].get("wake_word_enabled", False):
                    extend_awake(float(load_settings()["ui"].get("wake_followup_seconds", 30.0)))
        except asyncio.CancelledError:
            logger.info("Pipeline turn interrupted.")
        except Exception as e:
            if generation == turn_generation:
                logger.exception("Pipeline error")
                await send_json({"type": "error", "message": str(e)})
        finally:
            try:
                os.unlink(wav_path)
            except OSError:
                pass
            if generation == turn_generation:
                if hands_free_enabled:
                    settings = load_settings()
                    resume_delay = float(settings["audio"].get("hands_free_resume_delay_seconds", 0.45))
                    pipeline.pause_hands_free(resume_delay, replace=True)
                await send_json({"type": "status", "state": "listening" if hands_free_enabled else "idle"})
                turn_task = None

    async def run_text_pipeline_turn(text: str, history: list, generation: int) -> None:
        nonlocal turn_task
        try:
            async def send_pipeline_event(event: dict):
                if generation != turn_generation:
                    return
                event_type = event.get("type")
                if event_type == "user":
                    await send_json({"type": "user_transcript", "text": event.get("text", "")})
                elif event_type == "assistant_delta":
                    await send_json({"type": "assistant_delta", "delta": event.get("delta", "")})
                elif event_type == "assistant_done":
                    await send_json({"type": "assistant_done", "assistant": event.get("assistant", "")})

            await pipeline.run_text_turn_stream(text, history, send_pipeline_event)
        except asyncio.CancelledError:
            logger.info("Text pipeline turn interrupted.")
        except Exception as e:
            if generation == turn_generation:
                logger.exception("Text pipeline error")
                await send_json({"type": "error", "message": str(e)})
        finally:
            if generation == turn_generation:
                if hands_free_enabled:
                    settings = load_settings()
                    resume_delay = float(settings["audio"].get("hands_free_resume_delay_seconds", 0.45))
                    pipeline.pause_hands_free(resume_delay, replace=True)
                await send_json({"type": "status", "state": "listening" if hands_free_enabled else "idle"})
                turn_task = None

    try:
        while True:
            msg = await ws.receive_json()
            cmd = msg.get("cmd")

            if cmd == "start_record":
                logger.info("Start recording")
                try:
                    stop_hands_free_if_needed()
                    interrupt_turn()
                    pipeline.start_recording()
                    await send_json({"type": "status", "state": "recording"})
                except Exception as e:
                    logger.exception("Start recording failed")
                    await send_json({"type": "error", "message": str(e)})
                    await send_json({"type": "status", "state": "idle"})

            elif cmd == "stop_record":
                logger.info("Stop recording")
                try:
                    wav_path = pipeline.stop_recording()
                    await send_json({"type": "status", "state": "processing"})
                    history = msg.get("history", [])
                    begin_pipeline_turn(wav_path, history)
                except Exception as e:
                    logger.exception("Stop recording failed")
                    await send_json({"type": "error", "message": str(e)})
                    await send_json({"type": "status", "state": "idle"})

            elif cmd == "text_message":
                text = str(msg.get("text", "")).strip()
                if text:
                    logger.info("Text message")
                    await send_json({"type": "status", "state": "processing"})
                    begin_text_turn(text, msg.get("history", []))

            elif cmd == "cancel":
                logger.info("Cancel recording")
                interrupt_turn()
                pipeline.cancel_recording()
                await send_json({"type": "status", "state": "idle"})

            elif cmd == "set_hands_free":
                enabled = bool(msg.get("enabled"))
                logger.info("Set hands-free listening: %s", enabled)
                try:
                    if enabled:
                        pipeline.update_hands_free_settings(load_settings())
                        started = start_hands_free_if_needed()
                        if started or turn_task is None:
                            await send_json({"type": "status", "state": "listening"})
                    else:
                        stop_hands_free_if_needed()
                        await send_json({"type": "status", "state": "idle"})
                except Exception as e:
                    logger.exception("Hands-free mode failed")
                    stop_hands_free_if_needed()
                    await send_json({"type": "error", "message": str(e)})
                    await send_json({"type": "status", "state": "idle"})

    except WebSocketDisconnect:
        logger.info("WebSocket disconnected")
    finally:
        stop_hands_free_if_needed()
        interrupt_turn()
        if active_ws is ws:
            active_ws = None
            _event_loop = None


def main():
    import uvicorn
    uvicorn.run(app, host=get_host(), port=get_port(), log_level="info")


def _stt_status() -> dict:
    try:
        from stt_engine import status as stt_status

        return stt_status()
    except Exception as exc:
        return {"error": str(exc)}


def _stt_models() -> list[dict]:
    try:
        from stt_engine import model_inventory

        return model_inventory()
    except Exception:
        logger.exception("Failed to collect STT model inventory.")
        return []


if __name__ == "__main__":
    main()

