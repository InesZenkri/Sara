# Sara - What Siri should have been

Big Berlin Hack 2025, making your clipboard a supertool

Sara is an AI-powered macOS assistant that enhances your clipboard functionality. It can read and modify text, create memes, remove image backgrounds, and remember your preferences using persistent memory. Powered by LiveKit for real-time audio processing and Cartesia for voice synthesis, Sara provides a seamless voice-enabled experience that Siri could have been to help you with everyday tasks.

## What Sara can do

Core voice/clipboard actions
- Copy text to the clipboard on command. Great for: summaries, re-writes, code generation, data extraction, and format conversions. There is no character limit for copied content.
- Short reply/confirmations. Give you a quick spoken/notification response when no clipboard change is needed.
- Monitor your clipboard (text and images) and operate on the most recent content automatically.

Image tools (operate on the image currently in your clipboard)
- Make meme: Add top and/or bottom text to the clipboard image in bold meme style, then copy the result back to your clipboard.
- Remove background: Remove the background from the clipboard image (transparent PNG when possible), then copy the result back to your clipboard.

Social posting
- Post to Facebook Page: Post one or more images from your clipboard to a Facebook Page you manage. Optionally include a caption. Sara can analyze the image to suggest an engaging caption (useful when selling items). Requires FACEBOOK_PAGE_ID and FACEBOOK_PAGE_ACCESS_TOKEN.

Chess helper
- Analyze chess position on screen: Capture the current screen (requires screen recording permission on macOS) and analyze the visible chessboard to recommend the strongest move. The result (best move, evaluation and short explanation) is copied to your clipboard.

Memory (optional, disabled by default)
- Save to memory: Remember preferences, important info, reminders, and notes for later recall. Trigger phrases include “remember that…”, “save this…”, “note that…”. Enable with ENABLE_MEMORY=true.
- Search memory: Retrieve past information you asked Sara to remember (e.g., “what did we talk about yesterday?”, “find that email address I saved”).

Voice and hearing
- Hotword “sara”: Hands‑free activation. Works with either LiveKit real‑time audio or a local VAD fallback.
- Speech-to-text: Transcribe your speech using OpenAI Whisper by default, or Groq/Gemini if configured.
- Text‑to‑speech: Natural voice replies using Cartesia TTS. Requires CARTESIA_API_KEY.

Examples you can say
- “Sara, summarize this and copy it to my clipboard.”
- “Sara, turn this screenshot into a meme: top ‘when the build finally passes’, bottom ‘ship it’.”
- “Sara, remove the background from this image.”
- “Sara, post these to my Facebook page with the caption ‘Weekend vibes’.”
- “Sara, what’s the best move here?”
- “Sara, remember that I prefer 24‑hour time.”
- “Sara, what did we discuss about my trip last week?”

Notes
- Memory features are available only when ENABLE_MEMORY=true. When disabled, Sara still handles clipboard editing, memes, background removal, short replies, and Facebook posting.
- Facebook posting requires proper Page permissions and environment variables (see Facebook Posting below).
- LiveKit can be disabled with USE_LIVEKIT=false to use VAD.

## Setup

### Environment Variables

The following environment variables are required:

#### Core AI Configuration

- `OPENAI_API_KEY`: Your OpenAI API key (required if using OpenAI provider)
- `MODEL_PROVIDER`: Choose the AI provider, defaults to: `openai`
- `OPENAI_MODEL`: Optional override for the OpenAI chat model (defaults to `gpt-4o-mini`)
- `ENABLE_MEMORY`: Enable memory functionality (`true` or `false`, defaults to `false`)

#### LiveKit Configuration (for real-time audio)
- `LIVEKIT_URL`: LiveKit server URL (e.g., `wss://your-livekit-server.com`)
- `LIVEKIT_API_KEY`: LiveKit API key
- `LIVEKIT_API_SECRET`: LiveKit API secret
- `USE_LIVEKIT`: Enable LiveKit audio processing (`true` or `false`, defaults to `true`)

#### Cartesia TTS Configuration (for voice responses)
- `CARTESIA_API_KEY`: Your Cartesia API key (required for voice responses)
- `CARTESIA_VOICE_ID`: Voice ID for Sara (optional, defaults to "sara")
- `CARTESIA_MODEL_ID`: TTS model to use (optional, defaults to `sonic-2`)
- `CARTESIA_LANGUAGE`: ISO language code for synthesis (optional, defaults to `en`)

#### Audio Processing Configuration
- `OPENAI_TRANSCRIPTION_MODEL`: Speech-to-text model when using OpenAI (defaults to `whisper-1`)
- `STT_PROVIDER`: (Deprecated) legacy flag for speech-to-text provider

### Memory Configuration

Memory functionality is **disabled by default** for faster, simpler operation. You can enable it if you need persistent memory across sessions:

```bash
# Disable memory (default - faster and simpler)
export ENABLE_MEMORY=false
# or simply omit the variable

# Enable memory (slower startup, but remembers interactions)
export ENABLE_MEMORY=true
```

When memory is disabled:

- Faster startup time
- No ChromaDB dependencies
- Simpler prompts focused on clipboard operations
- Actions limited to: copy text, make memes, short replies

When memory is enabled:

- Full memory search and save capabilities
- Remembers user preferences and past interactions
- Additional actions: search memory, save to memory

### Model Provider Configuration

You can switch between different AI providers by setting the `MODEL_PROVIDER` environment variable:

```bash

# Use OpenAI
export MODEL_PROVIDER=openai
export OPENAI_API_KEY=your_openai_api_key
# Optional overrides
export OPENAI_MODEL=gpt-4o-mini
export OPENAI_TRANSCRIPTION_MODEL=whisper-1
```

### Installation

#### Facebook Posting

You can ask Sara to post images from your clipboard to your Facebook Page.

Requirements:
- A Facebook Page you manage
- A Page Access Token with permissions: `pages_manage_posts`, `pages_read_engagement`
- Environment variables set:
  - `FACEBOOK_PAGE_ID`
  - `FACEBOOK_PAGE_ACCESS_TOKEN`

Usage examples:
- "Sara, post these to my Facebook page with the caption 'Weekend vibes'"
- "Sara, post the clipboard images to Facebook"

Sara will post each image as its own photo post on your Page. The caption (if provided) will be applied to the first image.

---

### Installation

1. Install dependencies:

```bash
pip install -r app/requirements.txt
```

2. Set up your environment variables in a `.env` file or export them directly
3. Run the application:

```bash
cd app
python main.py
```

### Supported Models

- **OpenAI**: defaults to `gpt-4o-mini` for chat and `whisper-1` for speech-to-text (override with env vars above)

### Google Search Integration

When using Gemini as the provider, the application automatically enhances responses with real-time web search when queries contain keywords like:

- "current", "recent", "latest", "news", "today", "now", "update"

This provides access to the most current information beyond the model's training cutoff date.


### LiveKit Integration

Sara uses LiveKit for real-time audio processing, providing:

- **Real-time audio streaming**: Better performance than traditional VAD
- **Low-latency processing**: Faster response times
- **Scalable architecture**: Can handle multiple concurrent sessions
- **Fallback support**: Automatically falls back to VAD if LiveKit is unavailable

To disable LiveKit and use the traditional VAD approach:
```bash
export USE_LIVEKIT=false
```

### Cartesia Voice Synthesis

Sara responds with natural voice using Cartesia TTS:

- **High-quality voice synthesis**: Natural-sounding responses
- **Low-latency TTS**: Fast voice generation
- **Customizable voice**: Choose different voice models
- **Automatic fallback**: Gracefully handles TTS failures

Voice responses are played automatically after each command, providing both visual notifications and audio feedback.

### Hotword Detection

Sara listens for the hotword "sara" to activate. The system supports:

- **LiveKit-based detection**: Real-time audio processing with LiveKit
- **VAD fallback**: Traditional voice activity detection if LiveKit unavailable
- **Multiple STT providers**: Groq or Gemini for speech-to-text
- **Configurable sensitivity**: Adjustable detection parameters
