import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(__file__))

import echo_guard
import intent
import mac_control as mc


class FakeClock:
    t = 0.0

    def __call__(self):
        return self.t


class EchoGuardTests(unittest.TestCase):
    def setUp(self):
        self.c = FakeClock()
        self.g = echo_guard.EchoGuard(clock=self.c, tail=1.0)

    def test_muted_while_speaking_and_for_the_tail(self):
        self.assertFalse(self.g.muted())
        self.g.begin("Created note")
        self.assertTrue(self.g.muted())
        self.c.t = 5.0
        self.assertTrue(self.g.muted())  # still speaking
        self.g.end()
        self.c.t = 5.5
        self.assertTrue(self.g.muted())  # tail
        self.c.t = 6.5
        self.assertFalse(self.g.muted())

    def test_her_real_echoes_are_dropped(self):
        self.g.begin("Note to Sara is not on my allowed apps list")
        self.g.end()
        self.g.begin("I have not made a note yet, so there is nothing to edit")
        self.g.end()
        self.assertTrue(self.g.is_echo("Note to Sara is not on my allowed apps list."))
        self.assertTrue(self.g.is_echo("I have not made a note yet, so there is nothing"))

    def test_real_commands_are_not_echo(self):
        self.g.begin("Created note 'note from Sara'")
        self.g.end()
        self.assertFalse(self.g.is_echo("Sara open notes"))
        self.assertFalse(self.g.is_echo("Sara, change the note title to groceries"))

    def test_old_speech_is_forgotten(self):
        self.g.begin("Opened Notes now")
        self.g.end()
        self.c.t = 500.0
        self.assertFalse(self.g.is_echo("Opened Notes now"))


class ParsingVariants(unittest.TestCase):
    def classify(self, s):
        return intent.RuleBackend().classify(s)

    def test_her_whisper_variants(self):
        g = self.classify("Sara changed the name of the note to a open note to Sara and the date of today")
        self.assertEqual(g.action, "EDIT_NOTE")
        self.assertEqual(g.args["title"], "note to Sara and the date of today")
        for s in ["Sara changed the note name to Sara and the date of today",
                  "Sara changed the notes name to the date of today",
                  "Sara change the note's name to groceries",
                  "Sara changes the title of the note to groceries"]:
            self.assertEqual(self.classify(s).action, "EDIT_NOTE", s)

    def test_open_only_for_allowed_apps(self):
        self.assertEqual(self.classify("open note to sara").action, None)
        self.assertEqual(self.classify("open terminal").action, None)
        self.assertEqual(self.classify("open notes").action, "OPEN_APP")
        self.assertEqual(mc.match_app("note"), "Notes")
        self.assertIsNone(mc.match_app("terminal"))

    def test_date_in_title(self):
        import datetime

        today = datetime.date.today().isoformat()
        self.assertEqual(mc.resolve_title("the date of today"), today)
        self.assertEqual(mc.resolve_title("Sara and the date of today"), f"Sara {today}")
        with mock.patch.dict(os.environ, {"SARA_DATE_FORMAT": "%d.%m.%Y"}):
            self.assertEqual(mc.resolve_title("today's date"), datetime.date.today().strftime("%d.%m.%Y"))
        self.assertEqual(mc.resolve_title("groceries"), "groceries")


class EditRecentNote(unittest.TestCase):
    def setUp(self):
        mc._last_note.clear()
        os.environ.pop("SARA_MAC_DRY_RUN", None)

    def test_falls_back_to_most_recent_note_and_keeps_its_html(self):
        found = "x-coredata://n9\x1fOld title\x1f<div><h1>Old title</h1></div><div>keep me</div>"
        with mock.patch.object(mc.subprocess, "run") as run:
            run.side_effect = [mock.Mock(stdout=found + "\n"), mock.Mock(stdout="")]
            res = mc.edit_note("New title")
        self.assertTrue(res.ok and res.executed, res.message)
        argv = run.call_args_list[1][0][0]
        self.assertEqual(argv[3], "x-coredata://n9")
        self.assertIn("<h1>New title</h1>", argv[4])
        self.assertIn("keep me", argv[4])

    def test_dry_run_does_not_touch_notes(self):
        os.environ["SARA_MAC_DRY_RUN"] = "true"
        try:
            with mock.patch.object(mc.subprocess, "run") as run:
                res = mc.edit_note("X")
            self.assertTrue(res.ok)
            run.assert_not_called()
        finally:
            os.environ.pop("SARA_MAC_DRY_RUN", None)


if __name__ == "__main__":
    unittest.main()
