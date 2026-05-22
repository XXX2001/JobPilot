from __future__ import annotations

import asyncio
import json
import logging
import uuid
from typing import Any, Dict

try:
    from fastapi import APIRouter, WebSocket, WebSocketDisconnect
except Exception:  # pragma: no cover - fallback for environments without fastapi

    def APIRouter():  # type: ignore
        class _R:
            def websocket(self, path: str):
                def _decorator(func):
                    return func

                return _decorator

        return _R()

    class WebSocketDisconnect(Exception):
        pass

    class WebSocket:
        async def accept(self):
            return None

        async def receive_text(self):
            raise WebSocketDisconnect()

        async def send_text(self, data: str):
            return None


from backend.api.ws_models import JobAssessment, Pong, Status

router = APIRouter()
logger = logging.getLogger(__name__)


class ConnectionManager:
    def __init__(self) -> None:
        self.active_connections: Dict[str, WebSocket] = {}
        self._lock = asyncio.Lock()
        # Message handler registry: type -> async callable(msg_dict, websocket)
        self._message_handlers: Dict[str, Any] = {}

    def register_handler(self, msg_type: str, handler) -> None:
        """Register a handler for a specific WS message type."""
        self._message_handlers[msg_type] = handler

    async def connect(self, websocket: WebSocket) -> str:
        try:
            await websocket.accept()
        except Exception:
            pass
        client_id = str(uuid.uuid4())
        async with self._lock:
            self.active_connections[client_id] = websocket
        return client_id

    def disconnect(self, client_id: str) -> None:
        self.active_connections.pop(client_id, None)

    @staticmethod
    def _encode(message: Any) -> str:
        """Serialize *message* to JSON.

        Accepts (in priority order):
          1. A Pydantic ``BaseModel`` (uses ``model_dump_json``).
          2. A ``dict`` already shaped like a wire message (json.dumps).
          3. Any other JSON-serializable value (json.dumps with default=str).

        All outgoing payloads SHOULD be Pydantic models from ``ws_models`` —
        the dict path exists only as a defensive fallback for legacy
        callers and the reconnect-replay of ``runner.last_status``.
        """
        dump = getattr(message, "model_dump_json", None)
        if callable(dump):
            try:
                result = dump()
                if isinstance(result, str):
                    return result
            except Exception:
                pass
        try:
            return json.dumps(message)
        except Exception:
            return json.dumps(message, default=str)

    async def broadcast(self, message: Any) -> None:
        payload = self._encode(message)
        to_remove: list[str] = []
        for cid, ws in list(self.active_connections.items()):
            try:
                await ws.send_text(payload)
            except Exception:
                to_remove.append(cid)
        for cid in to_remove:
            self.disconnect(cid)

    async def send_to(self, client_id: str, message: Any) -> None:
        payload = self._encode(message)
        ws = self.active_connections.get(client_id)
        if not ws:
            return
        try:
            await ws.send_text(payload)
        except Exception:
            self.disconnect(client_id)

manager = ConnectionManager()


@router.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket) -> None:
    client_id = await manager.connect(websocket)
    # Send current batch status to reconnecting clients
    try:
        app = websocket.app if hasattr(websocket, "app") else None
        runner = getattr(getattr(app, "state", None), "batch_runner", None) if app else None
        if runner and runner.running and runner.last_status:
            await websocket.send_text(json.dumps(runner.last_status))
    except Exception:
        pass
    try:
        while True:
            try:
                data = await websocket.receive_text()
            except WebSocketDisconnect:
                break
            except Exception:
                continue
            try:
                msg = json.loads(data)
                if not isinstance(msg, dict):
                    continue
                msg_type = msg.get("type")
                if msg_type == "ping":
                    await websocket.send_text(Pong().model_dump_json())
                elif msg_type in manager._message_handlers:
                    handler = manager._message_handlers[msg_type]
                    try:
                        handler(msg)
                    except Exception as exc:
                        logger.debug("Handler for %s failed: %s", msg_type, exc)
            except Exception:
                pass
    finally:
        manager.disconnect(client_id)

async def broadcast_status(message: str, progress: float = 0.0) -> None:
    """Broadcast a status update to all connected WebSocket clients.

    Wraps the payload in a ``ws_models.Status`` so the wire format is
    guaranteed to validate against the discriminated ``WSMessage`` union.
    """
    await manager.broadcast(Status(message=message, progress=progress))


async def broadcast_job_assessment(
    match_id: int,
    ats_score: float,
    gap_severity: float,
    decision: str,
    covered: list[str],
    gaps: list[dict],
) -> None:
    """Broadcast per-job fit assessment to all connected WebSocket clients.

    Wraps the payload in a ``ws_models.JobAssessment`` (wire-format
    discriminator stays ``"job_progress"`` for backward compatibility).
    """
    await manager.broadcast(
        JobAssessment(
            match_id=match_id,
            ats_score=round(ats_score, 1),
            gap_severity=round(gap_severity, 3),
            decision=decision,
            covered=covered,
            gaps=gaps,
        )
    )


__all__ = ["ConnectionManager", "manager", "router", "broadcast_status", "broadcast_job_assessment"]
