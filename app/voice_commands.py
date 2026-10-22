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
import sys
import threading
import time
from typing import Callable, Optional

import command_rules
import echo_guard
import mac_control
from intent import IntentGuess, RuleBackend, StreamingIntentRunner
from overlay import OverlayState

PARTIAL_INTERVAL = 0.8  # default seconds between partial transcriptions


def _log(msg: str) -> None:
    print(msg, flush=True)


MIN_RMS = 40  # int16 RMS under this is treated as silence (fan hiss, room tone)


def _min_rms() -> float:
    try:
        return float(os.getenv("SARA_MIN_RMS", str(MIN_RMS)))
    except ValueError:
        return float(MIN_RMS)


def wav_rms(wav: bytes) -> float:
    """RMS level of a 16-bit mono WAV (44-byte header), 0.0 if empty."""
    import array
    import math

    pcm = wav[44:]
    pcm = pcm[: len(pcm) // 2 * 2]
    if not pcm:
        return 0.0
    samples = array.array("h")
    samples.frombytes(pcm)
    if sys.byteorder == "big":
        samples.byteswap()
    return math.sqrt(sum(s * s for s in samples) / len(samples))


def _provider() -> str:
    return (
        os.getenv("TRANSCRIPTION_PROVIDER") or os.getenv("STT_PROVIDER") or os.getenv("MODEL_PROVIDER") or "openai"
    ).lower()


def partial_interval_from_env(default: float = PARTIAL_INTERVAL) -> float:
    """SARA_PARTIAL_INTERVAL in seconds. 0 (or negative) turns partial transcripts off,
    so audio is transcribed only once per command. Use 3 or more with rate-limited
    cloud speech-to-text. Unset: partials are OFF for local and Groq speech-to-text (a CPU
    transcription every 0.8 s cannot keep up, Groq free tier allows about 20 requests a minute) and every PARTIAL_INTERVAL seconds otherwise."""
    raw = (os.getenv("SARA_PARTIAL_INTERVAL") or "").strip()
    if not raw:
        return float("inf") if _provider() in ("local", "faster-whisper", "groq") else default
    try:
        value = float(raw)
    except ValueError:
        return default
    return float("inf") if value <= 0 else value
FOLLOWUP_SECONDS = 10.0  # after a command, the next one may skip the wake word for this long


def followup_seconds_from_env(default: float = FOLLOWUP_SECONDS) -> float:
    """SARA_FOLLOWUP_SECONDS. 0 turns the follow-up window off (wake word always needed)."""
    raw = (os.getenv("SARA_FOLLOWUP_SECONDS") or "").strip()
    try:
        return max(0.0, float(raw)) if raw else default
    except ValueError:
        return default


MIN_PARTIAL_SECONDS = 0.6  # do not transcribe less audio than this


def _describe(g: IntentGuess) -> str:
    if g.action == "OPEN_APP":
        return f"Opening {g.args.get('app', '?')}"
    if g.action == "CREATE_NOTE":
        return "Creating a note"
    if g.action == "EDIT_NOTE":
        return "Editing the note"
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
        on_quit: Optional[Callable[[], None]] = None,
        execute: Callable[..., mac_control.MacResult] = mac_control.execute,
        hotword: str = "sara",
        backend=None,
        clock: Callable[[], float] = time.monotonic,
        followup_seconds: Optional[float] = None,
        async_notify: bool = False,
    ):
        self.async_notify = async_notify  # speak/notify in the background so the action is not delayed
        self.timings: dict = {}
        self._clock = clock
        self.followup_seconds = followup_seconds_from_env() if followup_seconds is None else followup_seconds
        self._followup_until = float("-inf")
        self.overlay = overlay or OverlayState()
        self.legacy = legacy
        self.notify = notify
        self.confirm = confirm
        self.on_quit = on_quit  # called after "Sara, bye": stop the app
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

    def _open_followup(self) -> None:
        if self.followup_seconds > 0:
            self._followup_until = self._clock() + self.followup_seconds

    def _classify(self, text: str):
        """Like classify_utterance, but for a short time after a command a Mac command
        is accepted without the wake word ("Sara, open notes" ... "rename that note")."""
        kind, cmd = command_rules.classify_utterance(text, self.hotword)
        if kind == "ignore" and self._clock() <= self._followup_until:
            g = self.runner.backend.classify(text)
            if g.action and g.confidence >= self.runner.threshold:
                _log(f"[follow-up] accepted without wake word: {text.strip()!r}")
                return "command", text.strip()
        return kind, cmd

    # -- actions -----------------------------------------------------------
    def _fire(self, g: IntentGuess, early: bool) -> None:
        self._fired += 1
        self.overlay.doing(_describe(g))
        t_action = time.monotonic()
        res = self.execute(
            g.action,
            app=g.args.get("app", ""),
            title=g.args.get("title", ""),
            text=g.args.get("text", ""),
            mode=g.args.get("mode", "set"),
            confirm=self.confirm,
        )
        self.timings["action_ms"] = (time.monotonic() - t_action) * 1000
        self._open_followup()
        self.log.append(("early" if early else "final", g.action, res.ok, res.message))
        _log(f"[{'early ' if early else ''}action] {g.action} {'ok' if res.ok else 'FAILED'}: {res.message}")
        if res.ok:
            self.overlay.done(res.message)
        else:
            self.overlay.error(res.message)
        if self.notify and (not res.ok or res.executed):
            t_notify = time.monotonic()

            def _notify(message=res.message):
                try:
                    self.notify(message)
                except Exception:
                    pass

            if self.async_notify:
                threading.Thread(target=_notify, daemon=True).start()
            else:
                _notify()
            self.timings["notify_ms"] = (time.monotonic() - t_notify) * 1000

    # -- transcript events ---------------------------------------------------
    def on_partial(self, text: str) -> None:
        if self._cancelled:
            return
        kind, cmd = command_rules.classify_utterance(text, self.hotword)
        if kind == "ignore":
            return
        if kind == "quit":
            return  # only a finished utterance can stop Sara
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
        kind, cmd = self._classify(text)
        cancelled = self._cancelled or kind == "cancel"
        try:
            if kind == "ignore":
                if not self._cancelled:
                    self.overlay.hide()
                return "cancelled" if self._cancelled else "ignored"
            if kind == "quit":
                self.overlay.done("Bye")
                self.log.append(("quit",))
                if self.on_quit is not None:
                    try:
                        self.on_quit()
                    except Exception as exc:
                        print(f"Quit handler failed: {exc}", flush=True)
                return "quit"
            if cancelled:
                self.overlay.done("Cancelled")
                return "cancelled"
            if kind == "wake":
                self._open_followup()
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
    on first use). Local options: SARA_WHISPER_MODEL (default "base.en"; "tiny.en" is faster, "small.en" more accurate), SARA_WHISPER_LANGUAGE
    (default "en"; "auto" detects the language, slower and less reliable on short commands),
    SARA_WHISPER_THREADS (CPU threads, default 4, so the Mac stays usable)."""
    provider = _provider()
    if provider in ("local", "faster-whisper"):
        import io

        from faster_whisper import WhisperModel

        language = (os.getenv("SARA_WHISPER_LANGUAGE") or "en").strip()
        if language.lower() in ("", "auto"):
            language = None
        # English-only models are smaller and faster; use a multilingual one only for auto-detect
        name = os.getenv("SARA_WHISPER_MODEL") or ("base.en" if language == "en" else "base")
        try:
            threads = max(1, int(os.getenv("SARA_WHISPER_THREADS", "4")))
        except ValueError:
            threads = 4
        _log(f"Loading Whisper model '{name}' (first run downloads it)...")
        model = WhisperModel(name, device="cpu", compute_type="int8", cpu_threads=threads)
        _log("Whisper model ready.")
        lock = threading.Lock()  # one transcription at a time on the shared model

        def local_t(wav: bytes) -> str:
            with lock:
                segments, _ = model.transcribe(
                    io.BytesIO(wav), language=language, beam_size=1, initial_prompt="Sara, open Notes.",
                    vad_filter=True, condition_on_previous_text=False
                )
                return " ".join(s.text.strip() for s in segments).strip()

        return local_t
    if provider == "groq":
        from groq import Groq

        client = Groq(api_key=os.getenv("GROQ_API_KEY"))
        gmodel = os.getenv("SARA_GROQ_STT_MODEL", "whisper-large-v3")
        lang = (os.getenv("SARA_WHISPER_LANGUAGE") or "en").strip()
        lang = None if lang.lower() in ("", "auto") else lang

        def groq_t(wav: bytes) -> str:
            kwargs = {"language": lang} if lang else {}
            return (
                client.audio.transcriptions.create(file=("audio.wav", wav), model=gmodel, **kwargs).text or ""
            )

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
    try:  # 3 = strictest: steady fan or room noise is less likely to count as speech
        vadmod.VAD_MODE = min(3, max(0, int(os.getenv("SARA_VAD_MODE", "3"))))
    except ValueError:
        vadmod.VAD_MODE = 3

    vadmod.END_CONSECUTIVE = max(1, int(command_rules.SILENCE_SECONDS * 1000 / vadmod.FRAME_DURATION_MS))
    bytes_per_second = vadmod.SAMPLE_RATE * vadmod.BYTES_PER_SAMPLE

    class VoiceCommandListener(vadmod.VADStream):
        def __init__(self):
            super().__init__(on_speech_start=self._started, on_speech_end=self._ended)
            self._utt = 0
            self._last_partial = 0.0
            self._inflight = False
            self._final_lock = threading.Lock()  # finals run one at a time, in order
            self.final_threads: list = []

        def _started(self):
            _log("[speech start]")
            self._utt += 1
            self._last_partial = clock()
            controller.start_utterance()
            controller.overlay.listening("")

        def _partial(self, utt: int, pcm: bytes):
            try:
                wav = self._pcm_to_wav_bytes(pcm)
                if wav_rms(wav) < _min_rms():
                    return
                text = transcribe(wav)
                if utt == self._utt:  # drop results from an utterance that already ended
                    controller.on_partial(text)
            except Exception as exc:
                _log(f"Partial transcription failed: {exc}")
            finally:
                self._inflight = False

        def _ended(self, wav_bytes, duration):
            self._utt += 1  # invalidates any partial still in flight
            _log(f"[speech end] {duration:.1f}s")
            if duration < 0.4:
                controller.overlay.hide()
                return
            if threaded:
                # Transcribing can take seconds on a CPU. Do it off the audio loop so
                # frames keep being read and the queue does not grow.
                t = threading.Thread(target=self._final, args=(wav_bytes,), daemon=True)
                self.final_threads.append(t)
                self.final_threads = [x for x in self.final_threads if x.is_alive() or x is t]
                t.start()
            else:
                self._final(wav_bytes)

        def _final(self, wav_bytes):
            with self._final_lock:
                level = wav_rms(wav_bytes)
                if level < _min_rms():
                    _log(f"[skipped] too quiet (level {level:.0f}, SARA_MIN_RMS={_min_rms():.0f})")
                    controller.overlay.hide()
                    return
                t0 = time.monotonic()
                try:
                    text = transcribe(wav_bytes)
                except Exception as exc:
                    _log(f"Final transcription failed: {exc}")
                    controller.overlay.error("Could not transcribe")
                    return
                t1 = time.monotonic()
                if not (text or "").strip():
                    _log(f"[skipped] empty transcript (STT {(t1 - t0) * 1000:.0f} ms)")
                    controller.overlay.hide()
                    return
                if echo_guard.guard.is_echo(text):
                    _log(f"[skipped] sounds like Sara's own voice: {text!r}")
                    controller.overlay.hide()
                    return
                _log(f"Heard: {text}  (STT {(t1 - t0) * 1000:.0f} ms)")
                controller.timings.clear()
                try:
                    result = controller.on_final(text, wav_bytes)
                except Exception as exc:
                    _log(f"Handling failed: {exc}")
                    return
                t2 = time.monotonic()
                hint = " (say the wake word first, e.g. 'Sara, open Notes')" if result == "ignored" else ""
                parts = [f"{k[:-3]} {v:.0f} ms" for k, v in controller.timings.items()]
                parts.append(f"total {(t2 - t1) * 1000:.0f} ms")
                _log(f"  -> {result}{hint}  ({', '.join(parts)})")

        def _process_frame(self, frame_bytes):
            if echo_guard.guard.muted():  # Sara is speaking: do not listen to herself
                self._speech = False
                self._start_counter = 0
                self._end_counter = 0
                self._segment.clear()
                return
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
