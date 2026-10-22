"""Command-boundary rules for Sara: fuzzy wake word, cancel words, end of command.

Pure functions plus a small endpointing helper. No audio, GUI or network
dependencies, so all of it is unit tested. Not wired into main.py yet.

Ideas (not code) borrowed from the design notes in Louis-CFM/coucou
(docs/VOICE.md, MIT): end a command after about 1.2 s of silence or 10 s
total, a cancel word, fuzzy wake-word matching, and a feature switch that is
off by default. No code or assets from that repo are used.
"""
from __future__ import annotations

import difflib
import os
import re
import time
from typing import Callable, Optional, Tuple

SILENCE_SECONDS = 1.2
MAX_COMMAND_SECONDS = 10.0

CANCEL_PHRASES = (
    "cancel",
    "never mind",
    "nevermind",
    "forget it",
    "stop",
    "abort",
    "annule",
    "laisse tomber",
)

# Said after the wake word to stop Sara for good ("Sara, bye"). The WHOLE command
# must be one of these, so "thanks" inside a longer command never quits.
QUIT_PHRASES = (
    "bye",
    "bye bye",
    "goodbye",
    "good bye",
    "see you",
    "see you later",
    "thanks",
    "thank you",
    "thank you very much",
    "thanks a lot",
    "thx",
    "that was it",
    "that is it",
    "thats it",
    "that s it",
    "that's it",
    "that will be all",
    "that ll be all",
    "that'll be all",
    "stop listening",
    "go to sleep",
    "quit",
    "exit",
)

# Spellings an STT engine may produce for "sara".
SARA_VARIANTS = ("sara", "sarah", "sera", "zara", "sarra", "saara", "sarrah", "sahra")
_LEADERS = ("", "hey", "hi", "ok", "okay", "yo", "hello", "hallo")


def streaming_enabled() -> bool:
    """Master switch for the whole voice-command feature. OFF by default."""
    return os.getenv("SARA_VOICE_COMMANDS", "false").lower() == "true"


def _words(text: str) -> list:
    return re.findall(r"[a-z\u00e0-\u00ff']+", text.lower())


def _is_sara(word: str, hotword: str = "sara") -> bool:
    if word == hotword or word in SARA_VARIANTS:
        return True
    return len(word) >= 3 and difflib.SequenceMatcher(None, word, hotword).ratio() >= 0.85


def split_wake_word(text: str, hotword: str = "sara") -> Optional[str]:
    """If `text` starts with (leader +) the wake word, return the command after it
    ("" if only the wake word was said). Otherwise None.
    Only the first 3 words are considered, so a name later in a sentence
    ("tell sara hi" inside a long sentence) does not trigger."""
    words = _words(text)
    for i, w in enumerate(words[:3]):
        if _is_sara(w, hotword) and all(x in _LEADERS for x in words[:i]):
            return " ".join(words[i + 1 :])
    return None


def is_cancel(text: str) -> bool:
    """True if the whole utterance is (essentially) a cancel word."""
    t = " ".join(_words(text))
    if not t:
        return False
    t = re.sub(r"^(?:sara|please|ok|okay|hey)\s+", "", t)
    t = re.sub(r"\s+(?:please|sara)$", "", t)
    return t in CANCEL_PHRASES


def is_quit(text: str) -> bool:
    """True if the whole utterance (after the wake word) is a goodbye phrase."""
    t = " ".join(_words(text))
    if not t:
        return False
    t = re.sub(r"^(?:sara|please|ok|okay|hey)\s+", "", t)
    t = re.sub(r"\s+(?:please|sara)$", "", t)
    return t in QUIT_PHRASES


class Endpointer:
    """Decides when a command has ended.

    Call `update(speaking)` on every audio frame / VAD tick. It returns:
      None        keep listening
      "silence"   speech stopped for `silence_seconds`
      "timeout"   `max_seconds` since the command started
    A command only counts as started after the first speech, so leading
    silence does not end it.
    """

    def __init__(
        self,
        silence_seconds: float = SILENCE_SECONDS,
        max_seconds: float = MAX_COMMAND_SECONDS,
        clock: Callable[[], float] = time.monotonic,
    ):
        self.silence_seconds = silence_seconds
        self.max_seconds = max_seconds
        self.clock = clock
        self.reset()

    def reset(self) -> None:
        self._start: Optional[float] = None
        self._last_speech: Optional[float] = None

    def update(self, speaking: bool) -> Optional[str]:
        now = self.clock()
        if self._start is None:
            if not speaking:
                return None
            self._start = now
        if speaking:
            self._last_speech = now
        if now - self._start >= self.max_seconds:
            return "timeout"
        if self._last_speech is not None and now - self._last_speech >= self.silence_seconds:
            return "silence"
        return None


def classify_utterance(text: str, hotword: str = "sara") -> Tuple[str, str]:
    """('ignore'|'wake'|'quit'|'cancel'|'command', command_text)."""
    rest = split_wake_word(text, hotword)
    if rest is None:
        return "ignore", ""
    if not rest:
        return "wake", ""
    if is_quit(rest):
        return "quit", ""
    if is_cancel(rest):
        return "cancel", ""
    return "command", rest
