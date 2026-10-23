"""Start Sara with voice commands (partial transcripts, early Mac actions, overlay).

    cd app
    SARA_VOICE_COMMANDS=true SARA_MAC_DRY_RUN=true python run_voice.py   # first run: nothing touches your Mac
    SARA_VOICE_COMMANDS=true python run_voice.py                         # real

Optional: SARA_OVERLAY=true for the floating overlay.
`python main.py` still works exactly as before.
"""
import sys
import threading
import time

import command_rules

if not command_rules.streaming_enabled():
    print("Voice commands are off. Set SARA_VOICE_COMMANDS=true to start them.")
    sys.exit(1)

import mac_control
from main import hotword, notify_user, on_hotword_detected  # importing main does not start its listener
from overlay import make_overlay
from voice_commands import VoiceController, make_listener, make_transcriber


import collections  # noqa: E402


class _Tee:
    """Copy everything printed into a buffer so "put the terminal output in the note" works."""

    def __init__(self, stream, chunks):
        self._stream, self._chunks = stream, chunks

    def write(self, text):
        self._chunks.append(text)
        return self._stream.write(text)

    def __getattr__(self, name):
        return getattr(self._stream, name)


_chunks = collections.deque(maxlen=500)
sys.stdout = _Tee(sys.stdout, _chunks)
sys.stderr = _Tee(sys.stderr, _chunks)
mac_control.set_terminal_source(lambda: "".join(_chunks)[-4000:])

if mac_control.dry_run():
    print("DRY RUN: Mac actions are only logged (SARA_MAC_DRY_RUN=true).")

transcribe = make_transcriber()
if transcribe is None:
    sys.exit(1)

state, gui = make_overlay()
stop_event = threading.Event()


def say_goodbye_and_stop() -> None:
    """Called after "Sara, bye": stop the microphone, say a short goodbye, end the program."""
    try:
        listener.stop()
    except Exception:
        pass
    try:
        from main import speak_response

        speak_response("Bye!")  # does nothing if there is no voice
    except Exception:
        pass
    print("Bye.", flush=True)
    if gui is not None:
        gui.request_stop()
    stop_event.set()


controller = VoiceController(
    on_quit=say_goodbye_and_stop,
    overlay=state,
    legacy=on_hotword_detected,  # anything that is not a Mac command goes to the old clipboard assistant
    notify=notify_user,
    async_notify=True,
    hotword=hotword,
)
if gui is None:  # no window: show the same status in the terminal
    state.subscribe(lambda s: print(f"[overlay] {s.title} {s.text}".rstrip(), flush=True) if s.visible else None)
try:
    import sounddevice as sd

    print(f"Microphone: {sd.query_devices(kind='input')['name']}", flush=True)
except Exception as exc:
    print(f"Could not read the default microphone: {exc}", flush=True)
listener = make_listener(controller, transcribe)
listener.start()
print(f"Say '{hotword}, open notes'. Say '{hotword}, cancel' to cancel. Say '{hotword}, bye' (or 'that was it', 'thanks') to stop. Ctrl+C also works.", flush=True)

try:
    if gui is not None:
        gui.run()  # Tk must own the main thread
    else:
        while not stop_event.wait(0.5):
            pass
except KeyboardInterrupt:
    pass
finally:
    listener.stop()
