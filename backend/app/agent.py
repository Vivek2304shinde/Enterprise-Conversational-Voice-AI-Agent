import asyncio
import base64
import json
import logging
from typing import Optional, Dict, Any
from datetime import datetime
import aiohttp

from deepgram import DeepgramClient, LiveTranscriptionEvents, LiveOptions
from openai import AsyncOpenAI

from .config import settings
from .websocket_manager import manager
from . import call_service
from .database import SessionLocal

logger = logging.getLogger(__name__)

CARTESIA_TTS_URL = "https://api.cartesia.ai/tts/stream"
CARTESIA_VOICE_ID = "79a125e8-cd45-4c13-8a67-188112f4dd22"

class VoiceAgent:
    def __init__(self, call_sid: str, stream_sid: str, websocket):
        self.call_sid = call_sid
        self.stream_sid = stream_sid
        self.websocket = websocket
        self.deepgram = DeepgramClient(settings.DEEPGRAM_API_KEY)
        self.openai = AsyncOpenAI(api_key=settings.OPENAI_API_KEY)

        self.is_speaking = False
        self.transcript_buffer = ""
        self.full_transcript = ""  # accumulate all user utterances
        self.dg_connection = None
        self.llm_task: Optional[asyncio.Task] = None
        self.tts_queue = asyncio.Queue()
        self.tts_task: Optional[asyncio.Task] = None
        self.running = True
        self.last_sentiment = "neutral"
        self.last_extracted = {}

        self.tts_task = asyncio.create_task(self._tts_playback())
        self._setup_deepgram()

        # Broadcast initial status
        asyncio.create_task(self._broadcast_update(status="in-progress"))

    def _setup_deepgram(self):
        try:
            self.dg_connection = self.deepgram.listen.websocket.v("1")
            self.dg_connection.on(LiveTranscriptionEvents.Transcript, self._on_transcript)
            self.dg_connection.on(LiveTranscriptionEvents.UtteranceEnd, self._on_utterance_end)

            options = LiveOptions(
                model="nova-2",
                language="en-US",
                smart_format=True,
                endpointing=300,
                vad_events=True,
                interim_results=False,
            )
            self.dg_connection.start(options)
            logger.info(f"Deepgram started for call {self.call_sid}")
        except Exception as e:
            logger.error(f"Deepgram start error: {e}")

    async def process_audio(self, audio_bytes: bytes):
        if self.dg_connection and self.running:
            try:
                self.dg_connection.send(audio_bytes)
            except Exception as e:
                logger.error(f"Deepgram send error: {e}")

    def _on_transcript(self, result, **kwargs):
        if result.is_final:
            transcript = result.channel.alternatives[0].transcript
            if transcript.strip():
                logger.info(f"User said: {transcript}")
                self.transcript_buffer += " " + transcript
                self.full_transcript += " " + transcript
                # Broadcast live transcript to dashboard
                asyncio.create_task(self._broadcast_update(transcript=transcript))
                if not self.is_speaking and (self.llm_task is None or self.llm_task.done()):
                    user_text = self.transcript_buffer.strip()
                    self.transcript_buffer = ""
                    self.llm_task = asyncio.create_task(self._get_llm_response(user_text))

    def _on_utterance_end(self, *args, **kwargs):
        if not self.is_speaking and self.transcript_buffer.strip():
            if self.llm_task is None or self.llm_task.done():
                user_text = self.transcript_buffer.strip()
                self.transcript_buffer = ""
                self.llm_task = asyncio.create_task(self._get_llm_response(user_text))

    async def _analyze_sentiment_and_extract(self, user_input: str, ai_reply: str) -> Dict[str, Any]:
        """Call OpenAI to get sentiment and extract structured data."""
        prompt = f"""
        You are an AI assistant that analyzes conversations.
        Given the user input and the AI reply, extract:
        - sentiment: one of [positive, neutral, negative, frustrated, angry]
        - intent: one of [payment_today, partial_payment, schedule_callback, transfer_human, other]
        - payment_date: if user mentions a specific date, extract in YYYY-MM-DD format, else null
        - promised_amount: if user mentions an amount, extract as number, else null
        - callback_required: true if user asks for a callback or future call, else false

        Return ONLY a JSON object with keys: sentiment, intent, payment_date, promised_amount, callback_required.

        User: {user_input}
        AI: {ai_reply}
        """
        try:
            response = await self.openai.chat.completions.create(
                model="gpt-4o-mini",
                messages=[{"role": "user", "content": prompt}],
                max_tokens=150,
                temperature=0.2,
                response_format={"type": "json_object"}
            )
            result = json.loads(response.choices[0].message.content)
            logger.info(f"Extracted: {result}")
            return result
        except Exception as e:
            logger.error(f"Extraction error: {e}")
            return {
                "sentiment": "neutral",
                "intent": "other",
                "payment_date": None,
                "promised_amount": None,
                "callback_required": False
            }

    async def _get_llm_response(self, user_input: str):
        self.is_speaking = True
        try:
            system_prompt = (
                "You are Alex, a professional collections agent calling on behalf of ABC Finance. "
                "Your goal is to remind customers about overdue payments, be polite and empathetic. "
                "If the customer says they will pay on a specific date, confirm that date and say you will schedule a callback. "
                "If they ask for a human, say you will transfer them to an agent. "
                "Keep responses short and conversational. "
                "Do not threaten or argue."
            )
            response = await self.openai.chat.completions.create(
                model="gpt-4o-mini",
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_input}
                ],
                max_tokens=150,
                temperature=0.7,
            )
            reply = response.choices[0].message.content.strip()
            logger.info(f"AI reply: {reply}")

            # Sentiment & extraction
            extracted = await self._analyze_sentiment_and_extract(user_input, reply)
            sentiment = extracted.get("sentiment", "neutral")
            self.last_sentiment = sentiment
            self.last_extracted = extracted

            # Broadcast update with transcript, sentiment, extracted data
            await self._broadcast_update(
                transcript=f"AI: {reply}",
                sentiment=sentiment,
                extracted_data=extracted,
                confidence=0.85  # placeholder
            )

            # Save to DB
            db = SessionLocal()
            call_service.update_transcript(db, self.call_sid, self.full_transcript)
            call_service.update_extracted_data(db, self.call_sid, extracted)
            db.close()

            # Synthesize and send TTS
            await self._synthesize_and_send(reply)

        except Exception as e:
            logger.error(f"LLM error: {e}")
            await self._synthesize_and_send("I'm sorry, I'm having trouble. Please hold.")
        finally:
            self.is_speaking = False

    async def _synthesize_and_send(self, text: str):
        if not text:
            return
        try:
            headers = {
                "Content-Type": "application/json",
                "Cartesia-Version": "2024-06-10",
                "X-API-Key": settings.CARTESIA_API_KEY
            }
            payload = {
                "text": text,
                "voice_id": CARTESIA_VOICE_ID,
                "model_id": "sonic-english",
                "output_format": {
                    "container": "raw",
                    "encoding": "pcm_mulaw",
                    "sample_rate": 8000
                }
            }

            async with aiohttp.ClientSession() as session:
                async with session.post(CARTESIA_TTS_URL, json=payload, headers=headers) as resp:
                    if resp.status != 200:
                        error_text = await resp.text()
                        logger.error(f"Cartesia error: {resp.status} - {error_text}")
                        return
                    async for chunk in resp.content.iter_chunks():
                        data, _ = chunk
                        if data:
                            audio_b64 = base64.b64encode(data).decode('utf-8')
                            await self.tts_queue.put(audio_b64)
        except Exception as e:
            logger.error(f"TTS synthesis error: {e}")

    async def _tts_playback(self):
        while self.running:
            try:
                audio_b64 = await self.tts_queue.get()
                if audio_b64 is None:
                    break
                payload = {
                    "event": "media",
                    "streamSid": self.stream_sid,
                    "media": {
                        "payload": audio_b64
                    }
                }
                await self.websocket.send(json.dumps(payload))
            except Exception as e:
                logger.error(f"TTS playback error: {e}")
                break

    async def _broadcast_update(self, **kwargs):
        """Send update to all dashboard clients via WebSocket."""
        update = {
            "call_sid": self.call_sid,
            "status": kwargs.get("status", "in-progress"),
            "timestamp": datetime.utcnow().isoformat(),
            "transcript": kwargs.get("transcript"),
            "sentiment": kwargs.get("sentiment", self.last_sentiment),
            "confidence": kwargs.get("confidence", 0.0),
            "extracted_data": kwargs.get("extracted_data", self.last_extracted),
        }
        await manager.broadcast(update)

    async def close(self):
        self.running = False
        if self.dg_connection:
            try:
                self.dg_connection.finish()
            except:
                pass
        if self.tts_task and not self.tts_task.done():
            self.tts_task.cancel()
        if self.llm_task and not self.llm_task.done():
            self.llm_task.cancel()
        await self.tts_queue.put(None)
        # Broadcast final status
        await self._broadcast_update(status="completed")