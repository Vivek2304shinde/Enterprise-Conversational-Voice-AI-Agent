from fastapi import FastAPI, Depends
from sqlalchemy.orm import Session

from .database import engine, Base, get_db
from . import models, schemas, twilio_webhook, media_stream_handler
from . import campaign_service, call_service

# Create tables
Base.metadata.create_all(bind=engine)

app = FastAPI(title="VoiceOps AI", version="0.1.0")

# Include routers
app.include_router(twilio_webhook.router)
app.include_router(media_stream_handler.router)

# --- API endpoints for frontend ---

@app.post("/campaigns", response_model=schemas.CampaignOut)
def create_campaign(campaign: schemas.CampaignCreate, db: Session = Depends(get_db)):
    return campaign_service.create_campaign(db, campaign)

@app.get("/campaigns", response_model=list[schemas.CampaignOut])
def list_campaigns(db: Session = Depends(get_db)):
    return campaign_service.get_campaigns(db)

@app.post("/customers", response_model=schemas.CustomerOut)
def create_customer(customer: schemas.CustomerCreate, db: Session = Depends(get_db)):
    return campaign_service.create_customer(db, customer)

@app.get("/customers", response_model=list[schemas.CustomerOut])
def list_customers(db: Session = Depends(get_db)):
    return campaign_service.get_customers(db)

@app.post("/campaigns/{campaign_id}/call/{customer_id}")
def start_call(campaign_id: int, customer_id: int, db: Session = Depends(get_db)):
    try:
        call_sid = call_service.initiate_outbound_call(db, customer_id, campaign_id)
        return {"call_sid": call_sid, "status": "initiated"}
    except Exception as e:
        return {"error": str(e)}, 400

@app.get("/calls", response_model=list[schemas.CallLogOut])
def list_calls(db: Session = Depends(get_db)):
    return campaign_service.get_call_logs(db)

@app.get("/calls/{call_sid}")
def get_call(call_sid: str, db: Session = Depends(get_db)):
    call_log = call_service.get_call_by_sid(db, call_sid)
    if not call_log:
        return {"error": "Call not found"}, 404
    return call_log

# Root health check
@app.get("/health")
def health():
    return {"status": "ok"}