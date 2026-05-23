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


from backend.api.ws_models import JobAssessment, Pong, SkillGap, Status

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
        """Serialize a wire-message model to JSON.

        Every payload sent over the WebSocket MUST be a Pydantic model from
        ``ws_models`` (which provides ``model_dump_json``). Callers that
        previously passed raw ``dict``s should construct the matching model
        instead. The runtime contract is enforced by the ``model_dump_json``
        call; the parameter type is ``Any`` because ``ws_models.BaseModel``
        carries a stubbed fallback class that pyright cannot reconcile with
        ``pydantic.BaseModel`` at type-check time.
        """
        return message.model_dump_json()

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
    # Cold-start path: app.state or batch_runner may not exist yet.
    # We log at debug so a noisy stack trace doesn't appear on every fresh
    # connect, but a developer running with `LOG_LEVEL=debug` can see why
    # the resume-state replay was skipped.
    try:
        app = websocket.app if hasattr(websocket, "app") else None
        runner = getattr(getattr(app, "state", None), "batch_runner", None) if app else None
        if runner and runner.running and runner.last_status:
            await websocket.send_text(manager._encode(runner.last_status))
    except Exception as exc:
        logger.debug("WS resume-state replay skipped: %s", exc)
    try:
        while True:
            try:
                data = await websocket.receive_text()
            except WebSocketDisconnect:
                break
            except Exception as exc:
                logger.debug("WS receive_text raised, looping: %s", exc)
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
            except Exception as exc:
                logger.debug("WS message dispatch failed: %s", exc)
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
    ``gaps`` are accepted as plain dicts for caller convenience and validated
    into ``SkillGap`` so the frontend contract stays tight.
    """
    await manager.broadcast(
        JobAssessment(
            match_id=match_id,
            ats_score=round(ats_score, 1),
            gap_severity=round(gap_severity, 3),
            decision=decision,
            covered=covered,
            gaps=[SkillGap(**g) for g in gaps],
        )
    )


__all__ = ["ConnectionManager", "manager", "router", "broadcast_status", "broadcast_job_assessment"]
