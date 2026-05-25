from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field
from time import time
from typing import Any, Callable

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pathlib import Path
from pydantic import BaseModel


class MoveDeltaRequest(BaseModel):
    dx: float = 0.0
    dy: float = 0.0
    dz: float = 0.0


class MoveInputRequest(BaseModel):
    dx: float = 0.0
    dy: float = 0.0
    dz: float = 0.0
    face_movement: bool = True


class RotateHumanRequest(BaseModel):
    delta_deg: float = 0.0


class CameraRequest(BaseModel):
    mode: str


class TaskRequest(BaseModel):
    task: str


class VideoSourceRequest(BaseModel):
    source: str


class SensorRangesRequest(BaseModel):
    visible: bool = False


@dataclass
class LiveSceneBridge:
    move_human: Callable[[float, float, float], dict[str, Any]] | None = None
    set_human_input: Callable[[float, float, float, bool], dict[str, Any]] | None = None
    rotate_human_heading: Callable[[float], dict[str, Any]] | None = None
    set_camera: Callable[[str], dict[str, Any]] | None = None
    run_task: Callable[[str], dict[str, Any]] | None = None
    reset_scene: Callable[[], dict[str, Any]] | None = None
    set_video_source: Callable[[str], dict[str, Any]] | None = None
    set_sensor_ranges_visible: Callable[[bool], dict[str, Any]] | None = None
    get_state: Callable[[], dict[str, Any]] | None = None
    clients: set[WebSocket] = field(default_factory=set)
    events: list[dict[str, Any]] = field(default_factory=list)
    latest_state: dict[str, Any] = field(default_factory=dict)
    latest_jpeg: bytes | None = None
    video_source: str = "viewer"
    video_frame_id: int = 0

    def log(self, event_type: str, payload: dict[str, Any]) -> None:
        event = {"type": event_type, "payload": payload, "timestamp": time()}
        self.events.append(event)
        self.events = self.events[-100:]
        print(f"[live-scene] {event_type}: {payload}", flush=True)
        try:
            LOG_DIR.mkdir(parents=True, exist_ok=True)
            with EVENT_LOG_PATH.open("a", encoding="utf-8") as f:
                f.write(json.dumps(event, ensure_ascii=False) + "\n")
        except OSError as exc:
            print(f"[live-scene] event_log_write_failed: {exc}", flush=True)

    def update_video_frame(self, jpeg: bytes, source: str) -> None:
        self.latest_jpeg = jpeg
        self.video_source = source
        self.video_frame_id += 1

    def snapshot(self) -> dict[str, Any]:
        if self.get_state is not None:
            self.latest_state = self.get_state()
        data = dict(self.latest_state)
        data["events"] = self.events[-20:]
        data["video"] = {
            "source": self.video_source,
            "frame_id": self.video_frame_id,
            "available": self.latest_jpeg is not None,
        }
        return data


bridge = LiveSceneBridge()
STATIC_DIR = Path(__file__).resolve().parent / "static"
LOG_DIR = Path(__file__).resolve().parents[2] / "logs"
EVENT_LOG_PATH = LOG_DIR / "live_scene_events.jsonl"

app = FastAPI(title="HomeSense Live OmniGibson Control")
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.get("/")
async def dashboard() -> FileResponse:
    return FileResponse(STATIC_DIR / "live.html")



@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok", "mode": "omnigibson-live"}


@app.get("/state")
async def get_state() -> dict[str, Any]:
    return bridge.snapshot()


@app.post("/command/move-human-delta")
async def move_human_delta(req: MoveDeltaRequest) -> dict[str, Any]:
    if bridge.move_human is None:
        bridge.log("move_human_blocked", {"reason": "scene runner not attached"})
    else:
        result = bridge.move_human(req.dx, req.dy, req.dz)
        bridge.log(result.get("event", "move_human_delta"), result)
    await broadcast_state()
    return bridge.snapshot()


@app.post("/command/set-human-input")
async def set_human_input(req: MoveInputRequest) -> dict[str, Any]:
    if bridge.set_human_input is None:
        bridge.log("set_human_input_blocked", {"reason": "scene runner not attached"})
    else:
        result = bridge.set_human_input(req.dx, req.dy, req.dz, req.face_movement)
        bridge.log(result.get("event", "set_human_input"), result)
    await broadcast_state()
    return bridge.snapshot()


@app.post("/command/rotate-human-heading")
async def rotate_human_heading(req: RotateHumanRequest) -> dict[str, Any]:
    if bridge.rotate_human_heading is None:
        bridge.log("rotate_human_heading_blocked", {"reason": "scene runner not attached"})
    else:
        result = bridge.rotate_human_heading(req.delta_deg)
        bridge.log(result.get("event", "rotate_human_heading"), result)
    await broadcast_state()
    return bridge.snapshot()


@app.post("/command/set-camera")
async def set_camera(req: CameraRequest) -> dict[str, Any]:
    if bridge.set_camera is None:
        bridge.log("set_camera_blocked", {"reason": "scene runner not attached", "mode": req.mode})
    else:
        result = bridge.set_camera(req.mode)
        bridge.log("set_camera", result)
    await broadcast_state()
    return bridge.snapshot()




@app.post("/command/set-video-source")
async def set_video_source(req: VideoSourceRequest) -> dict[str, Any]:
    if bridge.set_video_source is None:
        bridge.log("set_video_source_blocked", {"reason": "scene runner not attached", "source": req.source})
    else:
        result = bridge.set_video_source(req.source)
        bridge.log(result.get("event", "set_video_source"), result)
    await broadcast_state()
    return bridge.snapshot()


@app.post("/command/set-sensor-ranges-visible")
async def set_sensor_ranges_visible(req: SensorRangesRequest) -> dict[str, Any]:
    if bridge.set_sensor_ranges_visible is None:
        bridge.log("set_sensor_ranges_blocked", {"reason": "scene runner not attached", "visible": req.visible})
    else:
        result = bridge.set_sensor_ranges_visible(req.visible)
        bridge.log(result.get("event", "set_sensor_ranges_visible"), result)
    await broadcast_state()
    return bridge.snapshot()


@app.get("/video.mjpg")
async def video_stream() -> StreamingResponse:
    async def frames():
        last_frame_id = -1
        while True:
            if bridge.latest_jpeg is not None and bridge.video_frame_id != last_frame_id:
                last_frame_id = bridge.video_frame_id
                yield (
                    b"--frame\r\n"
                    b"Content-Type: image/jpeg\r\n"
                    b"Cache-Control: no-store\r\n\r\n"
                    + bridge.latest_jpeg
                    + b"\r\n"
                )
            await asyncio.sleep(0.05)

    return StreamingResponse(frames(), media_type="multipart/x-mixed-replace; boundary=frame")

@app.post("/command/run-task")
async def run_task(req: TaskRequest) -> dict[str, Any]:
    if bridge.run_task is None:
        bridge.log("task_blocked", {"reason": "scene runner not attached", "task": req.task})
    else:
        result = bridge.run_task(req.task)
        bridge.log(result.get("event", "task_started"), result)
    await broadcast_state()
    return bridge.snapshot()


@app.post("/command/reset")
async def reset() -> dict[str, Any]:
    if bridge.reset_scene is not None:
        bridge.log("reset", bridge.reset_scene())
    await broadcast_state()
    return bridge.snapshot()


@app.websocket("/ws")
async def websocket_endpoint(ws: WebSocket) -> None:
    await ws.accept()
    bridge.clients.add(ws)
    try:
        await ws.send_json(bridge.snapshot())
        while True:
            await ws.receive_text()
    except WebSocketDisconnect:
        bridge.clients.discard(ws)


async def broadcast_state() -> None:
    if not bridge.clients:
        return
    snapshot = bridge.snapshot()
    disconnected = []
    for ws in bridge.clients:
        try:
            await ws.send_json(snapshot)
        except RuntimeError:
            disconnected.append(ws)
    for ws in disconnected:
        bridge.clients.discard(ws)


async def periodic_broadcast() -> None:
    while True:
        await broadcast_state()
        await asyncio.sleep(0.1)


@app.on_event("startup")
async def startup() -> None:
    asyncio.create_task(periodic_broadcast())
