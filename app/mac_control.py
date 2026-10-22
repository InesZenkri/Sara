"""Mac control layer for Sara.

Lets Sara open apps, create notes and type text, with a safety policy:

- SAFE actions (open an allow-listed app, create a note) run right away.
- CONFIRM actions (typing into the focused app) need an explicit yes from
  the user through the `confirm` callback.
- Anything not on the allow-list is refused.

Nothing here uses a shell. Arguments are passed as argv lists, and text for
AppleScript is passed as an argument, never spliced into the script, so a
spoken or LLM-generated string cannot inject commands.

Set SARA_MAC_DRY_RUN=true to log what would happen without touching the Mac.
Set SARA_ALLOWED_APPS="Notes,Safari,..." to change the allow-list.
"""
from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass
from enum import Enum
from typing import Callable, Optional

DEFAULT_ALLOWED_APPS = (
    "Notes",
    "Safari",
    "Calendar",
    "Reminders",
    "Finder",
    "Music",
    "Maps",
    "Calculator",
)

MAX_TEXT_LENGTH = 5000


class Risk(Enum):
    SAFE = "safe"
    CONFIRM = "confirm"
    BLOCKED = "blocked"


@dataclass
class MacResult:
    ok: bool
    message: str
    risk: Risk = Risk.SAFE
    executed: bool = False


def allowed_apps() -> list[str]:
    raw = os.getenv("SARA_ALLOWED_APPS")
    if raw:
        return [a.strip() for a in raw.split(",") if a.strip()]
    return list(DEFAULT_ALLOWED_APPS)


def dry_run() -> bool:
    return os.getenv("SARA_MAC_DRY_RUN", "false").lower() == "true"


def match_app(name: str) -> Optional[str]:
    """Return the canonical allow-listed app name for a spoken name, or None."""
    wanted = (name or "").strip().lower().removesuffix(" app").strip()
    for app in allowed_apps():
        if app.lower() == wanted:
            return app
    return None


def _run(argv: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(argv, check=True, capture_output=True, text=True, timeout=15)


def open_app(name: str) -> MacResult:
    app = match_app(name)
    if app is None:
        return MacResult(False, f"'{name}' is not on my allowed apps list", Risk.BLOCKED)
    if dry_run():
        return MacResult(True, f"[dry run] would open {app}", Risk.SAFE)
    try:
        _run(["open", "-a", app])
    except (subprocess.SubprocessError, OSError) as exc:
        return MacResult(False, f"Could not open {app}: {exc}", Risk.SAFE)
    return MacResult(True, f"Opened {app}", Risk.SAFE, executed=True)


_NOTE_SCRIPT = """
on run argv
    set noteTitle to item 1 of argv
    set noteBody to item 2 of argv
    tell application "Notes"
        make new note with properties {name:noteTitle, body:noteBody}
    end tell
end run
"""


def _html_escape(text: str) -> str:
    return (
        text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace("\n", "<br>")
    )


def create_note(title: str, body: str = "") -> MacResult:
    if match_app("Notes") is None:
        return MacResult(False, "Notes is not on my allowed apps list", Risk.BLOCKED)
    title = (title or "Note from Sara").strip()[:200]
    body = (body or "")[:MAX_TEXT_LENGTH]
    if dry_run():
        return MacResult(True, f"[dry run] would create note '{title}'", Risk.SAFE)
    try:
        _run(["osascript", "-e", _NOTE_SCRIPT, title, _html_escape(body)])
    except (subprocess.SubprocessError, OSError) as exc:
        return MacResult(False, f"Could not create note: {exc}", Risk.SAFE)
    return MacResult(True, f"Created note '{title}'", Risk.SAFE, executed=True)


_TYPE_SCRIPT = """
on run argv
    tell application "System Events" to keystroke (item 1 of argv)
end run
"""


def type_text(text: str, confirm: Optional[Callable[[str], bool]] = None) -> MacResult:
    """Type text into whatever app is focused. Always needs confirmation."""
    text = (text or "")[:MAX_TEXT_LENGTH]
    if not text:
        return MacResult(False, "Nothing to type", Risk.CONFIRM)
    preview = text if len(text) <= 60 else text[:57] + "..."
    if confirm is None or not confirm(f"Type into the focused app: {preview!r}?"):
        return MacResult(False, "Typing was not confirmed", Risk.CONFIRM)
    if dry_run():
        return MacResult(True, f"[dry run] would type {preview!r}", Risk.CONFIRM)
    try:
        _run(["osascript", "-e", _TYPE_SCRIPT, text])
    except (subprocess.SubprocessError, OSError) as exc:
        return MacResult(False, f"Could not type text: {exc}", Risk.CONFIRM)
    return MacResult(True, "Typed text", Risk.CONFIRM, executed=True)


def risk_of(action: str) -> Risk:
    """Policy table used by the planner to decide when an action may fire early."""
    return {
        "OPEN_APP": Risk.SAFE,
        "CREATE_NOTE": Risk.SAFE,
        "TYPE_TEXT": Risk.CONFIRM,
    }.get(action, Risk.BLOCKED)


def execute(
    action: str,
    *,
    app: str = "",
    title: str = "",
    text: str = "",
    confirm: Optional[Callable[[str], bool]] = None,
) -> MacResult:
    """Single entry point for the assistant: dispatch a Mac action by name."""
    if action == "OPEN_APP":
        return open_app(app)
    if action == "CREATE_NOTE":
        return create_note(title, text)
    if action == "TYPE_TEXT":
        return type_text(text, confirm)
    return MacResult(False, f"Unknown Mac action '{action}'", Risk.BLOCKED)
