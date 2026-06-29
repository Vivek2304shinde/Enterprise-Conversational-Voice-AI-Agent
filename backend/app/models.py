from sqlalchemy import Column, Integer, String, Float, DateTime, Boolean, JSON, Text
from sqlalchemy.sql import func
from .database import Base

class Campaign(Base):
    __tablename__ = "campaigns"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, nullable=False)
    voice = Column(String, default="professional_male")
    language = Column(String, default="en")
    call_time_start = Column(String, nullable=True)
    call_time_end = Column(String, nullable=True)
    retry_count = Column(Integer, default=3)
    escalation_enabled = Column(Boolean, default=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())


class Customer(Base):
    __tablename__ = "customers"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, nullable=False)
    phone = Column(String, unique=True, index=True, nullable=False)
    remaining_amount = Column(Float, nullable=False)
    due_date = Column(String, nullable=False)   # e.g., "2026-07-15"
    months_pending = Column(Integer, nullable=False)


class CallLog(Base):
    __tablename__ = "call_logs"

    id = Column(Integer, primary_key=True, index=True)
    campaign_id = Column(Integer, index=True, nullable=False)
    customer_id = Column(Integer, index=True, nullable=False)
    twilio_sid = Column(String, unique=True, index=True)
    status = Column(String, default="initiated")   # initiated, ringing, in-progress, completed, failed, busy, no-answer
    duration = Column(Integer, default=0)
    transcript = Column(Text, default="")
    extracted_data = Column(JSON, default={})
    sentiment = Column(String, default="neutral")
    confidence = Column(Float, default=0.0)
    started_at = Column(DateTime(timezone=True), server_default=func.now())
    ended_at = Column(DateTime(timezone=True), nullable=True)


class CallbackSchedule(Base):
    __tablename__ = "callback_schedules"

    id = Column(Integer, primary_key=True, index=True)
    call_log_id = Column(Integer, index=True, nullable=False)
    scheduled_date = Column(DateTime(timezone=True), nullable=False)
    status = Column(String, default="pending")   # pending, completed, failed