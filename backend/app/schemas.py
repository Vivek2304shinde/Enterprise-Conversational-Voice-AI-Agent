from pydantic import BaseModel
from datetime import datetime
from typing import Optional, Dict, Any

# Campaign schemas
class CampaignCreate(BaseModel):
    name: str
    voice: str = "professional_male"
    language: str = "en"
    call_time_start: Optional[str] = None
    call_time_end: Optional[str] = None
    retry_count: int = 3
    escalation_enabled: bool = True

class CampaignOut(BaseModel):
    id: int
    name: str
    voice: str
    language: str
    call_time_start: Optional[str]
    call_time_end: Optional[str]
    retry_count: int
    escalation_enabled: bool
    created_at: datetime
    updated_at: Optional[datetime]

    class Config:
        from_attributes = True

# Customer schemas
class CustomerCreate(BaseModel):
    name: str
    phone: str
    remaining_amount: float
    due_date: str
    months_pending: int

class CustomerOut(CustomerCreate):
    id: int

    class Config:
        from_attributes = True

# Call log schemas
class CallLogOut(BaseModel):
    id: int
    campaign_id: int
    customer_id: int
    twilio_sid: str
    status: str
    duration: int
    transcript: str
    extracted_data: Dict[str, Any]
    sentiment: str
    confidence: float
    started_at: datetime
    ended_at: Optional[datetime]

    class Config:
        from_attributes = True