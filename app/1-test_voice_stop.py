import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(__file__))

import command_rules as cr
import overlay
import mac_control
import voice_commands as vc


class QuitPhraseTests(unittest.TestCase):
    def test_goodbyes_after_the_wake_word_quit(self):
        for s in ["Sara, bye", "sara goodbye", "Hey Sara, that was it", "sara that's it", "Sara, thanks",
                  "sara thank you", "sara thx", "sarah stop listening", "okay sara, that will be all",
                  "Sara, thanks.", "sara that\u2019s it", "zara bye bye"]:
            self.assertEqual(cr.classify_utterance(s), ("quit", ""), s)

    def test_needs_the_wake_word(self):
        for s in ["bye", "thanks", "that was it", "thank you so much for that"]:
            self.assertEqual(cr.classify_utterance(s)[0], "ignore", s)

    def test_thanks_inside_a_command_does_not_quit(self):
        for s in ["sara open notes thanks", "sara create a note saying thanks for dinner",
                  "sara thanks for opening notes", "sara type bye", "sara open safari and say goodbye"]:
            self.assertNotEqual(cr.classify_utterance(s)[0], "quit", s)

    def test_cancel_still_works(self):
        self.assertEqual(cr.classify_utterance("sara stop"), ("cancel", ""))
        self.assertEqual(cr.classify_utterance("sara cancel"), ("cancel", ""))


class ControllerQuitTests(unittest.TestCase):
    def make(self, on_quit):
        executed = []
        c = vc.VoiceController(
            overlay=overlay.OverlayState(),
            execute=lambda *a, **k: executed.append(a) or mac_control.MacResult(True, 'ok', mac_control.Risk.SAFE),
            confirm=lambda q: True,
            on_quit=on_quit,
        )
        return c, executed

    def test_final_goodbye_calls_on_quit_once(self):
        calls = []
        c, executed = self.make(lambda: calls.append(1))
        self.assertEqual(c.on_final("Sara, that was it"), "quit")
        self.assertEqual(calls, [1])
        self.assertEqual(executed, [])
        self.assertIn(("quit",), c.log)

    def test_partial_goodbye_does_not_quit(self):
        calls = []
        c, _ = self.make(lambda: calls.append(1))
        c.on_partial("sara thanks")
        self.assertEqual(calls, [])

    def test_normal_sentence_with_thanks_is_not_a_quit(self):
        calls = []
        c, _ = self.make(lambda: calls.append(1))
        self.assertNotEqual(c.on_final("Sara, open notes thanks"), "quit")
        self.assertEqual(c.on_final("thanks everyone"), "ignored")
        self.assertEqual(calls, [])

    def test_broken_quit_handler_does_not_raise(self):
        def boom():
            raise RuntimeError("x")

        c, _ = self.make(boom)
        self.assertEqual(c.on_final("sara bye"), "quit")


if __name__ == "__main__":
    unittest.main()
