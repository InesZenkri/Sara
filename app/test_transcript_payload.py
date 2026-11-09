"""Wake recognition must not rewrite the text sent to Mac actions."""
import unittest

import command_rules as cr
import mac_control
from voice_commands import VoiceController


class TranscriptPayloadTests(unittest.TestCase):
    def test_wake_variants_preserve_original_command(self):
        command = "type Hello, Ines! Meeting at 14:30 on 12/11."
        for wake in ("Sara, ", "Hey SARAH: ", "okay sera - ", "Zara! "):
            with self.subTest(wake=wake):
                self.assertEqual(cr.split_wake_word(wake + command), command)
                self.assertEqual(cr.classify_utterance(wake + command), ("command", command))

    def test_unicode_payload(self):
        command = "type Grüße, Inès! موعد 14:30 🌸"
        self.assertEqual(cr.split_wake_word("Sara, " + command), command)

    def test_wake_only_and_background_chatter(self):
        self.assertEqual(cr.classify_utterance("Hey SARA!"), ("wake", ""))
        self.assertEqual(cr.classify_utterance("I told Sara: type 123."), ("ignore", ""))

    def test_cancel_and_quit_still_use_normalized_checks(self):
        for text in ("SARA, CANCEL!", "Hey Sarah, Never Mind."):
            self.assertEqual(cr.classify_utterance(text), ("cancel", ""))
        self.assertEqual(cr.classify_utterance("Sara, Thank You!"), ("quit", ""))
        # A stop phrase within dictated content is not a cancellation.
        self.assertEqual(cr.classify_utterance("Sara, type Cancel 2027!")[0], "command")

    def dispatch(self, text, partial=False):
        calls = []

        def execute(action, **kwargs):
            calls.append((action, kwargs))
            return mac_control.MacResult(True, "mock action")

        controller = VoiceController(execute=execute, confirm=lambda _: False, followup_seconds=0)
        if partial:
            controller.on_partial(text)
            self.assertEqual(calls, [])  # dictation must still wait for the final transcript
        result = controller.on_final(text)
        return result, calls

    def test_typing_receives_case_numbers_and_punctuation(self):
        payload = "Hello, Ines! Meeting at 14:30 on 12/11."
        for partial in (False, True):
            result, calls = self.dispatch("Sara, type " + payload, partial=partial)
            self.assertEqual(result, "mac")
            self.assertEqual(len(calls), 1)
            self.assertEqual(calls[0][0], "TYPE_TEXT")
            self.assertEqual(calls[0][1]["text"], payload)
            self.assertIsNotNone(calls[0][1]["confirm"])

    def test_notes_keep_payload_and_existing_title_rules(self):
        payload = "EPFL 2027: deadline 15.11."
        result, calls = self.dispatch("Sara, create a note called " + payload)
        self.assertEqual(result, "mac")
        self.assertEqual(calls[0][0], "CREATE_NOTE")
        # Existing note extraction trims the sentence-final period.
        self.assertEqual(calls[0][1]["title"], payload.rstrip("."))
        self.assertEqual(calls[0][1]["text"], payload.rstrip("."))
        long_title = "EPFL 2027 " + "A" * 80
        _, calls = self.dispatch("Sara, create a note called " + long_title)
        self.assertEqual(calls[0][1]["title"], long_title[:60])
        self.assertEqual(calls[0][1]["text"], long_title)

    def test_unicode_reaches_typing_action(self):
        payload = "Grüße, Inès! موعد 14:30 🌸"
        _, calls = self.dispatch("Sara, type " + payload)
        self.assertEqual(calls[0][1]["text"], payload)

    def test_cancel_does_not_dispatch(self):
        result, calls = self.dispatch("Sara, CANCEL!")
        self.assertEqual((result, calls), ("cancelled", []))

    def test_unlisted_app_still_does_not_dispatch(self):
        result, calls = self.dispatch("Sara, open Terminal")
        self.assertEqual(calls, [])
        self.assertNotEqual(result, "mac")


if __name__ == "__main__":
    unittest.main()
