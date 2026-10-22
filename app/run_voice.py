"""Start Sara with voice commands (partial transcripts, early Mac actions, overlay).

    cd app
    SARA_VOICE_COMMANDS=true SARA_MAC_DRY_RUN=true python run_voice.py   # first run: nothing touches your Mac
    SARA_VOICE_COMMANDS=true python run_voice.py                         # real

Optional: SARA_OVERLAY=true for the floating overlay.
`python main.py` still works exactly as before.
"""
import sys
import time

import command_rules

if not command_rules.streaming_enabled():
    print("Voice commands are off. Set SARA_VOICE_COMMANDS=true to start them.")
    sys.exit(1)

import mac_control
from main import hotword, notify_user, on_hotword_detected  # importing main does not start its listener
from overlay import make_overlay
from voice_commands import VoiceController, make_listener, make_transcriber

if mac_control.dry_run():
    print("DRY RUN: Mac actions are only logged (SARA_MAC_DRY_RUN=true).")

transcribe = make_transcriber()
if transcribe is None:
    sys.exit(1)

state, gui = make_overlay()
controller = VoiceController(
    overlay=state,
    legacy=on_hotword_detected,  # anything that is not a Mac command goes to the old clipboard assistant
    notify=notify_user,
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
print(f"Say '{hotword}, open notes'. Say '{hotword}, cancel' to cancel. Ctrl+C to stop.", flush=True)

try:
    if gui is not None:
        gui.run()  # Tk must own the main thread
    else:
        while True:
            time.sleep(1)
except KeyboardInterrupt:
    pass
finally:
    listener.stop()
