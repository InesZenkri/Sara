"""Keep Sara from hearing her own voice.

Sara speaks through the Mac speakers and listens through its microphone, so
without a guard she transcribes herself ("Heard: I have not made a note yet...")
and may even act on it. Two layers:

1. Mute: while Sara speaks (and for a short tail after), microphone frames are dropped.
   Tail length: SARA_ECHO_TAIL seconds, default 1.0.
2. Match: a transcript that mostly repeats something Sara said in the last two
   minutes is dropped.

Pure Python, no audio or Mac dependencies.
"""
from __future__ import annotations

import os
import re
import threading
import time
from collections import deque
from typing import Callable, Deque, Tuple

REMEMBER_SECONDS = 120.0
MATCH_RATIO = 0.7
MIN_WORDS = 3


def _words(text: str) -> list:
    return re.findall(r"[a-z0-9']+", (text or "").lower())


class EchoGuard:
    def __init__(self, clock: Callable[[], float] = time.monotonic, tail: float = None):
        self._clock = clock
        self._tail = tail
        self._lock = threading.Lock()
        self._speaking = 0
        self._quiet_until = float("-inf")
        self._said: Deque[Tuple[float, set]] = deque(maxlen=30)

    @property
    def tail(self) -> float:
        if self._tail is not None:
            return self._tail
        try:
            return max(0.0, float(os.getenv("SARA_ECHO_TAIL", "1.0")))
        except ValueError:
            return 1.0

    def begin(self, text: str = "") -> None:
        """Call right before Sara starts to speak (before generating the audio)."""
        with self._lock:
            self._speaking += 1
            if text:
                self._said.append((self._clock(), set(_words(text))))

    def end(self) -> None:
        with self._lock:
            self._speaking = max(0, self._speaking - 1)
            self._quiet_until = self._clock() + self.tail

    def muted(self) -> bool:
        with self._lock:
            return self._speaking > 0 or self._clock() < self._quiet_until

    def is_echo(self, transcript: str) -> bool:
        words = set(_words(transcript))
        if len(words) < MIN_WORDS:
            return False
        now = self._clock()
        with self._lock:
            recent = [w for t, w in self._said if now - t <= REMEMBER_SECONDS]
        return any(len(words & said) / len(words) >= MATCH_RATIO for said in recent)


guard = EchoGuard()
