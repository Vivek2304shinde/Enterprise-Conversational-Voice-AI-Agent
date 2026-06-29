from sqlalchemy.orm import Session
from twilio.rest import Client
from datetime import datetime
import logging

from .config import settings
from .models import CallLog, Customer, Campaign

logger = logging.getLogger(__name__)

twilio_client = Client(settings.TWILIO_ACCOUNT_SID, settings.TWILIO_AUTH_TOKEN)


def initiate_outbound_call(db: Session, customer_id: int, campaign_id: int) -> str:
    """
    Place an outbound call via Twilio and create a CallLog entry.
    Returns the Twilio Call SID.
    """
    customer = db.query(Customer).filter(Customer.id == customer_id).first()
    campaign = db.query(Campaign).filter(Campaign.id == campaign_id).first()
    if not customer or not campaign:
        raise ValueError("Customer or campaign not found")

    # Create call log entry (without Twilio SID yet)
    call_log = CallLog(
        campaign_id=campaign_id,
        customer_id=customer_id,
        status="initiated",
        twilio_sid="",  # will be updated
    )
    db.add(call_log)
    db.commit()
    db.refresh(call_log)

    # Place call via Twilio
    try:
        call = twilio_client.calls.create(
            to=customer.phone,
            from_=settings.TWILIO_PHONE_NUMBER,
            url=f"{settings.BASE_URL}/twilio/incoming",
            status_callback=f"{settings.BASE_URL}/twilio/status",
            status_callback_event=["completed", "failed", "no-answer", "busy"],
            status_callback_method="POST",
        )
        # Update call log with Twilio SID and status
        call_log.twilio_sid = call.sid
        call_log.status = call.status
        db.commit()
        return call.sid
    except Exception as e:
        logger.error(f"Twilio call initiation failed: {e}")
        call_log.status = "failed"
        db.commit()
        raise


def update_call_status(db: Session, call_sid: str, status: str):
    """Update call log status and ended_at if terminal."""
    call_log = db.query(CallLog).filter(CallLog.twilio_sid == call_sid).first()
    if call_log:
        call_log.status = status
        if status in ["completed", "failed", "busy", "no-answer", "canceled"]:
            call_log.ended_at = datetime.utcnow()
        db.commit()
    else:
        logger.warning(f"Call log not found for SID {call_sid}")


def get_call_by_sid(db: Session, call_sid: str):
    return db.query(CallLog).filter(CallLog.twilio_sid == call_sid).first()


def update_transcript(db: Session, call_sid: str, transcript: str):
    call_log = db.query(CallLog).filter(CallLog.twilio_sid == call_sid).first()
    if call_log:
        call_log.transcript = transcript
        db.commit()


def update_extracted_data(db: Session, call_sid: str, data: dict):
    call_log = db.query(CallLog).filter(CallLog.twilio_sid == call_sid).first()
    if call_log:
        call_log.extracted_data = data
        db.commit()