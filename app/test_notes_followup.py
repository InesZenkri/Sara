import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(__file__))

import intent
import mac_control as mc
import overlay
import voice_commands as vc

HER_SENTENCE = "Change that note title to Sara first trial and then as description put the terminal output"


class EditNoteParsing(unittest.TestCase):
    def classify(self, text):
        return intent.RuleBackend().classify(text)

    def test_her_real_sentence(self):
        g = self.classify(HER_SENTENCE)
        self.assertEqual(g.action, "EDIT_NOTE")
        self.assertEqual(g.args["title"], "Sara first trial")
        self.assertEqual(g.args["text"], "the terminal output")

    def test_other_phrasings(self):
        self.assertEqual(self.classify("rename the note to Groceries").args, {"title": "Groceries"})
        g = self.classify("add milk and eggs to that note")
        self.assertEqual((g.action, g.args), ("EDIT_NOTE", {"text": "milk and eggs", "mode": "append"}))
        g = self.classify("set the body to hello world")
        self.assertEqual((g.action, g.args["text"]), ("EDIT_NOTE", "hello world"))

    def test_create_and_open_unchanged(self):
        self.assertEqual(self.classify("create a note called buy milk").action, "CREATE_NOTE")
        self.assertEqual(self.classify("add a note about dinner").action, "CREATE_NOTE")
        self.assertEqual(self.classify("open notes").action, "OPEN_APP")
        self.assertEqual(self.classify("what is the weather").action, None)

    def test_node_is_heard_as_note(self):
        g = self.classify("Create a Node Node from Sara")
        self.assertEqual(g.action, "CREATE_NOTE")


class EditNoteAction(unittest.TestCase):
    def setUp(self):
        mc._last_note.clear()
        mc.set_terminal_source(None)
        os.environ.pop("SARA_MAC_DRY_RUN", None)

    def test_nothing_to_edit_when_notes_has_no_note(self):
        with mock.patch.object(mc.subprocess, "run", return_value=mock.Mock(stdout="")):
            res = mc.edit_note("x")
        self.assertFalse(res.ok)
        self.assertIn("could not find a note", res.message)

    def test_rename_and_terminal_body_use_argv_not_script(self):
        mc.set_terminal_source(lambda: "line one\nline <two>")
        with mock.patch.object(mc.subprocess, "run") as run:
            run.return_value = mock.Mock(stdout="x-coredata://note/p1\n")
            self.assertTrue(mc.create_note("Old", "body").ok)
            res = mc.edit_note("Sara first trial", "the terminal output")
        self.assertTrue(res.ok and res.executed, res.message)
        argv = run.call_args[0][0]
        self.assertEqual(argv[0], "osascript")
        self.assertEqual(argv[3], "x-coredata://note/p1")
        self.assertIn("<h1>Sara first trial</h1>", argv[4])
        self.assertIn("line one<br>line &lt;two&gt;", argv[4])
        self.assertNotIn("line one", argv[2])

    def test_append_keeps_old_body(self):
        os.environ["SARA_MAC_DRY_RUN"] = "true"
        try:
            mc.create_note("T", "first")
            res = mc.edit_note("", "second", "append")
            self.assertTrue(res.ok, res.message)
            self.assertEqual(mc._last_note["body"], "first\nsecond")
            self.assertEqual(mc._last_note["title"], "T")
        finally:
            os.environ.pop("SARA_MAC_DRY_RUN", None)

    def test_empty_terminal_is_reported_not_written(self):
        os.environ["SARA_MAC_DRY_RUN"] = "true"
        try:
            mc.create_note("T", "first")
            mc.set_terminal_source(lambda: "")
            res = mc.edit_note("", "the terminal output")
            self.assertFalse(res.ok)
        finally:
            os.environ.pop("SARA_MAC_DRY_RUN", None)

    def test_clipboard_body(self):
        os.environ["SARA_MAC_DRY_RUN"] = "true"
        try:
            mc.create_note("T", "")
            with mock.patch.object(mc, "read_clipboard", return_value="copied!"):
                res = mc.edit_note("", "the clipboard")
            self.assertTrue(res.ok)
            self.assertEqual(mc._last_note["body"], "copied!")
        finally:
            os.environ.pop("SARA_MAC_DRY_RUN", None)


class FollowUpWindow(unittest.TestCase):
    def setUp(self):
        os.environ["SARA_MAC_DRY_RUN"] = "true"
        mc._last_note.clear()
        self.t = 0.0
        self.c = vc.VoiceController(
            overlay=overlay.OverlayState(),
            legacy=None,
            notify=None,
            confirm=lambda q: True,
            clock=lambda: self.t,
            followup_seconds=10,
        )

    def tearDown(self):
        os.environ.pop("SARA_MAC_DRY_RUN", None)

    def test_without_a_previous_command_the_wake_word_is_required(self):
        self.assertEqual(self.c.on_final("open notes"), "ignored")

    def test_next_command_inside_the_window_needs_no_wake_word(self):
        self.assertEqual(self.c.on_final("Sara, create a note called test"), "mac")
        self.t = 8.0
        self.assertEqual(self.c.on_final(HER_SENTENCE), "mac")
        edits = [x for x in self.c.log if x[1] == "EDIT_NOTE"]
        self.assertEqual(len(edits), 1, self.c.log)
        self.assertTrue(edits[0][2], edits)

    def test_window_closes(self):
        self.c.on_final("Sara, create a note called test")
        self.t = 11.0
        self.assertEqual(self.c.on_final("rename the note to late"), "ignored")

    def test_ordinary_speech_in_the_window_is_ignored(self):
        self.c.on_final("Sara, open notes")
        self.t = 3.0
        for s in ["thanks everyone", "what a lovely day", "I will start the car"]:
            self.assertEqual(self.c.on_final(s), "ignored", s)

    def test_window_can_be_turned_off(self):
        c = vc.VoiceController(overlay=overlay.OverlayState(), clock=lambda: self.t, followup_seconds=0)
        c.on_final("Sara, open notes")
        self.assertEqual(c.on_final("open safari"), "ignored")


if __name__ == "__main__":
    unittest.main()
