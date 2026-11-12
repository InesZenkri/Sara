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

import datetime
import os
import re
import subprocess
from dataclasses import dataclass
from enum import Enum
from typing import Callable, NamedTuple, Optional

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

MAX_REMINDER_TITLE = 200

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
    # A clarification is a question back to the user, not a failure. `pending`
    # holds what Sara still owes the user an answer on (e.g. a reminder task
    # waiting for a clearer time); the voice layer keeps it for the follow-up window.
    clarification: bool = False
    pending: Optional[dict] = None


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
    for app in allowed_apps():  # "open note" for Notes
        if wanted and app.lower().rstrip("s") == wanted.rstrip("s"):
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


_FIND_NOTE_SCRIPT = """
tell application "Notes"
    if (count of notes) is 0 then return ""
    set best to first note
    set bestDate to modification date of best
    set i to 0
    repeat with n in notes
        set i to i + 1
        if i > 60 then exit repeat
        if (modification date of n) > bestDate then
            set best to contents of n
            set bestDate to modification date of best
        end if
    end repeat
    return (id of best) & (character id 31) & (name of best) & (character id 31) & (body of best)
end tell
"""


def lookup_recent_note() -> dict:
    """The most recently modified note in Notes (looks at the newest 60), or {}."""
    try:
        proc = _run(["osascript", "-e", _FIND_NOTE_SCRIPT])
    except (subprocess.SubprocessError, OSError):
        return {}
    out = proc.stdout if isinstance(getattr(proc, "stdout", None), str) else ""
    parts = out.rstrip("\n").split("\x1f", 2)
    if len(parts) != 3 or not parts[0].strip():
        return {}
    return {"id": parts[0].strip(), "title": parts[1], "html": parts[2], "body": ""}


def resolve_title(title: str) -> str:
    """"Sara and the date of today" -> "Sara 2026-10-22". Format: SARA_DATE_FORMAT (default %Y-%m-%d)."""
    import datetime

    fmt = os.getenv("SARA_DATE_FORMAT", "%Y-%m-%d")
    today = datetime.date.today().strftime(fmt)
    pattern = r"(?:\s*(?:,|and|with)\s+)?(?:the\s+)?(?:date\s+of\s+today|today'?s\s+date|current\s+date|date\s+today|date)\b"
    out = re.sub(pattern, " " + today, title or "", flags=re.IGNORECASE)
    return re.sub(r"\s+", " ", out).strip()


def edit_note(title: str = "", body: str = "", mode: str = "set") -> MacResult:
    """Rename and/or change the body of "that note": the note Sara created last, or, when
    she has made none this run, the most recently modified note in Notes.
    mode "set" replaces the body, "append" adds to it. Never any other note."""
    if match_app("Notes") is None:
        return MacResult(False, "Notes is not on my allowed apps list", Risk.BLOCKED)
    if not (title or "").strip() and not (body or "").strip():
        return MacResult(False, "Nothing to change in the note", Risk.SAFE)
    title = resolve_title(title)
    if not _last_note:
        if dry_run():
            _last_note.update(id="", title="(most recent note)", body="")
        else:
            found = lookup_recent_note()
            if not found:
                return MacResult(False, "I could not find a note to edit", Risk.SAFE)
            _last_note.update(found)
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
    if "html" in _last_note:  # an older note: keep its formatting, change only what was asked
        html = _last_note["html"]
        if (title or "").strip():
            html = html.replace(_html_escape(_last_note["title"]), _html_escape(new_title), 1)
        if text is not None:
            html = _note_html(new_title, text) if mode != "append" else html + f"<div>{_html_escape(text)}</div>"
        new_html = html
    else:
        new_html = _note_html(new_title, new_body)
    try:
        _run(["osascript", "-e", _EDIT_NOTE_SCRIPT, _last_note["id"], new_html])
    except (subprocess.SubprocessError, OSError) as exc:
        return MacResult(False, f"Could not edit note: {exc}", Risk.SAFE)
    if "html" in _last_note:
        _last_note.update(title=new_title, html=new_html)
    else:
        _last_note.update(title=new_title, body=new_body)
    return MacResult(True, f"Edited note: {summary}", Risk.SAFE, executed=True)


# ---------------------------------------------------------------------------
# Reminders ("Sara, remind me to call the dentist at 5 pm")
#
# Time phrases are parsed here, next to the AppleScript that needs the due date.
# An unclear time is a question back to the user, never a guess.
# ---------------------------------------------------------------------------

_WORD_HOURS = {
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6,
    "seven": 7, "eight": 8, "nine": 9, "ten": 10, "eleven": 11, "twelve": 12,
}

_DURATION_RE = re.compile(r"\bin\s+(\d+)\s*(minutes?|mins?|hours?|hrs?)\b", re.IGNORECASE)
_VAGUE_IN_RE = re.compile(r"\bin\s+a\s+(?:bit|while|moment)\b", re.IGNORECASE)
_DAY_RE = re.compile(r"\b(today|tomorrow)\b", re.IGNORECASE)
_NAMED_TIME_RE = re.compile(r"\bat\s+(noon|midday|midnight)\b", re.IGNORECASE)
_DIGIT_TIME_RE = re.compile(
    r"\bat\s+(\d{1,2})(?::(\d{2}))?\s*(a\.?\s?m\.?|p\.?\s?m\.?)?\b", re.IGNORECASE
)
_WORD_TIME_RE = re.compile(
    r"\bat\s+(" + "|".join(_WORD_HOURS) + r")\s*(a\.?\s?m\.?|p\.?\s?m\.?)\b", re.IGNORECASE
)
_VAGUE_DAY_RE = re.compile(r"\btonight\b|\bthis\s+(?:morning|afternoon|evening)\b", re.IGNORECASE)

_ASK_FORMAT = "I did not catch the time. What time? For example 'at 5 pm' or 'in 20 minutes'."


class When(NamedTuple):
    due: Optional[datetime.datetime]
    error: Optional[str]
    # True when the phrase named a clock time without a day ("at 5 pm", "at noon").
    # Then a time that is already past today rolls to tomorrow, which Sara must
    # confirm out loud instead of silently picking the day.
    bare_clock: bool = False


def _hour24(hour: int, ampm: str) -> int:
    pm = ampm.lower().replace(".", "").strip().startswith("p")
    return hour % 12 + (12 if pm else 0)


def _combine(day: Optional[datetime.date], hour: int, minute: int, now: datetime.datetime) -> datetime.datetime:
    """An explicit day stays on that day; a bare clock time means its next occurrence."""
    if day is not None:
        return datetime.datetime(day.year, day.month, day.day, hour, minute)
    due = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if due <= now:
        due += datetime.timedelta(days=1)
    return due


def parse_reminder_when(when: str, now: Optional[datetime.datetime] = None) -> When:
    """Resolve a spoken time phrase to a due datetime.

    Returns a When:
      When(due=datetime, error=None)   - time understood
      When(due=None, error=None)       - no time given: reminder without a due date
      When(due=None, error=message)    - unclear time: ask instead of guessing
    """
    now = now or datetime.datetime.now()
    text = (when or "").strip().lower().rstrip(".!?").strip()
    if not text:
        return When(None, None)

    m = _DURATION_RE.search(text)
    if m:
        n = int(m.group(1))
        if m.group(2).lower().startswith("h"):
            return When(now + datetime.timedelta(hours=n), None)
        return When(now + datetime.timedelta(minutes=n), None)
    if _VAGUE_IN_RE.search(text):
        return When(None, "I need an exact time. For example 'in 20 minutes' or 'at 5 pm'.")

    day = None
    dm = _DAY_RE.search(text)
    if dm:
        day = now.date() if dm.group(1) == "today" else now.date() + datetime.timedelta(days=1)

    m = _NAMED_TIME_RE.search(text)
    if m:
        hour = 0 if m.group(1) == "midnight" else 12
        return When(_combine(day, hour, 0, now), None, bare_clock=day is None)

    tm = _DIGIT_TIME_RE.search(text)
    if tm:
        hour, minute, ampm = int(tm.group(1)), int(tm.group(2) or 0), tm.group(3)
        if ampm:
            if hour > 12:
                return When(None, _ASK_FORMAT)
            hour = _hour24(hour, ampm)
        elif tm.group(2) is None and hour <= 12:
            # "at 5" alone: could be morning or afternoon, so ask.
            return When(None, f"Did you mean {hour} am or {hour} pm?")
        elif hour > 23:
            return When(None, _ASK_FORMAT)
        return When(_combine(day, hour, minute, now), None, bare_clock=day is None)

    wm = _WORD_TIME_RE.search(text)
    if wm:
        return When(_combine(day, _hour24(_WORD_HOURS[wm.group(1).lower()], wm.group(2)), 0, now), None, bare_clock=day is None)

    if _VAGUE_DAY_RE.search(text):
        return When(None, "I need an exact time. For example 'at 6 pm'.")
    if day is not None:
        return When(None, f"What time {dm.group(1)}? For example '{dm.group(1)} at 9 am'.")
    return When(None, _ASK_FORMAT)


def _describe_due(due: datetime.datetime, now: Optional[datetime.datetime] = None) -> str:
    now = now or datetime.datetime.now()
    time_txt = f"{(due.hour % 12) or 12}:{due.minute:02d} {'AM' if due.hour < 12 else 'PM'}"
    if due.date() == now.date():
        return f"today at {time_txt}"
    if due.date() == now.date() + datetime.timedelta(days=1):
        return f"tomorrow at {time_txt}"
    return f"{due.strftime('%A, %B')} {due.day} at {time_txt}"


_REMIND_SCRIPT = """
on run argv
    set remName to item 1 of argv
    tell application "Reminders"
        if (count of lists) is 0 then error "No Reminders list found"
        if (count of argv) > 1 then
            set dueDate to current date
            set day of dueDate to 1
            set year of dueDate to (item 2 of argv) as integer
            set month of dueDate to (item 3 of argv) as integer
            set day of dueDate to (item 4 of argv) as integer
            set hours of dueDate to (item 5 of argv) as integer
            set minutes of dueDate to (item 6 of argv) as integer
            set seconds of dueDate to 0
            tell default list to make new reminder with properties {name:remName, due date:dueDate}
        else
            tell default list to make new reminder with properties {name:remName}
        end if
    end tell
end run
"""


def create_reminder(
    task: str,
    when: str = "",
    now: Optional[datetime.datetime] = None,
    next_day_ok: bool = False,
) -> MacResult:
    """Create an Apple Reminders item in the default list.

    An unclear time is a question back (clarification=True, pending=what Sara is
    still waiting for), never a guess. A bare clock time that is already past
    today ("at 7 am" said at 3 pm) would land on tomorrow; Sara asks first
    unless the caller passes next_day_ok=True after the user confirmed."""
    if match_app("Reminders") is None:
        return MacResult(False, "Reminders is not on my allowed apps list", Risk.BLOCKED)
    task = (task or "").strip().strip(".!? ")[:MAX_REMINDER_TITLE]
    if not task:
        return MacResult(
            False,
            "What should I remind you about? For example 'remind me to call the dentist at 5 pm'.",
            Risk.SAFE,
        )
    now = now or datetime.datetime.now()
    when_res = parse_reminder_when(when, now)
    if when_res.error:
        return MacResult(
            False,
            when_res.error,
            Risk.SAFE,
            clarification=True,
            pending={"kind": "time", "task": task},
        )
    due = when_res.due
    if due is not None and when_res.bare_clock and due.date() != now.date() and not next_day_ok:
        question = f"That time has passed today. Remind you {_describe_due(due, now)} instead?"
        return MacResult(
            False,
            question,
            Risk.SAFE,
            clarification=True,
            pending={"kind": "confirm_tomorrow", "task": task, "when": when},
        )
    due_txt = f" due {due:%Y-%m-%d %H:%M}" if due else ""
    if dry_run():
        return MacResult(True, f"[dry run] would create reminder '{task}'{due_txt}", Risk.SAFE)
    # The task and the date parts travel as argv, never spliced into the script.
    argv = ["osascript", "-e", _REMIND_SCRIPT, task]
    if due is not None:
        argv += [str(due.year), str(due.month), str(due.day), str(due.hour), str(due.minute)]
    try:
        _run(argv)
    except (subprocess.SubprocessError, OSError) as exc:
        return MacResult(False, f"Could not create the reminder: {exc}", Risk.SAFE)
    when_txt = f" for {_describe_due(due, now)}" if due else ""
    return MacResult(True, f"Created reminder '{task}'{when_txt}", Risk.SAFE, executed=True)


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
        "REMIND": Risk.SAFE,
        "TYPE_TEXT": Risk.CONFIRM,
    }.get(action, Risk.BLOCKED)


def execute(
    action: str,
    *,
    app: str = "",
    title: str = "",
    text: str = "",
    mode: str = "set",
    task: str = "",
    when: str = "",
    next_day_ok: bool = False,
    confirm: Optional[Callable[[str], bool]] = None,
) -> MacResult:
    """Single entry point for the assistant: dispatch a Mac action by name."""
    if action == "OPEN_APP":
        return open_app(app)
    if action == "CREATE_NOTE":
        return create_note(title, text)
    if action == "EDIT_NOTE":
        return edit_note(title, text, mode)
    if action == "REMIND":
        return create_reminder(task, when, next_day_ok=next_day_ok)
    if action == "TYPE_TEXT":
        return type_text(text, confirm)
    return MacResult(False, f"Unknown Mac action '{action}'", Risk.BLOCKED)


# -- timing ---------------------------------------------------------------------
# Wrap `execute` so every action prints how long it took. The slow part of a
# Mac action is usually the app itself (first launch of Notes) or a macOS
# permission prompt waiting for a click, not Sara.
SLOW_ACTION_SECONDS = 3.0
_execute_untimed = execute


def execute(action: str, **kwargs) -> MacResult:  # noqa: F811
    import time

    t0 = time.monotonic()
    res = _execute_untimed(action, **kwargs)
    took = time.monotonic() - t0
    print(f"[timing] {action} took {took * 1000:.0f} ms", flush=True)
    if took >= SLOW_ACTION_SECONDS and not dry_run():
        print(
            "[timing] slow: the app is probably starting up, or macOS is waiting for you to allow "
            "automation (System Settings > Privacy & Security > Automation). Later runs are faster.",
            flush=True,
        )
    return res
