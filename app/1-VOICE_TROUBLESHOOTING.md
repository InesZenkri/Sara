# Voice commands: if nothing happens

`python run_voice.py` prints what it does. You should see, in this order:

```
Loading Whisper model 'base' (first run downloads it)...
Whisper model ready.
Microphone: <your input device>
Say 'sara, open notes'...
[speech start]
[speech end] 2.1s
Heard: sara open notes
  -> mac
```

- No `[speech start]`: wrong microphone or macOS blocked it. Check the `Microphone:` line and System Settings > Privacy > Microphone for Terminal.
- `[speech start]` never followed by `[speech end]`: steady noise (fan) counts as speech. `SARA_VAD_MODE=3` is the default (strictest). Move the mic or lower the fan load. Long audio is cut after a few seconds anyway.
- `Heard:` shows text, then `-> ignored`: say the wake word first, "Sara, open Notes".
- Heard text is wrong: try `SARA_WHISPER_MODEL=small`, keep `SARA_WHISPER_LANGUAGE=en` (default; `auto` detects the language).
- CPU very high: `SARA_WHISPER_THREADS=2`. Partial transcripts are off by default for local speech-to-text; turn them on with `SARA_PARTIAL_INTERVAL=3` only if your Mac copes.
- Stop old copies first: `pkill -f run_voice.py`.
- Dry run (nothing touches your Mac): `SARA_MAC_DRY_RUN=true`.
