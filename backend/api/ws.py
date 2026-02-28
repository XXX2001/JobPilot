from __future__ import annotations

import asyncio
import importlib
import uuid
from typing import Dict
import json

try:
    from fastapi import APIRouter, WebSocket, WebSocketDisconnect
except Exception:  # pragma: no cover - fallback for environments without fastapi
    APIRouter = lambda: None  # type: ignore

    class WebSocketDisconnect(Exception):
        pass

    class WebSocket:
        async def accept(self):
            return None

        async def receive_text(self):
            raise WebSocketDisconnect()

        async def send_text(self, data: str):
            return None

    def APIRouter():  # type: ignore
        class _R:
            def websocket(self, path: str):
                def _decorator(func):
                    return func

                return _decorator

        return _R()


router = APIRouter()


class ConnectionManager:
    def __init__(self) -> None:
        self.active_connections: Dict[str, WebSocket] = {}
        self._lock = asyncio.Lock()

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

    async def broadcast(self, message) -> None:
        try:
            payload = message.model_dump_json()
        except Exception:
            payload = json.dumps(message)

        to_remove: list[str] = []
        for cid, ws in list(self.active_connections.items()):
            try:
                await ws.send_text(payload)
            except Exception:
                to_remove.append(cid)
        for cid in to_remove:
            self.disconnect(cid)

    async def send_to(self, client_id: str, message) -> None:
        try:
            payload = message.model_dump_json()
        except Exception:
            payload = json.dumps(message)
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
    try:
        while True:
            try:
                data = await websocket.receive_text()
            except WebSocketDisconnect:
                break
            except Exception:
                continue
            # Business logic deliberately omitted from handler per task
    finally:
        manager.disconnect(client_id)


__all__ = ["ConnectionManager", "manager", "router"]
