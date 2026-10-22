"""Text-to-speech choice for Sara without any paid service.

SARA_TTS = auto (default) | say | cartesia | none
  auto      Cartesia if CARTESIA_API_KEY is set (as before), otherwise the macOS
            built-in `say` voice, otherwise no voice replies.
  say       macOS built-in voice. Free, offline, nothing to install.
  cartesia  Cartesia only (needs CARTESIA_API_KEY and `pip install cartesia`).
  none      no spoken replies (notifications only).

Optional for `say`: SARA_SAY_VOICE (e.g. "Samantha", list with `say -v '?'`),
SARA_SAY_RATE (words per minute, e.g. 190).

Every backend has `synthesize(text) -> wav bytes`, which is what main.py plays.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from typing import Optional

MAX_CHARS = 2000


class SayTTS:
    def __init__(self) -> None:
        if shutil.which("say") is None:
            raise RuntimeError("macOS `say` command not found")
        self.voice = (os.getenv("SARA_SAY_VOICE") or "").strip()
        self.rate = (os.getenv("SARA_SAY_RATE") or "").strip()

    def command(self, out_path: str, text: str) -> list:
        cmd = ["say", "-o", out_path, "--file-format=WAVE", "--data-format=LEI16@22050"]
        if self.voice:
            cmd += ["-v", self.voice]
        if self.rate:
            cmd += ["-r", self.rate]
        # "--" so text starting with "-" is never read as an option; no shell involved.
        return cmd + ["--", text[:MAX_CHARS]]

    def synthesize(self, text: str, voice_id: Optional[str] = None) -> bytes:
        fd, path = tempfile.mkstemp(suffix=".wav")
        os.close(fd)
        try:
            subprocess.run(self.command(path, text), check=True, capture_output=True, timeout=60)
            with open(path, "rb") as f:
                return f.read()
        finally:
            try:
                os.unlink(path)
            except OSError:
                pass


def make_tts():
    mode = (os.getenv("SARA_TTS") or "auto").strip().lower()
    if mode == "none":
        print("TTS off (SARA_TTS=none)")
        return None
    if mode in ("auto", "cartesia") and os.getenv("CARTESIA_API_KEY"):
        try:
            from tts import CartesiaTTS

            client = CartesiaTTS()
            print("Cartesia TTS initialized successfully")
            return client
        except Exception as exc:
            print(f"Failed to initialize Cartesia TTS: {exc}")
            if mode == "cartesia":
                return None
    elif mode == "cartesia":
        print("SARA_TTS=cartesia but CARTESIA_API_KEY is not set")
        return None
    if mode in ("auto", "say"):
        try:
            client = SayTTS()
            print("Using macOS `say` for voice replies")
            return client
        except Exception as exc:
            print(f"No voice replies: {exc}")
            return None
    print(f"Unknown SARA_TTS '{mode}', use auto, say, cartesia or none")
    return None
