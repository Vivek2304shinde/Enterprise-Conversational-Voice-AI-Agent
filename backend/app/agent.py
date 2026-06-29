import asyncio
import base64
import json
import logging
from typing import Optional
import aiohttp
import websockets

from deepgram import DeepgramClient, LiveTranscriptionEvents, LiveOptions
from openai import AsyncOpenAI

from .config import settings

logger = logging.getLogger(__name__)

# Cartesia TTS endpoint
CARTESIA_TTS_URL = "https://api.cartesia.ai/tts/stream"
CARTESIA_VOICE_ID = "79a125e8-cd45-4c13-8a67-188112f4dd22"  # professional male


class VoiceAgent:
    def __init__(self, call_sid: str, stream_sid: str, websocket):
        self.call_sid = call_sid
        self.stream_sid = stream_sid
        self.websocket = websocket

        # Clients
        self.deepgram = DeepgramClient(settings.DEEPGRAM_API_KEY)
        self.openai = AsyncOpenAI(api_key=settings.OPENAI_API_KEY)

        # State
        self.is_speaking = False
        self.transcript_buffer = ""
        self.dg_connection = None
        self.llm_task: Optional[asyncio.Task] = None
        self.tts_queue = asyncio.Queue()
        self.tts_task: Optional[asyncio.Task] = None
        self.running = True

        # Start TTS playback loop
        self.tts_task = asyncio.create_task(self._tts_playback())

        # Start Deepgram live connection
        self._setup_deepgram()

    def _setup_deepgram(self):
        """Initialize Deepgram live transcription with endpointing."""
        try:
            self.dg_connection = self.deepgram.listen.websocket.v("1")
            self.dg_connection.on(LiveTranscriptionEvents.Transcript, self._on_transcript)
            self.dg_connection.on(LiveTranscriptionEvents.UtteranceEnd, self._on_utterance_end)

            options = LiveOptions(
                model="nova-2",
                language="en-US",          # we can make this dynamic later
                smart_format=True,
                endpointing=300,           # 300ms of silence triggers utterance end
                vad_events=True,
                interim_results=False,
            )
            self.dg_connection.start(options)
            logger.info(f"Deepgram started for call {self.call_sid}")
        except Exception as e:
            logger.error(f"Failed to start Deepgram: {e}")

    async def process_audio(self, audio_bytes: bytes):
        """Send audio chunk to Deepgram."""
        if self.dg_connection and self.running:
            try:
                self.dg_connection.send(audio_bytes)
            except Exception as e:
                logger.error(f"Deepgram send error: {e}")

    def _on_transcript(self, result, **kwargs):
        """Called when Deepgram returns a final transcript."""
        if result.is_final:
            transcript = result.channel.alternatives[0].transcript
            if transcript.strip():
                logger.info(f"User said: {transcript}")
                self.transcript_buffer += " " + transcript
                # Trigger LLM response if we are not already speaking and no LLM task running
                if not self.is_speaking and (self.llm_task is None or self.llm_task.done()):
                    # Clear buffer and start LLM
                    user_text = self.transcript_buffer.strip()
                    self.transcript_buffer = ""
                    self.llm_task = asyncio.create_task(self._get_llm_response(user_text))

    def _on_utterance_end(self, *args, **kwargs):
        """Triggered when the user stops speaking (endpoint detected)."""
        # If we have buffered transcript and LLM not yet triggered, do it now
        if not self.is_speaking and self.transcript_buffer.strip():
            if self.llm_task is None or self.llm_task.done():
                user_text = self.transcript_buffer.strip()
                self.transcript_buffer = ""
                self.llm_task = asyncio.create_task(self._get_llm_response(user_text))

    async def _get_llm_response(self, user_input: str):
        """Call OpenAI and generate a response."""
        self.is_speaking = True
        try:
            # System prompt – can be customised per campaign later
            system_prompt = (
                "You are Alex, a professional collections agent calling on behalf of ABC Finance. "
                "Your goal is to remind customers about overdue payments, be polite and empathetic. "
                "If the customer says they will pay on a specific date, confirm that date and say you will schedule a callback. "
                "If they ask for a human, say you will transfer them to an agent. "
                "Keep responses short and conversational. "
                "Do not threaten or argue."
            )
            response = await self.openai.chat.completions.create(
                model="gpt-4o-mini",   # fast and cheap
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_input}
                ],
                max_tokens=150,
                temperature=0.7,
            )
            reply = response.choices[0].message.content.strip()
            logger.info(f"AI reply: {reply}")

            # Send to TTS
            await self._synthesize_and_send(reply)

        except Exception as e:
            logger.error(f"LLM error: {e}")
            await self._synthesize_and_send("I'm sorry, I'm having trouble. Please hold.")
        finally:
            self.is_speaking = False

    async def _synthesize_and_send(self, text: str):
        """Use Cartesia TTS to synthesize speech and queue for playback."""
        if not text:
            return
        try:
            # Cartesia streaming via HTTP POST
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
                    "encoding": "pcm_mulaw",   # μ-law for Twilio
                    "sample_rate": 8000
                }
            }

            async with aiohttp.ClientSession() as session:
                async with session.post(CARTESIA_TTS_URL, json=payload, headers=headers) as resp:
                    if resp.status != 200:
                        error_text = await resp.text()
                        logger.error(f"Cartesia error: {resp.status} - {error_text}")
                        return
                    # Read streaming chunks
                    chunk_count = 0
                    async for chunk in resp.content.iter_chunks():
                        data, _ = chunk
                        if data:
                            # Data is raw μ-law audio bytes; we need to base64 encode for Twilio
                            audio_b64 = base64.b64encode(data).decode('utf-8')
                            await self.tts_queue.put(audio_b64)
                            chunk_count += 1
                    logger.info(f"TTS generated {chunk_count} chunks for: {text[:30]}...")
        except Exception as e:
            logger.error(f"TTS synthesis error: {e}")

    async def _tts_playback(self):
        """Continuously send queued audio chunks to Twilio via WebSocket."""
        while self.running:
            try:
                audio_b64 = await self.tts_queue.get()
                if audio_b64 is None:
                    break
                # Send media event to Twilio
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

    async def close(self):
        """Clean up resources."""
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
        # Put None to stop TTS queue
        await self.tts_queue.put(None)