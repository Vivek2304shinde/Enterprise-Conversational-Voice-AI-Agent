from fastapi import WebSocket, WebSocketDisconnect
from .websocket_manager import manager

# ...

@app.websocket("/ws/dashboard")
async def websocket_dashboard(websocket: WebSocket):
    await manager.connect(websocket)
    try:
        while True:
            # Keep connection alive, client may send ping
            await websocket.receive_text()
    except WebSocketDisconnect:
        manager.disconnect(websocket)