import os
import tempfile
from typing import Optional
from dotenv import load_dotenv

load_dotenv()

class CartesiaTTS:
    """Text-to-Speech using Cartesia API."""
    
    def __init__(self):
        self.api_key = os.getenv("CARTESIA_API_KEY")
        self.voice_id = os.getenv("CARTESIA_VOICE_ID", "sara")  # Default to sara voice
        self.model_id = os.getenv("CARTESIA_MODEL_ID", "sonic-2")
        self.language = os.getenv("CARTESIA_LANGUAGE", "en").strip() or None
        self.client = None
        
        if not self.api_key:
            raise ValueError("CARTESIA_API_KEY environment variable is required")
        
        self._initialize_client()
    
    def _initialize_client(self):
        """Initialize Cartesia client."""
        try:
            import cartesia
            self.client = cartesia.Cartesia(api_key=self.api_key)
            print(f"Cartesia TTS initialized with voice: {self.voice_id}")
        except ImportError:
            raise ImportError("Cartesia package not installed. Run: pip install cartesia")
        except Exception as e:
            raise RuntimeError(f"Failed to initialize Cartesia client: {e}")
    
    def synthesize(self, text: str, voice_id: Optional[str] = None) -> bytes:
        """
        Synthesize speech from text.
        
        Args:
            text: Text to synthesize
            voice_id: Optional voice ID override
            
        Returns:
            Audio data as bytes (WAV format)
        """
        if not self.client:
            raise RuntimeError("Cartesia client not initialized")
        
        voice = voice_id or self.voice_id
        
        try:
            voice_spec = {"mode": "id", "id": voice}
            output_format = {
                "container": "wav",
                "sample_rate": 44100,
                "encoding": "pcm_s16le",
            }

            # Cartesia returns a generator of byte chunks; join to produce a wav file in memory.
            request_kwargs = dict(
                model_id=self.model_id,
                transcript=text,
                voice=voice_spec,
                output_format=output_format,
            )
            if self.language:
                request_kwargs["language"] = self.language

            audio_chunks = self.client.tts.bytes(**request_kwargs)
            audio_bytes = b"".join(audio_chunks)
            return audio_bytes

        except Exception as e:
            print(f"Error synthesizing speech: {e}")
            raise
    
    def synthesize_to_file(self, text: str, output_path: str, voice_id: Optional[str] = None) -> str:
        """
        Synthesize speech and save to file.
        
        Args:
            text: Text to synthesize
            output_path: Path to save audio file
            voice_id: Optional voice ID override
            
        Returns:
            Path to saved file
        """
        audio_data = self.synthesize(text, voice_id)
        
        with open(output_path, 'wb') as f:
            f.write(audio_data)
        
        return output_path
    
    def synthesize_to_temp_file(self, text: str, voice_id: Optional[str] = None) -> str:
        """
        Synthesize speech and save to temporary file.
        
        Args:
            text: Text to synthesize
            voice_id: Optional voice ID override
            
        Returns:
            Path to temporary file
        """
        audio_data = self.synthesize(text, voice_id)
        
        with tempfile.NamedTemporaryFile(suffix='.wav', delete=False) as tmp_file:
            tmp_file.write(audio_data)
            return tmp_file.name

# Example usage
if __name__ == "__main__":
    try:
        tts = CartesiaTTS()
        audio_data = tts.synthesize("Hello, I'm Sara, your voice assistant!")
        
        # Save to file for testing
        with open("test_output.wav", "wb") as f:
            f.write(audio_data)
        print("Audio saved to test_output.wav")
        
    except Exception as e:
        print(f"Error: {e}")
