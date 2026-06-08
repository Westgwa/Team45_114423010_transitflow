import asyncio
import json
import threading
from typing import Optional, Set

from starlette.websockets import WebSocket


class NotificationManager:
    def __init__(self) -> None:
        self._connections: Set[WebSocket] = set()
        self._lock = threading.Lock()
        self._loop: Optional[asyncio.AbstractEventLoop] = None

    def set_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        self._loop = loop

    async def websocket_endpoint(self, websocket: WebSocket) -> None:
        await websocket.accept()
        with self._lock:
            self._connections.add(websocket)

        try:
            await websocket.send_text(json.dumps({"message": "Connected to TransitFlow notifications."}))
            while True:
                await websocket.receive_text()
        except Exception:
            pass
        finally:
            with self._lock:
                self._connections.discard(websocket)

    async def broadcast(self, payload: dict) -> None:
        if not self._connections:
            return

        text = json.dumps(payload)
        disconnected = []

        for websocket in list(self._connections):
            try:
                await websocket.send_text(text)
            except Exception:
                disconnected.append(websocket)

        if disconnected:
            with self._lock:
                for websocket in disconnected:
                    self._connections.discard(websocket)

    def notify(self, payload: dict) -> None:
        if self._loop is None:
            return
        try:
            asyncio.run_coroutine_threadsafe(self.broadcast(payload), self._loop)
        except Exception:
            pass


notifications = NotificationManager()
