"""Floating "listening / heard / doing" overlay for Sara.

Two parts:
- OverlayState: a small thread-safe model (phase + text) with listeners. No GUI
  imports, so it is easy to test and safe to use from audio/network threads.
- TkOverlay: a minimal always-on-top pill that renders an OverlayState.
  Optional and OFF by default (set SARA_OVERLAY=true). Uses tkinter, which
  ships with Python. Written without access to a Mac: see the PR notes.

Not wired into main.py yet; that happens in the wiring PR.
"""
from __future__ import annotations

import os
import threading
from dataclasses import dataclass
from enum import Enum
from typing import Callable, List


class Phase(Enum):
    IDLE = "idle"
    LISTENING = "listening"
    HEARD = "heard"
    DOING = "doing"
    DONE = "done"
    ERROR = "error"


LABELS = {
    Phase.LISTENING: "Listening",
    Phase.HEARD: "Heard",
    Phase.DOING: "Doing",
    Phase.DONE: "Done",
    Phase.ERROR: "Oops",
}

MAX_TEXT = 120


def overlay_enabled() -> bool:
    return os.getenv("SARA_OVERLAY", "false").lower() == "true"


@dataclass(frozen=True)
class OverlaySnapshot:
    phase: Phase = Phase.IDLE
    text: str = ""

    @property
    def visible(self) -> bool:
        return self.phase is not Phase.IDLE

    @property
    def title(self) -> str:
        return LABELS.get(self.phase, "")


def _clip(text: str) -> str:
    text = " ".join((text or "").split())
    return text if len(text) <= MAX_TEXT else text[: MAX_TEXT - 1] + "\u2026"


class OverlayState:
    """Thread-safe phase + text. Listeners get an OverlaySnapshot on every change."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._snap = OverlaySnapshot()
        self._listeners: List[Callable[[OverlaySnapshot], None]] = []

    def subscribe(self, fn: Callable[[OverlaySnapshot], None]) -> None:
        with self._lock:
            self._listeners.append(fn)

    @property
    def snapshot(self) -> OverlaySnapshot:
        with self._lock:
            return self._snap

    def _set(self, phase: Phase, text: str) -> None:
        snap = OverlaySnapshot(phase, _clip(text))
        with self._lock:
            if snap == self._snap:
                return
            self._snap = snap
            listeners = list(self._listeners)
        for fn in listeners:
            try:
                fn(snap)
            except Exception:  # a broken listener must never break the assistant
                pass

    def listening(self, partial: str = "") -> None:
        self._set(Phase.LISTENING, partial)

    def heard(self, text: str) -> None:
        self._set(Phase.HEARD, text)

    def doing(self, what: str) -> None:
        self._set(Phase.DOING, what)

    def done(self, message: str = "") -> None:
        self._set(Phase.DONE, message)

    def error(self, message: str = "") -> None:
        self._set(Phase.ERROR, message)

    def hide(self) -> None:
        self._set(Phase.IDLE, "")


class TkOverlay:
    """Tiny always-on-top pill. Call `run()` on the main thread (Tk needs it);
    feed it from other threads through the OverlayState it is given."""

    HIDE_AFTER_MS = {Phase.DONE: 1500, Phase.ERROR: 2500}

    def __init__(self, state: OverlayState) -> None:
        import queue
        import tkinter as tk

        self._tk = tk
        self._q: "queue.Queue[OverlaySnapshot]" = queue.Queue()
        self._hide_job = None
        self._stop = False
        self.root = tk.Tk()
        self.root.overrideredirect(True)
        self.root.attributes("-topmost", True)
        try:
            self.root.attributes("-alpha", 0.92)
        except tk.TclError:
            pass
        self.root.configure(bg="#1c1c1e")
        self.title = tk.Label(self.root, fg="#ffffff", bg="#1c1c1e", font=("Helvetica", 12, "bold"))
        self.body = tk.Label(
            self.root, fg="#d1d1d6", bg="#1c1c1e", font=("Helvetica", 12), wraplength=360, justify="left"
        )
        self.title.pack(anchor="w", padx=14, pady=(8, 0))
        self.body.pack(anchor="w", padx=14, pady=(0, 8))
        self.root.withdraw()
        state.subscribe(self._q.put)  # queue.Queue.put is thread-safe
        self.root.after(50, self._poll)

    def request_stop(self) -> None:
        """Ask the window loop to end. Safe to call from any thread."""
        self._stop = True

    def _poll(self) -> None:
        import queue

        if self._stop:
            self.root.quit()
            return

        try:
            while True:
                self._render(self._q.get_nowait())
        except queue.Empty:
            pass
        self.root.after(50, self._poll)

    def _render(self, snap: OverlaySnapshot) -> None:
        if self._hide_job is not None:
            self.root.after_cancel(self._hide_job)
            self._hide_job = None
        if not snap.visible:
            self.root.withdraw()
            return
        self.title.config(text=snap.title)
        self.body.config(text=snap.text)
        self.root.update_idletasks()
        w, h = self.root.winfo_reqwidth(), self.root.winfo_reqheight()
        x = (self.root.winfo_screenwidth() - w) // 2
        self.root.geometry(f"+{x}+40")
        self.root.deiconify()
        delay = self.HIDE_AFTER_MS.get(snap.phase)
        if delay:
            self._hide_job = self.root.after(delay, self.root.withdraw)

    def run(self) -> None:
        self.root.mainloop()


def make_overlay():
    """Return (OverlayState, TkOverlay or None). The state always works, so
    callers can report to it unconditionally; the GUI exists only if enabled."""
    state = OverlayState()
    if not overlay_enabled():
        return state, None
    try:
        return state, TkOverlay(state)
    except Exception as exc:  # no display, no tkinter, ...
        print(f"Overlay disabled: {exc}")
        return state, None
