import os
import tempfile
import threading
from dotenv import load_dotenv

load_dotenv()

# Check if LiveKit is available, otherwise fallback to VAD
USE_LIVEKIT = os.getenv("USE_LIVEKIT", "false").lower() == "true"

if USE_LIVEKIT:
    try:
        from livekit_audio import HotwordListener
        print("Using LiveKit-based HotwordListener")
    except ImportError:
        print("LiveKit not available, falling back to VAD")
        USE_LIVEKIT = False

if not USE_LIVEKIT:
    from vad import VADStream

    class HotwordListener:
        """Listens for a specified hotword using VAD and invokes a callback using the configured transcription provider."""
        def __init__(self, keyword, callback):
            self.keyword = keyword.lower()
            self.callback = callback
            # Determine provider
            self.provider = (
                os.getenv("TRANSCRIPTION_PROVIDER")
                or os.getenv("STT_PROVIDER")
                or os.getenv("MODEL_PROVIDER")
                or "openai"
            ).lower()
            self.groq_client = None
            self.gemini_client = None
            self.gemini_model = os.getenv("GEMINI_MODEL", "gemini-2.5-flash-lite")
            self.openai_client = None
            self.openai_transcription_model = os.getenv("OPENAI_TRANSCRIPTION_MODEL", "whisper-1")

            # Initialize transcription client lazily based on provider
            if self.provider == "groq":
                try:
                    from groq import Groq  # Lazy import so non-Groq users don't need it
                    self.groq_client = Groq(api_key=os.getenv("GROQ_API_KEY"))
                    print("HotwordListener using Groq for transcription")
                except Exception as e:
                    print(f"Failed to init Groq: {e}")
            elif self.provider == "openai":
                try:
                    from openai import OpenAI  # Lazy import for OpenAI transcription
                    api_key = os.getenv("OPENAI_API_KEY")
                    if not api_key:
                        raise RuntimeError("OPENAI_API_KEY is not set.")
                    self.openai_client = OpenAI(api_key=api_key)
                    print(f"HotwordListener using OpenAI ({self.openai_transcription_model}) for transcription")
                except Exception as e:
                    print(f"Failed to init OpenAI: {e}")
            else:
                try:
                    from google import genai
                    self.gemini_client = genai.Client(api_key=os.getenv("GEMINI_API_KEY"))
                    print(f"HotwordListener using Gemini ({self.gemini_model}) for transcription")
                except Exception as e:
                    print(f"Failed to init Gemini: {e}")
            # Initialize VAD stream
            self.vad_stream = None

        def _on_speech_end(self, wav_bytes, duration):
            """Called when VAD detects end of speech."""
            print(f"Speech detected, duration: {duration:.2f}s")
            
            # Only transcribe audio longer than 1 second
            if duration > 1.0:
                try:
                    text = ""
                    if self.provider == "groq" and self.groq_client is not None:
                        # Create a temporary file for Groq API
                        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp_file:
                            tmp_file.write(wav_bytes)
                            tmp_file.flush()
                            
                            with open(tmp_file.name, "rb") as file:
                                transcription = self.groq_client.audio.transcriptions.create(
                                    file=(tmp_file.name, file.read()),
                                    model="whisper-large-v3",
                                )
                                text = transcription.text
                        os.unlink(tmp_file.name)
                        print(f"Groq heard: {text}")
                    elif self.provider == "openai" and self.openai_client is not None:
                        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp_file:
                            tmp_file.write(wav_bytes)
                            tmp_file.flush()

                            with open(tmp_file.name, "rb") as file:
                                transcription = self.openai_client.audio.transcriptions.create(
                                    file=file,
                                    model=self.openai_transcription_model,
                                )
                                # OpenAI responses may be object-like or dict-like depending on SDK version
                                text = getattr(transcription, "text", None)
                                if not text and isinstance(transcription, dict):
                                    text = transcription.get("text", "")
                                text = text or ""
                        os.unlink(tmp_file.name)
                        print(f"OpenAI heard: {text}")
                    elif self.gemini_client is not None:
                        from google.genai import types
                        parts = [
                            types.Part.from_bytes(data=wav_bytes, mime_type="audio/wav"),
                            "Transcribe the audio to plain text with no extra words.",
                        ]
                        resp = self.gemini_client.models.generate_content(
                            model=self.gemini_model,
                            contents=parts,
                        )
                        # Best-effort text extraction
                        text = getattr(resp, "text", "") or (resp.candidates[0].content.parts[0].text if getattr(resp, "candidates", None) else "")
                        print(f"Gemini heard: {text}")
                    else:
                        print("No transcription client available; skipping transcription")
                    if self.keyword in text.lower():
                        self.callback(text, wav_bytes)
                except Exception as e:
                    print(f"Error during transcription/detection: {e}")
            else:
                print(f"Audio too short ({duration:.2f}s), skipping transcription")

        def start(self, background=False):
            """Begin listening loop. Use background=True to run in a daemon thread."""
            print("Listening for hotword...")
            
            def _on_speech_start():
                print("Speech started...")
            
            self.vad_stream = VADStream(
                on_speech_start=_on_speech_start,
                on_speech_end=self._on_speech_end
            )
            
            if background:
                threading.Thread(target=self._run, daemon=True).start()
            else:
                self._run()

        def _run(self):
            """Main listening loop."""
            self.vad_stream.start()
            try:
                # Keep the main thread alive while VAD runs
                import time
                while True:
                    time.sleep(0.1)
            except KeyboardInterrupt:
                self.stop()

        def stop(self):
            """Stop the listening loop."""
            if self.vad_stream:
                self.vad_stream.stop()

# Example usage:
if __name__ == "__main__":
    def demo(text, audio):
        print(f"Detected hotword in: {text}")

    listener = HotwordListener("sara", demo)
    listener.start()
