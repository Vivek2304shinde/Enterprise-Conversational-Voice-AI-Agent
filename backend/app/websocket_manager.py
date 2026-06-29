import json
import logging
from typing import Dict, Set
from fastapi import WebSocket

logger = logging.getLogger(__name__)

class ConnectionManager:
    def __init__(self):
        self.active_connections: Set[WebSocket] = set()

    async def connect(self, websocket: WebSocket):
        await websocket.accept()
        self.active_connections.add(websocket)
        logger.info(f"WebSocket connected: {websocket.client}")

    def disconnect(self, websocket: WebSocket):
        if websocket in self.active_connections:
            self.active_connections.remove(websocket)
            logger.info(f"WebSocket disconnected: {websocket.client}")

    async def broadcast(self, message: dict):
        """Send message to all connected clients."""
        if not self.active_connections:
            return
        data = json.dumps(message)
        for connection in self.active_connections.copy():
            try:
                await connection.send_text(data)
            except Exception as e:
                logger.error(f"Broadcast error: {e}")
                await self.disconnect(connection)

manager = ConnectionManager()