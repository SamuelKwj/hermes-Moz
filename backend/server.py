"""FastAPI + WebSocket server for voice desktop widget."""
import asyncio
import logging
import os
import sys
from pathlib import Path

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse

sys.path.insert(0, str(Path(__file__).resolve().parent))

from app_runtime import (
    configure_logging,
    configure_model_cache,
    get_frontend_index,
    prepend_bundled_bin_to_path,
)
from voice_pipeline import VoicePipeline
from settings import get_host, get_port, load_settings, patch_settings
from system_checks import dependency_status, hermes_status, list_audio_devices, runtime_status

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


@app.get("/api/status")
async def status():
    settings = load_settings()
    return {
        "ok": True,
        "stt_ready": _stt_ready,
        "tts_ready": _tts_ready,
        "last_error": _last_error,
        "dependencies": dependency_status(),
        "runtime": runtime_status(),
        "hermes": await hermes_status(settings),
        "settings": settings,
    }


@app.get("/api/settings")
async def get_settings():
    return load_settings()


@app.post("/api/settings")
async def update_settings(payload: dict):
    return patch_settings(payload)


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

    def level_cb(rms: float):
        if active_ws and _event_loop and _event_loop.is_running():
            asyncio.run_coroutine_threadsafe(
                active_ws.send_json({"type": "level", "rms": rms}),
                _event_loop,
            )

    pipeline.set_level_callback(level_cb)

    try:
        while True:
            msg = await ws.receive_json()
            cmd = msg.get("cmd")

            if cmd == "start_record":
                logger.info("Start recording")
                try:
                    pipeline.start_recording()
                    await ws.send_json({"type": "status", "state": "recording"})
                except Exception as e:
                    logger.exception("Start recording failed")
                    await ws.send_json({"type": "error", "message": str(e)})
                    await ws.send_json({"type": "status", "state": "idle"})

            elif cmd == "stop_record":
                logger.info("Stop recording")
                wav_path = None

                try:
                    wav_path = pipeline.stop_recording()
                    await ws.send_json({"type": "status", "state": "processing"})
                    history = msg.get("history", [])
                    result = await pipeline.run_turn(wav_path, history)
                    await ws.send_json({
                        "type": "result",
                        "user": result["user"],
                        "assistant": result["assistant"],
                    })
                except Exception as e:
                    logger.exception("Pipeline error")
                    await ws.send_json({"type": "error", "message": str(e)})
                finally:
                    if wav_path:
                        try:
                            os.unlink(wav_path)
                        except OSError:
                            pass

                await ws.send_json({"type": "status", "state": "idle"})

            elif cmd == "cancel":
                logger.info("Cancel recording")
                pipeline.cancel_recording()
                await ws.send_json({"type": "status", "state": "idle"})

    except WebSocketDisconnect:
        logger.info("WebSocket disconnected")
    finally:
        active_ws = None
        _event_loop = None


def main():
    import uvicorn
    uvicorn.run(app, host=get_host(), port=get_port(), log_level="info")


if __name__ == "__main__":
    main()

