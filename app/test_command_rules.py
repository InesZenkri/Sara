import os
import unittest

import command_rules as cr


class FakeClock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t


class WakeTests(unittest.TestCase):
    def test_variants(self):
        for s in ["sara open notes", "Hey Sara, open notes", "okay sarah open notes",
                  "ok sera open notes", "Zara open notes", "sarra open notes"]:
            self.assertEqual(cr.split_wake_word(s), "open notes", s)

    def test_only_wake_word(self):
        self.assertEqual(cr.split_wake_word("hey sara"), "")

    def test_not_at_start_or_not_sara(self):
        self.assertIsNone(cr.split_wake_word("I told my friend sara about it yesterday"))
        self.assertIsNone(cr.split_wake_word("open notes"))
        self.assertIsNone(cr.split_wake_word("sandra open notes"))
        self.assertIsNone(cr.split_wake_word(""))

    def test_cancel(self):
        for s in ["cancel", "Never mind", "sara stop", "annule", "forget it please"]:
            self.assertTrue(cr.is_cancel(s), s)
        for s in ["cancel my meeting note", "open notes", ""]:
            self.assertFalse(cr.is_cancel(s), s)

    def test_classify(self):
        self.assertEqual(cr.classify_utterance("sara"), ("wake", ""))
        self.assertEqual(cr.classify_utterance("sara cancel"), ("cancel", ""))
        self.assertEqual(cr.classify_utterance("hey sara open safari"), ("command", "open safari"))
        self.assertEqual(cr.classify_utterance("random chatter")[0], "ignore")


class EndpointerTests(unittest.TestCase):
    def make(self):
        c = FakeClock()
        return cr.Endpointer(clock=c), c

    def test_leading_silence_does_not_end(self):
        e, c = self.make()
        for _ in range(30):
            c.t += 0.5
            self.assertIsNone(e.update(False))

    def test_silence_after_speech(self):
        e, c = self.make()
        self.assertIsNone(e.update(True))
        c.t = 0.5
        self.assertIsNone(e.update(True))
        c.t = 1.0
        self.assertIsNone(e.update(False))
        c.t = 1.69
        self.assertIsNone(e.update(False))
        c.t = 1.71
        self.assertEqual(e.update(False), "silence")

    def test_timeout_when_talking_forever(self):
        e, c = self.make()
        e.update(True)
        c.t = 9.9
        self.assertIsNone(e.update(True))
        c.t = 10.0
        self.assertEqual(e.update(True), "timeout")

    def test_reset(self):
        e, c = self.make()
        e.update(True)
        e.reset()
        c.t = 5
        self.assertIsNone(e.update(False))

    def test_switch_off_by_default(self):
        os.environ.pop("SARA_VOICE_COMMANDS", None)
        self.assertFalse(cr.streaming_enabled())


if __name__ == "__main__":
    unittest.main()
