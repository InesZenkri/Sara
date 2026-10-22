"""Streaming intent detection for Sara ("acts while you speak").

Idea: while the user is still talking, classify each partial transcript with
a cheap, fast backend. When the intent is clear, stable across a couple of
partials, and SAFE to run early (e.g. opening an allow-listed app), fire it
before the sentence ends. Everything else waits for the final transcript.

This module has no macOS or network dependency at import time and is not
wired into main.py yet (that comes in the next PR, after the Mac control
layer is merged).

Backends (all implement `classify(text) -> IntentGuess`):
- RuleBackend: offline regexes. Default, needs no key, also used in tests.
- LLMBackend: any JSON-returning chat model through an injected callable;
  `openai_json_completer()` builds one for OpenAI-compatible APIs.
- JevBackend: optional. TypeSafe's Jev (POST /v1/systemone) classifies the
  action and whether the command is complete. Needs TYPESAFE_API_KEY.
  The design (partial transcript -> typed questions -> code policy) follows
  moritzkremb/jev-voice-browser (MIT): https://github.com/moritzkremb/jev-voice-browser
  Jev only picks the action; arguments are always extracted by code.
"""
from __future__ import annotations

import json
import os
import re
import time
import urllib.request
from dataclasses import dataclass, field
from typing import Callable, Optional, Protocol

ACTIONS = ("OPEN_APP", "CREATE_NOTE", "TYPE_TEXT")

# Only these may fire before the sentence is finished. Typing needs confirmation
# and notes need their full text, so both wait for the final transcript.
EARLY_SAFE_ACTIONS = frozenset({"OPEN_APP"})

DEFAULT_KNOWN_APPS = (
    "Notes", "Safari", "Calendar", "Reminders", "Finder", "Music", "Maps", "Calculator",
)


@dataclass
class IntentGuess:
    action: Optional[str] = None  # one of ACTIONS, or None for "not a Mac command"
    args: dict = field(default_factory=dict)
    confidence: float = 0.0
    complete: bool = False


class IntentBackend(Protocol):
    def classify(self, text: str) -> IntentGuess: ...


# --------------------------------------------------------------------------
# Argument extraction (always done by code, never by a model)
# --------------------------------------------------------------------------
_OPEN_RE = re.compile(
    r"\b(?:open|launch|start)\s+(?:up\s+)?(?:the\s+)?([a-z][a-z ]*?)(?:\s+app)?(?=\s+and\b|[.,!?]|$)",
    re.IGNORECASE,
)
_NOTE_RE = re.compile(
    r"\b(?:create|make|add|write)\s+(?:a\s+|an\s+|new\s+)*note"
    r"(?:\s+(?:called|titled|named|saying|that says|about|with))?\s*(.*)$",
    re.IGNORECASE,
)
_TYPE_RE = re.compile(r"^\s*(?:please\s+)?type\s+(.+)$", re.IGNORECASE)


def extract_args(action: str, text: str) -> dict:
    text = text.strip()
    if action == "OPEN_APP":
        m = _OPEN_RE.search(text)
        return {"app": m.group(1).strip()} if m else {}
    if action == "CREATE_NOTE":
        m = _NOTE_RE.search(text)
        body = m.group(1).strip().rstrip(".") if m else ""
        return {"title": body[:60], "text": body} if body else {}
    if action == "TYPE_TEXT":
        m = _TYPE_RE.search(text)
        return {"text": m.group(1).strip()} if m else {}
    return {}


def rule_action(text: str) -> Optional[str]:
    t = text.strip().lower()
    if _TYPE_RE.search(t):
        return "TYPE_TEXT"
    if _NOTE_RE.search(t) or re.search(r"\b(?:create|make|add|write)\s+(?:a\s+|new\s+)*note\b", t):
        return "CREATE_NOTE"
    if re.search(r"\b(?:open|launch|start)\b", t):
        return "OPEN_APP"
    return None


# --------------------------------------------------------------------------
# Backends
# --------------------------------------------------------------------------
class RuleBackend:
    """Offline backend. Confidence for OPEN_APP is high only for a known app,
    so a half-heard "open no" (a prefix of "open notes") never fires early."""

    def __init__(self, known_apps=DEFAULT_KNOWN_APPS):
        self.known = {a.lower() for a in known_apps}

    def classify(self, text: str) -> IntentGuess:
        action = rule_action(text)
        if action is None:
            return IntentGuess()
        args = extract_args(action, text)
        if action == "OPEN_APP":
            app = args.get("app", "").lower().removesuffix(" app")
            conf = 0.9 if app in self.known else 0.2
        else:
            conf = 0.9 if args else 0.4
        return IntentGuess(action, args, conf, complete=False)


class LLMBackend:
    """Classify with any chat model. `complete_json(prompt) -> dict` must return
    {"action": "OPEN_APP|CREATE_NOTE|TYPE_TEXT|NONE", "confidence": 0-1,
     "complete": bool}. Arguments still come from `extract_args`."""

    PROMPT = (
        "You classify a PARTIAL voice command for a Mac assistant. The speaker may "
        "not have finished. Reply with JSON only: "
        '{"action": "OPEN_APP" | "CREATE_NOTE" | "TYPE_TEXT" | "NONE", '
        '"confidence": number 0-1, "complete": boolean}. '
        "Use NONE for anything that is not clearly one of those commands.\n\n"
        "Partial transcript: "
    )

    def __init__(self, complete_json: Callable[[str], dict]):
        self.complete_json = complete_json

    def classify(self, text: str) -> IntentGuess:
        try:
            data = self.complete_json(self.PROMPT + json.dumps(text))
        except Exception:
            return IntentGuess()
        action = data.get("action")
        if action not in ACTIONS:
            return IntentGuess()
        return IntentGuess(
            action,
            extract_args(action, text),
            _clamp(data.get("confidence", 0.0)),
            bool(data.get("complete", False)),
        )


def openai_json_completer(model: Optional[str] = None) -> Callable[[str], dict]:
    """Build a `complete_json` for OpenAI's API (OPENAI_API_KEY). Lazy import."""
    from openai import OpenAI

    client = OpenAI()
    model = model or os.getenv("SARA_INTENT_MODEL", "gpt-5-mini")

    def complete(prompt: str) -> dict:
        resp = client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": prompt}],
            response_format={"type": "json_object"},
        )
        return json.loads(resp.choices[0].message.content)

    return complete


class JevBackend:
    """Optional TypeSafe Jev backend (needs TYPESAFE_API_KEY, early-access API).
    One request with two typed questions; code extracts the arguments.
    Untested against the live API (no key was available when this was written):
    request shape follows https://docs.typesafe.ai/api, response parsing is
    defensive."""

    URL = "https://api.typesafe.ai/v1/systemone"

    def __init__(self, api_key: Optional[str] = None, model: str = "jev-latest", timeout: float = 2.0):
        self.api_key = api_key or os.getenv("TYPESAFE_API_KEY")
        if not self.api_key:
            raise RuntimeError("TYPESAFE_API_KEY is not set")
        self.model = model
        self.timeout = timeout

    def _body(self, text: str) -> dict:
        return {
            "model": self.model,
            "state": text,
            "questions": {
                "action": {
                    "type": "choice",
                    "instructions": "Which Mac command is the speaker giving? The transcript may be cut off.",
                    "criteria": {
                        "OPEN_APP": "Open or launch an application",
                        "CREATE_NOTE": "Create a note",
                        "TYPE_TEXT": "Type text into the focused app",
                        "NONE": "Anything else, or unclear",
                    },
                },
                "complete": {
                    "type": "noul",
                    "instructions": "Is the command finished, with nothing more the speaker is about to add?",
                },
            },
        }

    def _post(self, body: dict) -> dict:
        req = urllib.request.Request(
            self.URL,
            data=json.dumps(body).encode(),
            headers={"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=self.timeout) as resp:
            return json.loads(resp.read())

    def classify(self, text: str) -> IntentGuess:
        try:
            answers = self._post(self._body(text)).get("answers", {})
        except Exception:
            return IntentGuess()
        probs = answers.get("action", {}).get("probabilities") or {}
        if not probs:
            return IntentGuess()
        action, conf = max(probs.items(), key=lambda kv: kv[1])
        if action not in ACTIONS:
            return IntentGuess()
        done = _clamp(answers.get("complete", {}).get("noul", 0.0)) >= 0.8
        return IntentGuess(action, extract_args(action, text), _clamp(conf), done)


def _clamp(x) -> float:
    try:
        return max(0.0, min(1.0, float(x)))
    except (TypeError, ValueError):
        return 0.0


def make_backend(name: Optional[str] = None) -> IntentBackend:
    """SARA_INTENT_BACKEND = rules (default) | llm | jev."""
    name = (name or os.getenv("SARA_INTENT_BACKEND", "rules")).lower()
    if name == "jev":
        return JevBackend()
    if name == "llm":
        return LLMBackend(openai_json_completer())
    return RuleBackend()


# --------------------------------------------------------------------------
# Streaming policy
# --------------------------------------------------------------------------
class StreamingIntentRunner:
    """Feed partial transcripts with `feed()`, then call `finish()` with the final one.

    `on_fire(guess, early)` is called at most once per utterance for an early
    action, and once at the end for anything not already run.
    """

    def __init__(
        self,
        backend: IntentBackend,
        on_fire: Callable[[IntentGuess, bool], None],
        *,
        threshold: float = 0.8,
        stable_n: int = 2,
        min_interval: float = 0.2,
        clock: Callable[[], float] = time.monotonic,
    ):
        self.backend = backend
        self.on_fire = on_fire
        self.threshold = threshold
        self.stable_n = stable_n
        self.min_interval = min_interval
        self.clock = clock
        self.reset()

    def reset(self) -> None:
        self._last_call = float("-inf")
        self._streak = 0
        self._last_key = None
        self._fired: Optional[IntentGuess] = None

    @staticmethod
    def _key(g: IntentGuess):
        return (g.action, json.dumps(g.args, sort_keys=True))

    def feed(self, partial: str) -> Optional[IntentGuess]:
        """Returns the guess if it fired early on this partial."""
        if self._fired is not None:
            return None
        now = self.clock()
        if now - self._last_call < self.min_interval:
            return None
        self._last_call = now
        g = self.backend.classify(partial)
        key = self._key(g)
        self._streak = self._streak + 1 if key == self._last_key and g.action else 1
        self._last_key = key
        if (
            g.action in EARLY_SAFE_ACTIONS
            and g.args
            and g.confidence >= self.threshold
            and self._streak >= self.stable_n
        ):
            self._fired = g
            self.on_fire(g, True)
            return g
        return None

    def finish(self, final_text: str) -> Optional[IntentGuess]:
        """Handle the final transcript. Skips it if the same action already ran early."""
        g = self.backend.classify(final_text)
        fired = self._fired
        self.reset()
        if g.action is None:
            return None
        if fired is not None and self._key(g) == self._key(fired):
            return None
        self.on_fire(g, False)
        return g
