from fastapi import APIRouter, Request, Response, Depends
from sqlalchemy.orm import Session
from twilio.twiml.voice_response import VoiceResponse, Connect, Stream

from .config import settings
from .database import get_db
from . import call_service
import logging

router = APIRouter(prefix="/twilio", tags=["twilio"])
logger = logging.getLogger(__name__)


@router.post("/incoming")
async def incoming_call(request: Request):
    """
    Twilio webhook for incoming/outbound calls.
    Returns TwiML that instructs Twilio to start a Media Stream.
    """
    form = await request.form()
    call_sid = form.get("CallSid")
    logger.info(f"Incoming call SID: {call_sid}")

    # Build TwiML response
    response = VoiceResponse()
    connect = Connect()
    # WebSocket URL for Media Streams – must match our endpoint
    ws_url = f"wss://{settings.BASE_URL.replace('http://','').replace('https://','')}/media-stream"
    stream = Stream(url=ws_url)
    connect.append(stream)
    response.append(connect)

    return Response(content=str(response), media_type="application/xml")


@router.post("/status")
async def status_callback(request: Request, db: Session = Depends(get_db)):
    """
    Receive call status updates from Twilio.
    """
    form = await request.form()
    call_sid = form.get("CallSid")
    call_status = form.get("CallStatus")
    logger.info(f"Call {call_sid} status: {call_status}")

    # Update call log in database
    call_service.update_call_status(db, call_sid, call_status)
    return Response(status_code=200)