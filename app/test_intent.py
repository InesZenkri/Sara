import json
import unittest

from intent import (
    IntentGuess, JevBackend, LLMBackend, RuleBackend, StreamingIntentRunner, extract_args,
)


class Clock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        self.t += 1.0
        return self.t


def run(partials, final):
    fired = []
    r = StreamingIntentRunner(RuleBackend(), lambda g, early: fired.append((g.action, g.args, early)), clock=Clock())
    for p in partials:
        r.feed(p)
    r.finish(final)
    return fired


class RuleTests(unittest.TestCase):
    def test_args(self):
        self.assertEqual(extract_args("OPEN_APP", "please open the notes app and create"), {"app": "notes"})
        self.assertEqual(extract_args("CREATE_NOTE", "create a note called buy milk")["text"], "buy milk")
        self.assertEqual(extract_args("TYPE_TEXT", "type hello world")["text"], "hello world")

    def test_prefix_of_app_name_has_low_confidence(self):
        b = RuleBackend()
        self.assertLess(b.classify("open no").confidence, 0.8)
        self.assertGreaterEqual(b.classify("open notes").confidence, 0.8)

    def test_not_a_command(self):
        self.assertIsNone(RuleBackend().classify("what's the weather").action)


class RunnerTests(unittest.TestCase):
    def test_open_app_fires_early_once_and_final_is_deduped(self):
        fired = run(
            ["open", "open no", "open notes", "open notes and", "open notes and create a"],
            "open notes and create a note called milk",
        )
        self.assertEqual(fired[0], ("OPEN_APP", {"app": "notes"}, True))
        # final transcript = open notes (same args) -> not run twice;
        # the note part is a different action and runs at the end
        self.assertEqual([f[0] for f in fired], ["OPEN_APP", "CREATE_NOTE"])
        self.assertFalse(fired[1][2])

    def test_needs_stability_not_a_single_partial(self):
        fired = []
        r = StreamingIntentRunner(RuleBackend(), lambda g, e: fired.append(g), clock=Clock())
        r.feed("open notes")
        self.assertEqual(fired, [])
        r.feed("open notes")
        self.assertEqual(len(fired), 1)

    def test_typing_and_notes_never_fire_early(self):
        fired = run(["type hello", "type hello", "type hello world"], "type hello world")
        self.assertEqual(fired, [("TYPE_TEXT", {"text": "hello world"}, False)])
        fired = run(["create a note called milk"] * 3, "create a note called milk")
        self.assertTrue(all(not f[2] for f in fired))

    def test_throttle(self):
        calls = []

        class B:
            def classify(self, t):
                calls.append(t)
                return IntentGuess()

        now = [0.0]
        r = StreamingIntentRunner(B(), lambda *a: None, min_interval=0.5, clock=lambda: now[0])
        r.feed("a"); now[0] = 0.1; r.feed("ab"); now[0] = 0.6; r.feed("abc")
        self.assertEqual(calls, ["a", "abc"])

    def test_reset_between_utterances(self):
        fired = run(["open notes", "open notes"], "open notes")
        self.assertEqual(len(fired), 1)


class BackendTests(unittest.TestCase):
    def test_llm_backend_uses_code_args_and_survives_errors(self):
        b = LLMBackend(lambda p: {"action": "OPEN_APP", "confidence": 0.95, "complete": False})
        g = b.classify("open safari")
        self.assertEqual((g.action, g.args), ("OPEN_APP", {"app": "safari"}))
        bad = LLMBackend(lambda p: 1 / 0)
        self.assertIsNone(bad.classify("open safari").action)
        junk = LLMBackend(lambda p: {"action": "rm -rf /"})
        self.assertIsNone(junk.classify("open safari").action)

    def test_jev_backend_parsing_and_failure(self):
        j = JevBackend(api_key="k")
        j._post = lambda body: {"answers": {
            "action": {"type": "choice", "probabilities": {"OPEN_APP": 0.93, "NONE": 0.07}},
            "complete": {"type": "noul", "noul": 0.1},
        }}
        g = j.classify("open safari")
        self.assertEqual((g.action, g.confidence, g.complete), ("OPEN_APP", 0.93, False))
        body = j._body("open safari")
        self.assertEqual(body["questions"]["action"]["type"], "choice")
        json.dumps(body)
        j._post = lambda body: 1 / 0
        self.assertIsNone(j.classify("open safari").action)

    def test_jev_needs_key(self):
        import os
        os.environ.pop("TYPESAFE_API_KEY", None)
        with self.assertRaises(RuntimeError):
            JevBackend()


if __name__ == "__main__":
    unittest.main()
