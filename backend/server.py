"""FastAPI + WebSocket server for voice desktop widget."""
import asyncio
import logging
import os
import sys
from pathlib import Path

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse

sys.path.insert(0, str(Path(__file__).resolve().parent))

from voice_pipeline import VoicePipeline

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(message)s")
logger = logging.getLogger("server")

app = FastAPI(title="Voice Desktop Widget")

pipeline = VoicePipeline()
active_ws: WebSocket | None = None
_event_loop: asyncio.AbstractEventLoop | None = None
_stt_ready = False
_tts_ready = False


@app.on_event("startup")
async def startup():
    """Warm STT in the background so the HTTP server can listen immediately."""
    asyncio.create_task(_warm_stt_model())
    asyncio.create_task(_warm_tts_engine())


async def _warm_stt_model():
    global _stt_ready
    try:
        logger.info("Pre-loading STT model...")
        from stt_engine import _get_model

        await asyncio.to_thread(_get_model)
        _stt_ready = True
        logger.info("STT model ready.")
    except Exception:
        logger.exception("STT model warmup failed.")


async def _warm_tts_engine():
    global _tts_ready
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
        logger.exception("TTS engine warmup failed.")


@app.get("/")
async def root():
    return FileResponse(Path(__file__).resolve().parent.parent / "frontend" / "index.html")


@app.get("/health")
async def health():
    return {"ok": True, "stt_ready": _stt_ready, "tts_ready": _tts_ready}


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
                pipeline.start_recording()
                await ws.send_json({"type": "status", "state": "recording"})

            elif cmd == "stop_record":
                logger.info("Stop recording")
                wav_path = pipeline.stop_recording()
                await ws.send_json({"type": "status", "state": "processing"})

                try:
                    result = await pipeline.run_turn(wav_path)
                    await ws.send_json({
                        "type": "result",
                        "user": result["user"],
                        "assistant": result["assistant"],
                    })
                except Exception as e:
                    logger.exception("Pipeline error")
                    await ws.send_json({"type": "error", "message": str(e)})
                finally:
                    try:
                        os.unlink(wav_path)
                    except OSError:
                        pass

                await ws.send_json({"type": "status", "state": "idle"})

            elif cmd == "cancel":
                logger.info("Cancel recording")
                try:
                    pipeline.stop_recording()
                except Exception:
                    pass
                await ws.send_json({"type": "status", "state": "idle"})

    except WebSocketDisconnect:
        logger.info("WebSocket disconnected")
    finally:
        active_ws = None
        _event_loop = None


def main():
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=8765, log_level="info")


if __name__ == "__main__":
    main()
