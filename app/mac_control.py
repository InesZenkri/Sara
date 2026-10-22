"""Mac control layer for Sara.

Lets Sara open apps, create and edit notes and type text, with a safety policy:

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
import re
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

# Sources for note text. "the terminal output" means the last lines Sara printed in
# this terminal (run_voice.py registers them); "the clipboard" means what you copied.
_terminal_source: Optional[Callable[[], str]] = None


def set_terminal_source(fn: Optional[Callable[[], str]]) -> None:
    global _terminal_source
    _terminal_source = fn


def read_clipboard() -> str:
    try:
        return subprocess.run(["pbpaste"], capture_output=True, text=True, timeout=5).stdout
    except (subprocess.SubprocessError, OSError):
        return ""


def resolve_note_text(spoken: str) -> str:
    """Turn a spoken body into real text: terminal output, clipboard, or the words as said."""
    words = (spoken or "").strip().lower().rstrip(".!")
    if re.match(r"(?:the |that |this )?(?:last |latest )?(?:terminal|console)(?: output| text| log)?\b", words):
        return (_terminal_source() if _terminal_source else "")[-MAX_TEXT_LENGTH:]
    if re.match(r"(?:(?:the |my )?(?:clipboard|clipboard text|copied text)|what i (?:just )?copied)\b", words):
        return read_clipboard()[:MAX_TEXT_LENGTH]
    return (spoken or "").strip()


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


# The note Sara made last, so "change that note's title" has something to point at.
_last_note: dict = {}


def _note_html(title: str, body: str) -> str:
    # Notes takes its title from the first line, so write the title as that line.
    return f"<div><h1>{_html_escape(title)}</h1></div><div>{_html_escape(body)}</div>"


def create_note(title: str, body: str = "") -> MacResult:
    if match_app("Notes") is None:
        return MacResult(False, "Notes is not on my allowed apps list", Risk.BLOCKED)
    title = (title or "Note from Sara").strip()[:200]
    body = (body or "")[:MAX_TEXT_LENGTH]
    if dry_run():
        _last_note.clear()
        _last_note.update(id="", title=title, body=body)
        return MacResult(True, f"[dry run] would create note '{title}'", Risk.SAFE)
    try:
        proc = _run(["osascript", "-e", _NOTE_SCRIPT, title, _note_html(title, body)])
    except (subprocess.SubprocessError, OSError) as exc:
        return MacResult(False, f"Could not create note: {exc}", Risk.SAFE)
    out = proc.stdout if isinstance(getattr(proc, "stdout", None), str) else ""
    _last_note.clear()
    _last_note.update(id=out.strip(), title=title, body=body)
    return MacResult(True, f"Created note '{title}'", Risk.SAFE, executed=True)


_EDIT_NOTE_SCRIPT = """
on run argv
    tell application "Notes"
        set n to note id (item 1 of argv)
        set body of n to (item 2 of argv)
    end tell
end run
"""


def edit_note(title: str = "", body: str = "", mode: str = "set") -> MacResult:
    """Rename and/or change the body of the note Sara created last.
    mode "set" replaces the body, "append" adds to it. Only that one note can be
    edited, never other notes."""
    if match_app("Notes") is None:
        return MacResult(False, "Notes is not on my allowed apps list", Risk.BLOCKED)
    if not _last_note:
        return MacResult(False, "I have not made a note yet, so there is nothing to edit", Risk.SAFE)
    if not (title or "").strip() and not (body or "").strip():
        return MacResult(False, "Nothing to change in the note", Risk.SAFE)
    new_title = (title or "").strip()[:200] or _last_note["title"]
    text = resolve_note_text(body) if (body or "").strip() else None
    if text is not None and not text.strip():
        return MacResult(False, "There was no text to put in the note (terminal output or clipboard empty)", Risk.SAFE)
    old_body = _last_note.get("body", "")
    if text is None:
        new_body = old_body
    elif mode == "append" and old_body:
        new_body = (old_body + "\n" + text)[:MAX_TEXT_LENGTH]
    else:
        new_body = text[:MAX_TEXT_LENGTH]
    what = []
    if (title or "").strip():
        what.append(f"title '{new_title}'")
    if text is not None:
        what.append(f"{'added to' if mode == 'append' else 'new'} body ({len(text)} characters)")
    summary = " and ".join(what)
    if dry_run():
        _last_note.update(title=new_title, body=new_body)
        return MacResult(True, f"[dry run] would edit note: {summary}", Risk.SAFE)
    if not _last_note.get("id"):
        return MacResult(False, "I lost track of the note I made, so I will not guess which one to edit", Risk.SAFE)
    try:
        _run(["osascript", "-e", _EDIT_NOTE_SCRIPT, _last_note["id"], _note_html(new_title, new_body)])
    except (subprocess.SubprocessError, OSError) as exc:
        return MacResult(False, f"Could not edit note: {exc}", Risk.SAFE)
    _last_note.update(title=new_title, body=new_body)
    return MacResult(True, f"Edited note: {summary}", Risk.SAFE, executed=True)


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
        "EDIT_NOTE": Risk.SAFE,
        "TYPE_TEXT": Risk.CONFIRM,
    }.get(action, Risk.BLOCKED)


def execute(
    action: str,
    *,
    app: str = "",
    title: str = "",
    text: str = "",
    mode: str = "set",
    confirm: Optional[Callable[[str], bool]] = None,
) -> MacResult:
    """Single entry point for the assistant: dispatch a Mac action by name."""
    if action == "OPEN_APP":
        return open_app(app)
    if action == "CREATE_NOTE":
        return create_note(title, text)
    if action == "EDIT_NOTE":
        return edit_note(title, text, mode)
    if action == "TYPE_TEXT":
        return type_text(text, confirm)
    return MacResult(False, f"Unknown Mac action '{action}'", Risk.BLOCKED)
