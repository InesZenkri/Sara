import os
import unittest
from unittest import mock

import mac_control as mc


class MacControlTests(unittest.TestCase):
    def setUp(self):
        os.environ.pop("SARA_ALLOWED_APPS", None)
        os.environ.pop("SARA_MAC_DRY_RUN", None)

    def test_match_app_is_case_insensitive_and_strips_app(self):
        self.assertEqual(mc.match_app("notes app"), "Notes")
        self.assertEqual(mc.match_app("SAFARI"), "Safari")
        self.assertIsNone(mc.match_app("Terminal"))

    def test_open_blocked_app_never_runs(self):
        with mock.patch.object(mc.subprocess, "run") as run:
            res = mc.open_app("Terminal")
        self.assertFalse(res.ok)
        self.assertEqual(res.risk, mc.Risk.BLOCKED)
        run.assert_not_called()

    def test_open_allowed_app_uses_argv_not_shell(self):
        with mock.patch.object(mc.subprocess, "run") as run:
            res = mc.open_app("notes")
        self.assertTrue(res.ok and res.executed)
        self.assertEqual(run.call_args[0][0], ["open", "-a", "Notes"])

    def test_note_text_is_passed_as_argument_not_script(self):
        evil = 'x" & (do shell script "rm -rf ~") & "'
        with mock.patch.object(mc.subprocess, "run") as run:
            mc.create_note("t", evil)
        argv = run.call_args[0][0]
        self.assertNotIn("rm -rf", argv[2])  # the script itself
        self.assertIn("rm -rf", argv[4])  # only in the data argument

    def test_type_requires_confirmation(self):
        with mock.patch.object(mc.subprocess, "run") as run:
            self.assertFalse(mc.type_text("hello").ok)
            self.assertFalse(mc.type_text("hello", confirm=lambda _: False).ok)
            run.assert_not_called()
            self.assertTrue(mc.type_text("hello", confirm=lambda _: True).ok)
            run.assert_called_once()

    def test_dry_run_does_not_touch_system(self):
        os.environ["SARA_MAC_DRY_RUN"] = "true"
        with mock.patch.object(mc.subprocess, "run") as run:
            self.assertTrue(mc.open_app("Notes").ok)
            self.assertTrue(mc.create_note("a", "b").ok)
            self.assertTrue(mc.type_text("c", confirm=lambda _: True).ok)
        run.assert_not_called()

    def test_custom_allow_list(self):
        os.environ["SARA_ALLOWED_APPS"] = "Safari"
        self.assertIsNone(mc.match_app("Notes"))
        self.assertEqual(mc.match_app("safari"), "Safari")

    def test_unknown_action_is_blocked(self):
        self.assertEqual(mc.execute("DELETE_EVERYTHING").risk, mc.Risk.BLOCKED)
        self.assertEqual(mc.risk_of("DELETE_EVERYTHING"), mc.Risk.BLOCKED)


if __name__ == "__main__":
    unittest.main()
