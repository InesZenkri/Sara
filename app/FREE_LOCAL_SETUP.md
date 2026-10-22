# Running Sara with no paid service

Nothing here needs a credit card. Keys go in `app/.env` on your Mac only (it is git-ignored, never commit it or paste keys anywhere).

## 1. Language model (pick one)

**Gemini (free tier, Google AI Studio key)**

```
SARA_LLM_API_KEY=<your Gemini key>
SARA_LLM_BASE_URL=https://generativelanguage.googleapis.com/v1beta/openai/
SARA_LLM_MODEL=gemini-2.5-flash
SARA_INSTRUCTOR_MODE=json
```

**Groq (free tier)**

```
SARA_LLM_API_KEY=<your Groq key>
SARA_LLM_BASE_URL=https://api.groq.com/openai/v1
SARA_LLM_MODEL=openai/gpt-oss-20b
SARA_INSTRUCTOR_MODE=json
```

Notes, checked against the providers' docs (not run with real keys by the author):

- Model names change. If one is rejected, pick a current one from the provider's model list.
- If structured output fails, try `SARA_INSTRUCTOR_MODE=json_schema` or `tools`. The default (unset) is unchanged from before.
- Free-tier limits are per model and change; look at your AI Studio / Groq console. Do not turn on billing.
- Gemini free tier: Google says content sent on the free tier may be used to improve its products. Do not send anything private through it.
- Image features (chess, Facebook captions) need a model that accepts images. Gemini Flash does. Many free Groq models do not.

## 2. Speech-to-text, local and free

```
pip install faster-whisper
TRANSCRIPTION_PROVIDER=local
SARA_WHISPER_MODEL=base
```

The first run downloads the model (about 150 MB for `base`). It runs on CPU, no key, no rate limit. `small` is more accurate but slower.
For cloud STT (`openai` or `groq`) set `SARA_PARTIAL_INTERVAL=3` or higher so live partial transcripts do not hit rate limits. `0` turns partials off.

## 3. Voice replies

Default is the built-in macOS `say` voice. Nothing to install. Optional:

```
SARA_TTS=auto          # auto | say | cartesia | none
SARA_SAY_VOICE=Samantha
SARA_SAY_RATE=190
```

Cartesia is used only if `SARA_TTS=cartesia`, or `auto` with `CARTESIA_API_KEY` set.

Other free local voices, not wired in: Kokoro-82M (Apache-2.0, better quality, needs extra install). Piper: the original `rhasspy/piper` is MIT but archived; the maintained fork `OHF-Voice/piper1-gpl` is GPL-3.0.

## 4. Not needed

LiveKit, Cartesia and chromadb are now in `requirements-optional.txt`. The default microphone path is the local VAD one.
