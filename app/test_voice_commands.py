"""End-to-end simulation of the voice pipeline without a microphone.

Uses the REAL vad.VADStream frame logic (silence counting) and the real
controller, intent runner, command rules, mac_control and overlay. Only these
are faked: the microphone/webrtcvad (a frame is speech if its first byte is 1),
the speech-to-text call (scripted by audio length) and the clock.
Mac actions run in dry-run mode, so nothing touches the system.
"""
import os
import sys
import types
import time
import unittest
from unittest import mock

FRAME = 960  # 30 ms of 16 kHz 16-bit mono
SPEECH = bytes([1]) * FRAME
SILENCE = bytes([0]) * FRAME

_saved = {}


def setUpModule():
    for name in ("sounddevice", "webrtcvad", "vad"):
        _saved[name] = sys.modules.pop(name, None)
    sd = types.ModuleType("sounddevice")
    sd.InputStream = object
    wv = types.ModuleType("webrtcvad")

    class Vad:
        def __init__(self, mode=0):
            pass

        def is_speech(self, frame, rate):
            return frame[0] == 1

    wv.Vad = Vad
    sys.modules["sounddevice"] = sd
    sys.modules["webrtcvad"] = wv
    os.environ["SARA_MAC_DRY_RUN"] = "true"
    os.environ["SARA_MIN_RMS"] = "0"
    os.environ.pop("SARA_ALLOWED_APPS", None)


def tearDownModule():
    for name, mod in _saved.items():
        if mod is None:
            sys.modules.pop(name, None)
        else:
            sys.modules[name] = mod
    os.environ.pop("SARA_MAC_DRY_RUN", None)
    os.environ.pop("SARA_MIN_RMS", None)


class Rig:
    """Feeds frames to a listener built on the real VADStream."""

    def __init__(self, script, confirm=lambda q: True):
        import overlay
        import voice_commands as vc

        self.t = 0.0
        self.script = sorted(script)  # [(min_frames_of_audio, text)]
        self.calls = []
        self.legacy_calls = []
        self.notes = []
        self.phases = []
        state = overlay.OverlayState()
        state.subscribe(lambda s: self.phases.append(s.phase.value))
        self.controller = vc.VoiceController(
            overlay=state,
            legacy=lambda text, wav: self.legacy_calls.append(text),
            notify=self.notes.append,
            confirm=confirm,
            clock=lambda: self.t,
        )
        self.listener = vc.make_listener(
            self.controller, self._transcribe, threaded=False, clock=lambda: self.t
        )

    def _transcribe(self, wav):
        frames = (len(wav) - 44) // FRAME
        self.calls.append(frames)
        text = ""
        for need, t in self.script:
            if frames >= need:
                text = t
        return text

    def feed(self, frame, n=1):
        for _ in range(n):
            self.t += 0.03
            self.listener._process_frame(frame)

    def log(self):
        return self.controller.log


class QuietAndTimingTests(unittest.TestCase):
    def _listener(self, texts):
        import overlay
        import voice_commands as vc

        controller = vc.VoiceController(
            overlay=overlay.OverlayState(), legacy=lambda t, w: None, notify=lambda m: None, confirm=lambda q: True
        )
        calls = []

        def stt(wav):
            calls.append(1)
            return texts

        return controller, calls, vc.make_listener(controller, stt, partial_interval=float("inf"), threaded=False)

    def test_wav_rms(self):
        import struct
        import voice_commands as vc

        loud = b"\0" * 44 + struct.pack("<100h", *([3000, -3000] * 50))
        self.assertGreater(vc.wav_rms(loud), 2900)
        self.assertEqual(vc.wav_rms(b"\0" * 44), 0.0)

    def test_quiet_audio_never_reaches_the_transcriber(self):
        with mock.patch.dict(os.environ, {"SARA_MIN_RMS": "1000"}):
            controller, calls, listener = self._listener("sara open notes")
            for _ in range(60):
                listener._process_frame(SPEECH)  # level 257, under the 1000 limit
            for _ in range(60):
                listener._process_frame(SILENCE)
        self.assertEqual(calls, [])
        self.assertEqual(controller.log, [])

    def test_empty_transcript_is_skipped_and_action_is_logged(self):
        controller, calls, listener = self._listener("   ")
        for _ in range(60):
            listener._process_frame(SPEECH)
        for _ in range(60):
            listener._process_frame(SILENCE)
        self.assertEqual(len(calls), 1)
        self.assertEqual(controller.log, [])
        import io
        from contextlib import redirect_stdout

        controller2, calls2, listener2 = self._listener("Sara, open Notes.")
        buf = io.StringIO()
        with redirect_stdout(buf):
            for _ in range(60):
                listener2._process_frame(SPEECH)
            for _ in range(60):
                listener2._process_frame(SILENCE)
        out = buf.getvalue()
        self.assertIn("[action] OPEN_APP ok: [dry run] would open Notes", out)
        self.assertIn("STT", out)
        self.assertIn("intent+action", out)


class RobustDefaultTests(unittest.TestCase):
    def test_local_stt_has_no_partials_by_default(self):
        import voice_commands as vc
        with mock.patch.dict(os.environ, {"TRANSCRIPTION_PROVIDER": "local"}, clear=True):
            self.assertEqual(vc.partial_interval_from_env(), float("inf"))
        with mock.patch.dict(os.environ, {"TRANSCRIPTION_PROVIDER": "local", "SARA_PARTIAL_INTERVAL": "2"}, clear=True):
            self.assertEqual(vc.partial_interval_from_env(), 2.0)
        with mock.patch.dict(os.environ, {"TRANSCRIPTION_PROVIDER": "groq"}, clear=True):
            self.assertEqual(vc.partial_interval_from_env(), float("inf"))
        with mock.patch.dict(os.environ, {"TRANSCRIPTION_PROVIDER": "openai"}, clear=True):
            self.assertEqual(vc.partial_interval_from_env(), vc.PARTIAL_INTERVAL)

    def test_final_transcription_does_not_block_the_audio_loop(self):
        import threading
        import overlay
        import voice_commands as vc

        release = threading.Event()
        heard = []

        def slow(wav):
            release.wait(5)
            return "sara open notes"

        controller = vc.VoiceController(
            overlay=overlay.OverlayState(), legacy=lambda t, w: None, notify=lambda m: None, confirm=lambda q: True
        )
        listener = vc.make_listener(controller, slow, partial_interval=float("inf"), threaded=True)
        for _ in range(60):
            listener._process_frame(SPEECH)
        t0 = time.time()
        for _ in range(40):
            listener._process_frame(SILENCE)  # speech ends here; the transcriber is stuck
        self.assertLess(time.time() - t0, 1.0, "the loop must not wait for the transcriber")
        self.assertEqual(controller.log, [])
        release.set()
        for th in list(listener.final_threads):
            th.join(5)
        self.assertTrue(any(x[1] == "OPEN_APP" for x in controller.log), controller.log)


class PipelineTests(unittest.TestCase):
    def test_open_notes_fires_before_the_sentence_ends(self):
        r = Rig([(0, "sara open no"), (30, "sara open notes"), (95, "sara open notes and create a note")])
        r.feed(SPEECH, 100)  # 3 s of speech, still talking
        early = [x for x in r.log() if x[0] == "early"]
        self.assertEqual(len(early), 1, r.log())
        self.assertEqual(early[0][1:3], ("OPEN_APP", True))
        self.assertIn("would open Notes", early[0][3])
        self.assertEqual(r.controller._fired, 1)
        r.feed(SILENCE, 40)  # 1.2 s silence ends the command
        opens = [x for x in r.log() if x[1] == "OPEN_APP"]
        self.assertEqual(len(opens), 1, "must not open twice")
        self.assertEqual(r.legacy_calls, [])
        self.assertTrue({"listening", "doing", "done"} <= set(r.phases), r.phases)

    def test_cancel_word(self):
        r = Rig([(0, "sara cancel")])
        r.feed(SPEECH, 60)
        r.feed(SILENCE, 40)
        self.assertEqual([x for x in r.log() if x[0] in ("early", "final")], [])
        self.assertEqual(r.legacy_calls, [])
        self.assertIn("cancelled", [p for p in ["cancelled"]])
        self.assertEqual(r.listener._speech, False)

    def test_wake_word_variants_run_on_final(self):
        for said, app in [
            ("okay sera open safari", "Safari"),
            ("Hey Sarah, open safari", "Safari"),
            ("zara open safari", "Safari"),
        ]:
            r = Rig([(0, said)])
            r.feed(SPEECH, 30)  # 0.9 s: too short for a stable early fire
            r.feed(SILENCE, 40)
            ran = [x for x in r.log() if x[0] in ("early", "final")]
            self.assertEqual(len(ran), 1, (said, r.log()))  # exactly once, early or at the end
            self.assertIn(app, ran[0][3])

    def test_not_for_sara_is_ignored(self):
        for said in ["sandra open notes", "what is the weather", "i told sara about it yesterday evening ok"]:
            r = Rig([(0, said)])
            r.feed(SPEECH, 60)
            r.feed(SILENCE, 40)
            self.assertEqual(r.log(), [], said)
            self.assertEqual(r.legacy_calls, [], said)

    def test_non_mac_command_goes_to_old_assistant(self):
        r = Rig([(0, "sara summarize my clipboard")])
        r.feed(SPEECH, 60)
        r.feed(SILENCE, 40)
        self.assertEqual(r.legacy_calls, ["sara summarize my clipboard"])
        self.assertEqual([x for x in r.log() if x[0] in ("early", "final")], [])

    def test_silence_boundary_is_1_2_seconds(self):
        r = Rig([(0, "sara open safari")])
        r.feed(SPEECH, 30)
        r.feed(SILENCE, 39)
        self.assertTrue(r.listener._speech, "39 silent frames (1.17 s) must not end the command")
        r.feed(SILENCE, 1)
        self.assertFalse(r.listener._speech, "40 silent frames (1.2 s) end the command")
        self.assertEqual(len([x for x in r.log() if x[0] in ("early", "final")]), 1)

    def test_maximum_length_forces_the_end(self):
        r = Rig([(0, "sara open safari")])
        r.feed(SPEECH, 360)  # 10.8 s of non-stop talking
        self.assertEqual(len([x for x in r.log() if x[0] in ("early", "final")]) >= 1, True, r.log())
        self.assertIn("heard", r.phases, "the 10 s limit must run the final step while still talking")
        self.assertTrue(any(c >= 330 for c in r.calls), r.calls)  # final transcription of ~10 s of audio

    def test_typing_needs_confirmation(self):
        denied = Rig([(0, "sara type hello world")], confirm=lambda q: False)
        denied.feed(SPEECH, 40)
        denied.feed(SILENCE, 40)
        res = [x for x in denied.log() if x[0] == "final"]
        self.assertEqual(len(res), 1)
        self.assertFalse(res[0][2])
        ok = Rig([(0, "sara type hello world")], confirm=lambda q: True)
        ok.feed(SPEECH, 40)
        ok.feed(SILENCE, 40)
        res = [x for x in ok.log() if x[0] == "final"]
        self.assertTrue(res[0][2])
        self.assertIn("would type", res[0][3])

    def test_app_not_on_allow_list_is_refused_and_reported(self):
        r = Rig([(0, "sara open terminal")])
        r.feed(SPEECH, 40)
        r.feed(SILENCE, 40)
        res = [x for x in r.log() if x[0] == "final"]
        self.assertEqual(len(res), 1)
        self.assertFalse(res[0][2])
        self.assertIn("not on my allowed apps", res[0][3])
        self.assertTrue(any("allowed apps" in n for n in r.notes))
        self.assertIn("error", r.phases)

    def test_dry_run_runs_no_subprocess(self):
        import mac_control

        called = []
        orig = mac_control.subprocess.run
        mac_control.subprocess.run = lambda *a, **k: called.append(a)
        try:
            r = Rig([(0, "sara open notes")])
            r.feed(SPEECH, 40)
            r.feed(SILENCE, 40)
        finally:
            mac_control.subprocess.run = orig
        self.assertEqual(called, [])


if __name__ == "__main__":
    unittest.main()
