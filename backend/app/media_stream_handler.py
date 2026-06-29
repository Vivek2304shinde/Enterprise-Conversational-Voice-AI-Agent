from fastapi import WebSocket, WebSocketDisconnect, APIRouter
import base64
import json
import logging

from .agent import VoiceAgent
from .database import SessionLocal
from . import call_service

router = APIRouter(tags=["media"])
logger = logging.getLogger(__name__)


@router.websocket("/media-stream")
async def media_stream(websocket: WebSocket):
    await websocket.accept()
    call_sid = None
    stream_sid = None
    agent = None

    try:
        # First message should be "connected" or "start"
        message = await websocket.receive_text()
        data = json.loads(message)
        if data.get("event") == "connected":
            logger.info("Media stream connected")
            # Wait for start event
            message = await websocket.receive_text()
            data = json.loads(message)

        if data.get("event") == "start":
            stream_sid = data["streamSid"]
            call_sid = data["start"]["callSid"]
            logger.info(f"Media stream started for call {call_sid}, stream {stream_sid}")

            # Initialize the agent for this call
            agent = VoiceAgent(call_sid, stream_sid, websocket)

            # Update call status to in-progress
            db = SessionLocal()
            call_service.update_call_status(db, call_sid, "in-progress")
            db.close()
        else:
            logger.warning(f"Unexpected message: {data}")
            await websocket.close()
            return

        # Main loop: process incoming audio messages
        while True:
            message = await websocket.receive_text()
            data = json.loads(message)
            event = data.get("event")

            if event == "media":
                # Audio payload is base64-encoded μ-law
                payload = data["media"]["payload"]
                audio_bytes = base64.b64decode(payload)
                if agent:
                    await agent.process_audio(audio_bytes)

            elif event == "stop":
                logger.info(f"Media stream stopped for call {call_sid}")
                break

            elif event == "mark":
                # Optional: marker for playback synchronization
                pass

    except WebSocketDisconnect:
        logger.info(f"WebSocket disconnected for call {call_sid}")
    except Exception as e:
        logger.error(f"Error in media stream: {e}")
    finally:
        if agent:
            await agent.close()
        # Update call status to completed/failed (if not already)
        if call_sid:
            db = SessionLocal()
            # Only update if still in-progress
            call_log = call_service.get_call_by_sid(db, call_sid)
            if call_log and call_log.status == "in-progress":
                call_service.update_call_status(db, call_sid, "completed")
            db.close()