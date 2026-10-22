"""Voice commands for Sara: partial transcripts -> early Mac actions.

Glue between the pieces that were merged earlier:
  command_rules  wake word, cancel, end of command
  intent         StreamingIntentRunner (fires only safe actions early)
  mac_control    allow-listed Mac actions (typing asks for confirmation)
  overlay        listening / heard / doing / done display

`VoiceController` has no audio or GUI dependency (this is what the tests
drive). `make_listener()` adds the audio layer on top of the existing
vad.VADStream: while you speak, it transcribes the audio so far every
~0.8 s and feeds the text to the controller.

Anything that is not a Mac command is handed to the old clipboard assistant
(`legacy` callback), so existing behaviour stays the same.

Cost note: partial transcripts mean one speech-to-text API call roughly every
0.8 s of speech instead of one per utterance.

Off by default: nothing here runs unless SARA_VOICE_COMMANDS=true and you start
`python run_voice.py`.
"""
from __future__ import annotations

import os
import subprocess
import threading
import time
from typing import Callable, Optional

import command_rules
import mac_control
from intent import IntentGuess, RuleBackend, StreamingIntentRunner
from overlay import OverlayState

PARTIAL_INTERVAL = 0.8  # default seconds between partial transcriptions


def partial_interval_from_env(default: float = PARTIAL_INTERVAL) -> float:
    """SARA_PARTIAL_INTERVAL in seconds. 0 (or negative) turns partial transcripts off,
    so audio is transcribed only once per command. Use 3 or more with rate-limited
    cloud speech-to-text; the local option has no limit."""
    raw = (os.getenv("SARA_PARTIAL_INTERVAL") or "").strip()
    if not raw:
        return default
    try:
        value = float(raw)
    except ValueError:
        return default
    return float("inf") if value <= 0 else value
MIN_PARTIAL_SECONDS = 0.6  # do not transcribe less audio than this


def _describe(g: IntentGuess) -> str:
    if g.action == "OPEN_APP":
        return f"Opening {g.args.get('app', '?')}"
    if g.action == "CREATE_NOTE":
        return "Creating a note"
    if g.action == "TYPE_TEXT":
        return "Typing"
    return "Working"


def dialog_confirm(question: str) -> bool:
    """Ask on macOS with a dialog. Cancel (or any error) means no."""
    script = (
        'on run argv\n'
        'display dialog (item 1 of argv) buttons {"Cancel", "OK"} '
        'default button "Cancel" with title "Sara"\n'
        'end run'
    )
    try:
        subprocess.run(["osascript", "-e", script, question], check=True, capture_output=True, timeout=60)
        return True
    except Exception:
        return False


class VoiceController:
    def __init__(
        self,
        *,
        overlay: Optional[OverlayState] = None,
        legacy: Optional[Callable[[str, Optional[bytes]], None]] = None,
        notify: Optional[Callable[[str], None]] = None,
        confirm: Optional[Callable[[str], bool]] = dialog_confirm,
        execute: Callable[..., mac_control.MacResult] = mac_control.execute,
        hotword: str = "sara",
        backend=None,
        clock: Callable[[], float] = time.monotonic,
    ):
        self.overlay = overlay or OverlayState()
        self.legacy = legacy
        self.notify = notify
        self.confirm = confirm
        self.execute = execute
        self.hotword = hotword
        self.log: list = []  # (kind, detail) records, handy for tests and debugging
        self.runner = StreamingIntentRunner(
            backend or RuleBackend(mac_control.allowed_apps()), self._fire, clock=clock, min_interval=0.0
        )
        self._reset()

    def _reset(self) -> None:
        self.runner.reset()
        self._cancelled = False
        self._fired = 0

    def start_utterance(self) -> None:
        self._reset()

    # -- actions -----------------------------------------------------------
    def _fire(self, g: IntentGuess, early: bool) -> None:
        self._fired += 1
        self.overlay.doing(_describe(g))
        res = self.execute(
            g.action,
            app=g.args.get("app", ""),
            title=g.args.get("title", ""),
            text=g.args.get("text", ""),
            confirm=self.confirm,
        )
        self.log.append(("early" if early else "final", g.action, res.ok, res.message))
        if res.ok:
            self.overlay.done(res.message)
        else:
            self.overlay.error(res.message)
        if self.notify and (not res.ok or res.executed):
            try:
                self.notify(res.message)
            except Exception:
                pass

    # -- transcript events ---------------------------------------------------
    def on_partial(self, text: str) -> None:
        if self._cancelled:
            return
        kind, cmd = command_rules.classify_utterance(text, self.hotword)
        if kind == "ignore":
            return
        if kind == "cancel":
            self._cancelled = True
            self.overlay.done("Cancelled")
            self.log.append(("cancel", "partial"))
            return
        self.overlay.listening(cmd)
        if kind == "command":
            self.runner.feed(cmd)

    def on_final(self, text: str, wav: Optional[bytes] = None) -> str:
        """Returns what happened: ignored | wake | cancelled | mac | legacy | none."""
        kind, cmd = command_rules.classify_utterance(text, self.hotword)
        cancelled = self._cancelled or kind == "cancel"
        try:
            if kind == "ignore":
                if not self._cancelled:
                    self.overlay.hide()
                return "cancelled" if self._cancelled else "ignored"
            if cancelled:
                self.overlay.done("Cancelled")
                return "cancelled"
            if kind == "wake":
                self.overlay.done("Yes?")
                return "wake"
            self.overlay.heard(cmd)
            g = self.runner.finish(cmd)
            if g is not None or self._fired:
                return "mac"
            if self.legacy is not None:
                self.overlay.doing("Thinking")
                self.legacy(text, wav)
                self.overlay.hide()
                return "legacy"
            self.overlay.hide()
            return "none"
        finally:
            self._reset()


# -- speech to text ------------------------------------------------------------
def make_transcriber() -> Optional[Callable[[bytes], str]]:
    """Return wav_bytes -> text.

    TRANSCRIPTION_PROVIDER: openai (default), groq, or local (faster-whisper on this
    machine: free, no key, no rate limit; `pip install faster-whisper`, model downloads
    on first use). Local options: SARA_WHISPER_MODEL (default "base"), SARA_WHISPER_LANGUAGE
    (e.g. "en", default auto-detect)."""
    provider = (
        os.getenv("TRANSCRIPTION_PROVIDER") or os.getenv("STT_PROVIDER") or os.getenv("MODEL_PROVIDER") or "openai"
    ).lower()
    if provider in ("local", "faster-whisper"):
        import io

        from faster_whisper import WhisperModel

        model = WhisperModel(os.getenv("SARA_WHISPER_MODEL", "base"), device="cpu", compute_type="int8")
        language = (os.getenv("SARA_WHISPER_LANGUAGE") or "").strip() or None
        lock = threading.Lock()  # one transcription at a time on the shared model

        def local_t(wav: bytes) -> str:
            with lock:
                segments, _ = model.transcribe(
                    io.BytesIO(wav), language=language, beam_size=1, initial_prompt="Sara, open Notes."
                )
                return " ".join(s.text.strip() for s in segments).strip()

        return local_t
    if provider == "groq":
        from groq import Groq

        client = Groq(api_key=os.getenv("GROQ_API_KEY"))

        def groq_t(wav: bytes) -> str:
            return client.audio.transcriptions.create(file=("audio.wav", wav), model="whisper-large-v3").text or ""

        return groq_t
    if provider == "openai":
        from openai import OpenAI

        client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
        model = os.getenv("OPENAI_TRANSCRIPTION_MODEL", "whisper-1")

        def openai_t(wav: bytes) -> str:
            return getattr(client.audio.transcriptions.create(file=("audio.wav", wav), model=model), "text", "") or ""

        return openai_t
    print(f"Voice commands support openai, groq or local transcription, not '{provider}'.")
    return None


# -- audio layer -----------------------------------------------------------------
def make_listener(
    controller: VoiceController,
    transcribe: Callable[[bytes], str],
    *,
    partial_interval: Optional[float] = None,
    threaded: bool = True,
    clock: Callable[[], float] = time.monotonic,
):
    """Build a VADStream subclass instance that feeds partial and final transcripts.

    `vad` (sounddevice, webrtcvad) is imported here, not at module import, so the
    controller can be tested anywhere. The silence length that ends a command is
    taken from command_rules.SILENCE_SECONDS, the maximum from MAX_COMMAND_SECONDS.
    """
    import vad as vadmod

    if partial_interval is None:
        partial_interval = partial_interval_from_env()

    vadmod.END_CONSECUTIVE = max(1, int(command_rules.SILENCE_SECONDS * 1000 / vadmod.FRAME_DURATION_MS))
    bytes_per_second = vadmod.SAMPLE_RATE * vadmod.BYTES_PER_SAMPLE

    class VoiceCommandListener(vadmod.VADStream):
        def __init__(self):
            super().__init__(on_speech_start=self._started, on_speech_end=self._ended)
            self._utt = 0
            self._last_partial = 0.0
            self._inflight = False

        def _started(self):
            self._utt += 1
            self._last_partial = clock()
            controller.start_utterance()
            controller.overlay.listening("")

        def _partial(self, utt: int, pcm: bytes):
            try:
                text = transcribe(self._pcm_to_wav_bytes(pcm))
                if utt == self._utt:  # drop results from an utterance that already ended
                    controller.on_partial(text)
            except Exception as exc:
                print(f"Partial transcription failed: {exc}")
            finally:
                self._inflight = False

        def _ended(self, wav_bytes, duration):
            self._utt += 1  # invalidates any partial still in flight
            if duration < 0.4:
                controller.overlay.hide()
                return
            try:
                text = transcribe(wav_bytes)
            except Exception as exc:
                print(f"Final transcription failed: {exc}")
                controller.overlay.error("Could not transcribe")
                return
            print(f"Heard: {text}")
            controller.on_final(text, wav_bytes)

        def _process_frame(self, frame_bytes):
            super()._process_frame(frame_bytes)
            if not self._speech:
                return
            seconds = len(self._segment) / bytes_per_second
            if seconds >= command_rules.MAX_COMMAND_SECONDS:
                self._force_end()
                return
            now = clock()
            if (
                not self._inflight
                and seconds >= MIN_PARTIAL_SECONDS
                and now - self._last_partial >= partial_interval
            ):
                self._last_partial = now
                self._inflight = True
                pcm = bytes(self._segment)
                if threaded:
                    threading.Thread(target=self._partial, args=(self._utt, pcm), daemon=True).start()
                else:
                    self._partial(self._utt, pcm)

        def _force_end(self):
            self._speech = False
            self._end_counter = 0
            wav = self._pcm_to_wav_bytes(bytes(self._segment))
            duration = len(self._segment) / bytes_per_second
            self._ended(wav, duration)

    return VoiceCommandListener()
