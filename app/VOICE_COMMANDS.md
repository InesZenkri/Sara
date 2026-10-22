# Voice commands: 3-step Mac test

Off by default. `python main.py` still works as before. These steps use `run_voice.py`.

Say "sara, open notes" (or "hey sara ...", "ok sara ..."). Say "sara, cancel" to cancel before the action runs.
Anything that is not a Mac command (open app, create note, type) goes to the old clipboard assistant.

## Step 1: permissions (once)

1. `cd app && pip install -r requirements.txt` (needs a working microphone package: `sounddevice`, `webrtcvad`).
2. System Settings > Privacy & Security > **Microphone**: allow your terminal app.
3. System Settings > Privacy & Security > **Accessibility**: allow your terminal app (needed only for typing).
4. The first time Sara creates a note, macOS asks to let your terminal control **Notes** (Automation). Allow.
5. Optional overlay needs Tk: `python -c "import tkinter"` should print nothing. If it fails, `brew install python-tk`.

## Step 2: dry run (nothing touches your Mac)

```bash
cd app
SARA_VOICE_COMMANDS=true SARA_MAC_DRY_RUN=true python run_voice.py
```

Try: "sara, open notes", "sara, open safari", "sara, open terminal" (must be refused), "sara, create a note called milk",
"sara, cancel", and something random without "sara" (must be ignored). Expect log lines like `[dry run] would open Notes`.
Add `SARA_OVERLAY=true` to see the small Listening / Heard / Doing / Done pill at the top of the screen.

## Step 3: real run

```bash
SARA_VOICE_COMMANDS=true python run_voice.py
```

Try "sara, open notes" (Notes should open while you are still finishing the sentence), "sara, create a note called test",
then "sara, type hello" (a dialog asks first; Cancel must stop it).
Stop with Ctrl+C. Send back anything odd: the terminal output and what you said.

Settings: `SARA_ALLOWED_APPS="Notes,Safari"` changes the allow-list. `SARA_INTENT_BACKEND` is not used here (rules only).
Cost: while you speak, audio so far is sent to the speech-to-text API about every 0.8 s.
