# Add this to existing schemas.py

class CallUpdate(BaseModel):
    call_sid: str
    status: str
    transcript: Optional[str] = None
    sentiment: Optional[str] = None
    confidence: Optional[float] = None
    extracted_data: Optional[Dict[str, Any]] = None
    duration: Optional[int] = None
    timestamp: datetime = datetime.utcnow()