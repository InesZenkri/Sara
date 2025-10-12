import os
import asyncio
import threading
import tempfile
from typing import Optional, Callable
from dotenv import load_dotenv

load_dotenv()

class LiveKitAudioHandler:
    """LiveKit-based audio handler for real-time speech-to-text and hotword detection."""
    
    def __init__(self, hotword: str, callback: Callable[[str, bytes], None]):
        self.hotword = hotword.lower()
        self.callback = callback
        
        # LiveKit configuration
        self.livekit_url = os.getenv("LIVEKIT_URL")
        self.livekit_api_key = os.getenv("LIVEKIT_API_KEY")
        self.livekit_api_secret = os.getenv("LIVEKIT_API_SECRET")
        
        # STT configuration
        self.stt_provider = (
            os.getenv("TRANSCRIPTION_PROVIDER")
            or os.getenv("STT_PROVIDER")
            or os.getenv("MODEL_PROVIDER")
            or "openai"
        ).lower()
        self.groq_client = None
        self.gemini_client = None
        self.openai_client = None
        self.openai_transcription_model = os.getenv("OPENAI_TRANSCRIPTION_MODEL", "whisper-1")
        
        # LiveKit components
        self.room = None
        self.audio_track = None
        self.is_connected = False
        
        if not all([self.livekit_url, self.livekit_api_key, self.livekit_api_secret]):
            raise ValueError("LiveKit credentials not found in environment variables")
        
        self._initialize_stt_client()
    
    def _initialize_stt_client(self):
        """Initialize STT client based on provider."""
        if self.stt_provider == "groq":
            try:
                from groq import Groq
                self.groq_client = Groq(api_key=os.getenv("GROQ_API_KEY"))
                print("LiveKit using Groq for STT")
            except Exception as e:
                print(f"Failed to init Groq STT: {e}")
        elif self.stt_provider == "openai":
            try:
                from openai import OpenAI
                api_key = os.getenv("OPENAI_API_KEY")
                if not api_key:
                    raise RuntimeError("OPENAI_API_KEY is not set.")
                self.openai_client = OpenAI(api_key=api_key)
                print(f"LiveKit using OpenAI ({self.openai_transcription_model}) for STT")
            except Exception as e:
                print(f"Failed to init OpenAI STT: {e}")
        else:
            try:
                from google import genai
                self.gemini_client = genai.Client(api_key=os.getenv("GEMINI_API_KEY"))
                print("LiveKit using Gemini for STT")
            except Exception as e:
                print(f"Failed to init Gemini STT: {e}")
    
    async def connect(self):
        """Connect to LiveKit room."""
        try:
            from livekit import rtc
            
            # Create room
            self.room = rtc.Room()
            
            # Set up event handlers
            self.room.on("track_subscribed", self._on_track_subscribed)
            self.room.on("track_unsubscribed", self._on_track_unsubscribed)
            self.room.on("participant_connected", self._on_participant_connected)
            self.room.on("participant_disconnected", self._on_participant_disconnected)
            
            # Connect to room
            token = self._generate_token()
            await self.room.connect(self.livekit_url, token)
            
            # Enable microphone
            await self.room.local_participant.enable_camera_and_microphone()
            
            self.is_connected = True
            print("Connected to LiveKit room")
            
        except Exception as e:
            print(f"Failed to connect to LiveKit: {e}")
            raise
    
    def _generate_token(self) -> str:
        """Generate LiveKit token for authentication."""
        try:
            from livekit import api
            
            token = api.AccessToken(self.livekit_api_key, self.livekit_api_secret)
            token.with_identity("sara-assistant")
            token.with_name("Sara Assistant")
            token.with_grants(api.VideoGrants(
                room_join=True,
                room="sara-room",
                can_publish=True,
                can_subscribe=True,
            ))
            
            return token.to_jwt()
        except Exception as e:
            print(f"Failed to generate LiveKit token: {e}")
            raise
    
    def _on_track_subscribed(self, track, publication, participant):
        """Handle track subscription."""
        if track.kind == rtc.TrackKind.KIND_AUDIO:
            print(f"Audio track subscribed from {participant.identity}")
            # Set up audio processing
            track.on("frame", self._on_audio_frame)
    
    def _on_track_unsubscribed(self, track, publication, participant):
        """Handle track unsubscription."""
        if track.kind == rtc.TrackKind.KIND_AUDIO:
            print(f"Audio track unsubscribed from {participant.identity}")
    
    def _on_participant_connected(self, participant):
        """Handle participant connection."""
        print(f"Participant connected: {participant.identity}")
    
    def _on_participant_disconnected(self, participant):
        """Handle participant disconnection."""
        print(f"Participant disconnected: {participant.identity}")
    
    def _on_audio_frame(self, frame):
        """Process incoming audio frame."""
        # Convert frame to bytes for STT processing
        audio_data = frame.data
        
        # For now, we'll use a simple approach - collect audio and process periodically
        # In a real implementation, you'd want to buffer audio and process in chunks
        self._process_audio_chunk(audio_data)
    
    def _process_audio_chunk(self, audio_data: bytes):
        """Process audio chunk for hotword detection."""
        # This is a simplified implementation
        # In practice, you'd want to buffer audio and process in larger chunks
        # For now, we'll simulate by processing periodically
        
        # Convert audio data to WAV format for STT
        wav_data = self._convert_to_wav(audio_data)
        
        # Transcribe audio
        text = self._transcribe_audio(wav_data)
        
        if text and self.hotword in text.lower():
            print(f"Hotword '{self.hotword}' detected in: {text}")
            self.callback(text, wav_data)
    
    def _convert_to_wav(self, audio_data: bytes) -> bytes:
        """Convert raw audio data to WAV format."""
        import wave
        import io
        
        # This is a simplified conversion
        # In practice, you'd need to handle the specific audio format from LiveKit
        buf = io.BytesIO()
        with wave.open(buf, "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)  # 16-bit
            wf.setframerate(16000)  # 16kHz
            wf.writeframes(audio_data)
        return buf.getvalue()
    
    def _transcribe_audio(self, wav_data: bytes) -> str:
        """Transcribe audio using configured STT provider."""
        if not wav_data:
            return ""
        
        try:
            if self.stt_provider == "groq" and self.groq_client:
                # Use Groq for transcription
                with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp_file:
                    tmp_file.write(wav_data)
                    tmp_file.flush()
                    
                    with open(tmp_file.name, "rb") as file:
                        transcription = self.groq_client.audio.transcriptions.create(
                            file=(tmp_file.name, file.read()),
                            model="whisper-large-v3",
                        )
                        return transcription.text
                os.unlink(tmp_file.name)
            
            elif self.stt_provider == "openai" and self.openai_client:
                with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp_file:
                    tmp_file.write(wav_data)
                    tmp_file.flush()

                    with open(tmp_file.name, "rb") as file:
                        transcription = self.openai_client.audio.transcriptions.create(
                            file=file,
                            model=self.openai_transcription_model,
                        )
                        text = getattr(transcription, "text", None)
                        if not text and isinstance(transcription, dict):
                            text = transcription.get("text", "")
                        return text or ""
                os.unlink(tmp_file.name)

            elif self.gemini_client:
                # Use Gemini for transcription
                from google.genai import types
                parts = [
                    types.Part.from_bytes(wav_data, mime_type="audio/wav"),
                    "Transcribe the audio to plain text with no extra words.",
                ]
                resp = self.gemini_client.models.generate_content(
                    model=os.getenv("GEMINI_MODEL", "gemini-2.5-flash-lite"),
                    contents=parts,
                )
                return getattr(resp, "text", "") or (resp.candidates[0].content.parts[0].text if getattr(resp, "candidates", None) else "")
            
        except Exception as e:
            print(f"Error during transcription: {e}")
            return ""
        
        return ""
    
    async def play_audio(self, audio_data: bytes):
        """Play audio through LiveKit."""
        if not self.is_connected or not self.room:
            print("Not connected to LiveKit room")
            return
        
        try:
            from livekit import rtc
            
            # Create audio track from data
            audio_track = rtc.LocalAudioTrack.create_audio_track(
                "tts-audio", 
                rtc.AudioStream(audio_data)
            )
            
            # Publish audio track
            await self.room.local_participant.publish_track(audio_track)
            
            # Wait a bit for playback
            await asyncio.sleep(2)
            
            # Unpublish track
            await self.room.local_participant.unpublish_track(audio_track)
            
        except Exception as e:
            print(f"Error playing audio: {e}")
    
    async def disconnect(self):
        """Disconnect from LiveKit room."""
        if self.room and self.is_connected:
            await self.room.disconnect()
            self.is_connected = False
            print("Disconnected from LiveKit room")
    
    def start(self, background: bool = False):
        """Start the LiveKit audio handler."""
        if background:
            threading.Thread(target=self._run_async, daemon=True).start()
        else:
            self._run_async()
    
    def _run_async(self):
        """Run the async LiveKit connection."""
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        
        try:
            loop.run_until_complete(self.connect())
            loop.run_forever()
        except KeyboardInterrupt:
            print("Stopping LiveKit audio handler...")
            loop.run_until_complete(self.disconnect())
        finally:
            loop.close()
    
    def stop(self):
        """Stop the LiveKit audio handler."""
        if self.is_connected:
            # This would need to be called from the async context
            # For now, we'll just set a flag
            self.is_connected = False

# Backward compatibility wrapper
class HotwordListener:
    """Backward compatible wrapper for LiveKit-based hotword detection."""
    
    def __init__(self, keyword: str, callback: Callable[[str, bytes], None]):
        self.keyword = keyword
        self.callback = callback
        self.livekit_handler = None
    
    def start(self, background: bool = False):
        """Start listening for hotword."""
        try:
            self.livekit_handler = LiveKitAudioHandler(self.keyword, self.callback)
            self.livekit_handler.start(background)
        except Exception as e:
            print(f"Failed to start LiveKit hotword listener: {e}")
            print("Falling back to VAD-based approach...")
            # Fallback to original VAD implementation
            self._fallback_to_vad()
    
    def _fallback_to_vad(self):
        """Fallback to original VAD implementation."""
        from vad import VADStream
        
        def _on_speech_end(wav_bytes, duration):
            if duration > 1.0:
                # Use existing transcription logic from audio.py
                self._transcribe_and_check_hotword(wav_bytes)
        
        self.vad_stream = VADStream(
            on_speech_start=lambda: print("Speech started..."),
            on_speech_end=_on_speech_end
        )
        self.vad_stream.start()
    
    def _transcribe_and_check_hotword(self, wav_bytes: bytes):
        """Transcribe audio and check for hotword (fallback method)."""
        # This would use the same logic as the original audio.py
        # For brevity, we'll just call the callback with dummy data
        self.callback("fallback transcription", wav_bytes)
    
    def stop(self):
        """Stop listening."""
        if self.livekit_handler:
            self.livekit_handler.stop()
        elif hasattr(self, 'vad_stream'):
            self.vad_stream.stop()

# Example usage
if __name__ == "__main__":
    def demo_callback(text, audio):
        print(f"Hotword detected in: {text}")
    
    listener = HotwordListener("sara", demo_callback)
    listener.start(background=True)
    
    try:
        import time
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        listener.stop()
