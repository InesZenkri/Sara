"""Voice reminders: rule parsing, time resolution, clarification flow, execution.

Runs anywhere: no microphone, no Mac, no API key. Time parsing is frozen at
NOW so "today"/"tomorrow" outcomes are deterministic.
"""
import datetime
import os
import types
import unittest
from unittest import mock

import intent
import mac_control as mc
from overlay import OverlayState, Phase
from voice_commands import VoiceController

NOW = datetime.datetime(2026, 11, 11, 15, 0)  # a Wednesday, 3 pm


def freeze_now():
    """Patch mac_control's clock so "today"/"tomorrow" resolve against NOW."""

    class FakeDateTime(datetime.datetime):
        @classmethod
        def now(cls, tz=None):
            return NOW

    fake_module = types.SimpleNamespace(
        datetime=FakeDateTime, timedelta=datetime.timedelta, date=datetime.date
    )
    return mock.patch.object(mc, "datetime", fake_module)


class TimeParsingTests(unittest.TestCase):
    def test_relative_minutes_and_hours(self):
        self.assertEqual(mc.parse_reminder_when("in 20 minutes", NOW).due, datetime.datetime(2026, 11, 11, 15, 20))
        self.assertEqual(mc.parse_reminder_when("in 2 hours", NOW).due, datetime.datetime(2026, 11, 11, 17, 0))
        self.assertFalse(mc.parse_reminder_when("in 20 minutes", NOW).bare_clock)

    def test_at_24h_and_ampm(self):
        self.assertEqual(mc.parse_reminder_when("at 17:30", NOW).due, datetime.datetime(2026, 11, 11, 17, 30))
        self.assertEqual(mc.parse_reminder_when("at 5 pm", NOW).due, datetime.datetime(2026, 11, 11, 17, 0))
        # 7 am is already past at 3 pm, so it resolves to tomorrow - and is marked
        # as a bare clock time so create_reminder confirms that before creating.
        res = mc.parse_reminder_when("at 7 am", NOW)
        self.assertEqual(res.due, datetime.datetime(2026, 11, 12, 7, 0))
        self.assertTrue(res.bare_clock)
        self.assertEqual(mc.parse_reminder_when("at five pm", NOW).due, datetime.datetime(2026, 11, 11, 17, 0))

    def test_today_tomorrow_noon_midnight(self):
        self.assertEqual(mc.parse_reminder_when("tomorrow at 9 am", NOW).due, datetime.datetime(2026, 11, 12, 9, 0))
        # An explicit "today" stays today, even if that time is already past.
        self.assertEqual(mc.parse_reminder_when("today at 8:00", NOW).due, datetime.datetime(2026, 11, 11, 8, 0))
        self.assertEqual(mc.parse_reminder_when("at noon", NOW).due, datetime.datetime(2026, 11, 12, 12, 0))
        self.assertEqual(mc.parse_reminder_when("at midnight", NOW).due, datetime.datetime(2026, 11, 12, 0, 0))

    def test_explicit_day_is_not_bare_clock(self):
        self.assertFalse(mc.parse_reminder_when("tomorrow at 9 am", NOW).bare_clock)
        self.assertFalse(mc.parse_reminder_when("today at 8:00", NOW).bare_clock)

    def test_unclear_times_ask_instead_of_guessing(self):
        res = mc.parse_reminder_when("at 5", NOW)
        self.assertIsNone(res.due)
        self.assertIn("5 am or 5 pm", res.error)
        res = mc.parse_reminder_when("tomorrow", NOW)
        self.assertIsNone(res.due)
        self.assertIn("what time", res.error.lower())
        for vague in ("tonight", "this evening", "in a bit"):
            res = mc.parse_reminder_when(vague, NOW)
            self.assertIsNone(res.due)
            self.assertIn("exact time", res.error)

    def test_no_time_means_no_due_date(self):
        self.assertEqual(mc.parse_reminder_when("", NOW), mc.When(None, None))


class IntentTests(unittest.TestCase):
    def test_rule_action(self):
        self.assertEqual(intent.rule_action("remind me to call the dentist at 5 pm"), "REMIND")
        self.assertEqual(intent.rule_action("set a reminder to buy milk in 20 minutes"), "REMIND")
        self.assertEqual(intent.rule_action("Remind me to water the plants"), "REMIND")

    def test_extract_task_and_time(self):
        args = intent.extract_args("REMIND", "remind me to call the dentist at 5 pm")
        self.assertEqual(args, {"task": "call the dentist", "when": "at 5 pm"})
        args = intent.extract_args("REMIND", "remind me in 20 minutes to call the dentist")
        self.assertEqual(args, {"task": "call the dentist", "when": "in 20 minutes"})
        args = intent.extract_args("REMIND", "remind me about the team meeting tomorrow at 9 am")
        self.assertEqual(args, {"task": "the team meeting", "when": "tomorrow at 9 am"})
        args = intent.extract_args("REMIND", "remind me to buy milk")
        self.assertEqual(args, {"task": "buy milk"})

    def test_task_keeps_original_case_and_punctuation(self):
        args = intent.extract_args("REMIND", "remind me to call Dr. Amari at 5 pm")
        self.assertEqual(args["task"], "call Dr. Amari")

    def test_backend_confidence(self):
        g = intent.RuleBackend().classify("remind me to call the dentist at 5 pm")
        self.assertEqual(g.action, "REMIND")
        self.assertGreaterEqual(g.confidence, 0.8)
        g = intent.RuleBackend().classify("remind me")
        self.assertEqual(g.action, "REMIND")
        self.assertLess(g.confidence, 0.8)

    def test_reminders_never_fire_early(self):
        # A half-heard time must not create a reminder; REMIND waits for the final transcript.
        self.assertNotIn("REMIND", intent.EARLY_SAFE_ACTIONS)


class CreateReminderTests(unittest.TestCase):
    def setUp(self):
        os.environ.pop("SARA_ALLOWED_APPS", None)
        os.environ.pop("SARA_MAC_DRY_RUN", None)

    def tearDown(self):
        os.environ.pop("SARA_MAC_DRY_RUN", None)
        os.environ.pop("SARA_ALLOWED_APPS", None)

    def test_past_bare_clock_time_needs_confirmation(self):
        with mock.patch.object(mc.subprocess, "run") as run:
            res = mc.create_reminder("call the dentist", "at 7 am", now=NOW)
        self.assertFalse(res.ok)
        self.assertTrue(res.clarification)
        self.assertEqual(res.pending["kind"], "confirm_tomorrow")
        self.assertIn("tomorrow at 7:00 AM", res.message)
        run.assert_not_called()

    def test_confirmed_next_day_creates(self):
        os.environ["SARA_MAC_DRY_RUN"] = "true"
        res = mc.create_reminder("call the dentist", "at 7 am", now=NOW, next_day_ok=True)
        self.assertTrue(res.ok)
        self.assertIn("2026-11-12 07:00", res.message)

    def test_unambiguous_commands_create_without_confirmation(self):
        os.environ["SARA_MAC_DRY_RUN"] = "true"
        with mock.patch.object(mc.subprocess, "run") as run:
            future_today = mc.create_reminder("a", "at 5 pm", now=NOW)
            explicit_tomorrow = mc.create_reminder("b", "tomorrow at 7 am", now=NOW)
            explicit_today = mc.create_reminder("c", "today at 8:00", now=NOW)
            duration = mc.create_reminder("d", "in 20 minutes", now=NOW)
        self.assertTrue(future_today.ok and "2026-11-11 17:00" in future_today.message)
        self.assertTrue(explicit_tomorrow.ok and "2026-11-12 07:00" in explicit_tomorrow.message)
        self.assertTrue(explicit_today.ok and "2026-11-11 08:00" in explicit_today.message)
        self.assertTrue(duration.ok and "15:20" in duration.message)
        run.assert_not_called()  # dry run never touches the Mac

    def test_unclear_time_is_a_pending_clarification(self):
        with mock.patch.object(mc.subprocess, "run") as run:
            res = mc.create_reminder("call the dentist", "at 5", now=NOW)
        self.assertFalse(res.ok)
        self.assertTrue(res.clarification)
        self.assertEqual(res.pending, {"kind": "time", "task": "call the dentist"})
        self.assertIn("5 am or 5 pm", res.message)
        run.assert_not_called()

    def test_missing_task_asks(self):
        res = mc.create_reminder("", "", now=NOW)
        self.assertFalse(res.ok)
        self.assertIn("What should I remind you about", res.message)

    def test_reminders_not_on_allow_list_is_blocked(self):
        os.environ["SARA_ALLOWED_APPS"] = "Notes"
        res = mc.create_reminder("x", "", now=NOW)
        self.assertFalse(res.ok)
        self.assertEqual(res.risk, mc.Risk.BLOCKED)

    def test_reminder_args_are_argv_not_script(self):
        evil = 'x" & (do shell script "rm -rf ~") & "'
        with mock.patch.object(mc.subprocess, "run") as run:
            mc.create_reminder(evil, "", now=NOW)
        argv = run.call_args[0][0]
        self.assertNotIn("rm -rf", argv[2])  # the script itself
        self.assertIn("rm -rf", argv[3])  # only in the data argument

    def test_due_date_parts_are_argv(self):
        with mock.patch.object(mc.subprocess, "run") as run:
            mc.create_reminder("call the dentist", "at 5 pm", now=NOW)
        argv = run.call_args[0][0]
        self.assertEqual(argv[3], "call the dentist")
        self.assertEqual(argv[4:], ["2026", "11", "11", "17", "0"])

    def test_success_message_says_the_resolved_time(self):
        os.environ["SARA_MAC_DRY_RUN"] = "true"
        res = mc.create_reminder("call the dentist", "at 7 am", now=NOW, next_day_ok=True)
        self.assertIn("2026-11-12 07:00", res.message)


class PendingReminderControllerTests(unittest.TestCase):
    """End to end through VoiceController: question -> short answer -> reminder."""

    def setUp(self):
        os.environ["SARA_MAC_DRY_RUN"] = "true"
        os.environ.pop("SARA_ALLOWED_APPS", None)
        self.calls = []
        self.spoken = []
        self.snaps = []
        self.overlay = OverlayState()
        self.overlay.subscribe(self.snaps.append)
        self._freeze = freeze_now()  # NOW is 3 pm, so "at 5 pm" is still today
        self._freeze.start()

        def recording_execute(action, **kwargs):
            self.calls.append((action, kwargs))
            return mc.execute(action, **kwargs)

        self.controller = VoiceController(
            overlay=self.overlay,
            notify=self.spoken.append,
            execute=recording_execute,
            confirm=lambda _: False,
            followup_seconds=60,
        )

    def tearDown(self):
        self._freeze.stop()
        os.environ.pop("SARA_MAC_DRY_RUN", None)
        os.environ.pop("SARA_ALLOWED_APPS", None)

    def _remind_calls(self):
        return [c for c in self.calls if c[0] == "REMIND"]

    def test_short_time_answer_completes_held_task(self):
        result = self.controller.on_final("Sara, remind me to call the dentist at 5")
        self.assertEqual(result, "mac")
        self.assertEqual(self.overlay.snapshot.phase, Phase.WAITING)
        self.assertIn("5 am or 5 pm", self.spoken[-1])
        result = self.controller.on_final("5 pm")
        self.assertEqual(result, "mac")
        self.assertEqual(self.overlay.snapshot.phase, Phase.DONE)
        last = self._remind_calls()[-1][1]
        self.assertEqual(last["task"], "call the dentist")
        self.assertEqual(last["when"], "at 5 pm")

    def test_at_prefixed_answer_completes_held_task(self):
        self.controller.on_final("Sara, remind me to buy milk at 5")
        self.controller.on_final("at 8 am")
        last = self._remind_calls()[-1][1]
        self.assertEqual(last["task"], "buy milk")
        self.assertEqual(last["when"], "at 8 am")

    def test_cancel_word_does_not_create(self):
        self.controller.on_final("Sara, remind me to call the dentist at 5")
        result = self.controller.on_final("Sara, cancel")
        self.assertEqual(result, "cancelled")
        result = self.controller.on_final("5 pm")
        self.assertEqual(result, "ignored")
        self.assertEqual(len(self._remind_calls()), 1)  # only the first, unclear attempt

    def test_expired_followup_window_does_not_create(self):
        self.controller.followup_seconds = 0  # window closes immediately
        self.controller.on_final("Sara, remind me to call the dentist at 5")
        result = self.controller.on_final("5 pm")
        self.assertEqual(result, "ignored")
        self.assertEqual(len(self._remind_calls()), 1)

    def test_unrelated_command_does_not_inherit_task(self):
        self.controller.on_final("Sara, remind me to call the dentist at 5")
        self.controller.on_final("Sara, open Safari")
        self.assertEqual(self.calls[-1][0], "OPEN_APP")
        result = self.controller.on_final("5 pm")
        self.assertEqual(result, "ignored")
        self.assertEqual(len(self._remind_calls()), 1)

    def test_fresh_reminder_command_does_not_inherit_task(self):
        self.controller.on_final("Sara, remind me to call the dentist at 5")
        self.controller.on_final("remind me to water the plants in 20 minutes")
        tasks = [c[1]["task"] for c in self._remind_calls()]
        self.assertNotIn(("call the dentist", "in 20 minutes"), [(c[1]["task"], c[1]["when"]) for c in self._remind_calls()])
        self.assertEqual(tasks, ["call the dentist", "water the plants"])

    def test_failure_shows_waiting_not_error_and_success_shows_done(self):
        self.controller.on_final("Sara, remind me to call the dentist at 5")
        self.assertEqual(self.overlay.snapshot.phase, Phase.WAITING)
        self.assertNotEqual(self.overlay.snapshot.phase, Phase.ERROR)
        self.controller.on_final("5 pm")
        self.assertEqual(self.overlay.snapshot.phase, Phase.DONE)


class ConfirmTomorrowControllerTests(unittest.TestCase):
    def setUp(self):
        os.environ["SARA_MAC_DRY_RUN"] = "true"
        os.environ.pop("SARA_ALLOWED_APPS", None)
        self.calls = []
        self.spoken = []
        self.overlay = OverlayState()
        self._freeze = freeze_now()
        self._freeze.start()

        def recording_execute(action, **kwargs):
            self.calls.append((action, kwargs))
            return mc.execute(action, **kwargs)

        self.controller = VoiceController(
            overlay=self.overlay,
            notify=self.spoken.append,
            execute=recording_execute,
            confirm=lambda _: False,
            followup_seconds=60,
        )

    def tearDown(self):
        self._freeze.stop()
        os.environ.pop("SARA_MAC_DRY_RUN", None)
        os.environ.pop("SARA_ALLOWED_APPS", None)

    def _remind_calls(self):
        return [c for c in self.calls if c[0] == "REMIND"]

    def test_past_time_asks_before_creating_for_tomorrow(self):
        result = self.controller.on_final("Sara, remind me to call the dentist at 7 am")
        self.assertEqual(result, "mac")
        self.assertEqual(self.overlay.snapshot.phase, Phase.WAITING)
        self.assertIn("tomorrow at 7:00 AM", self.spoken[-1])
        self.assertFalse(self.controller._pending_reminder is None)
        # Nothing created yet: the only REMIND call ended in the question.
        self.assertEqual(len(self._remind_calls()), 1)

    def test_yes_creates_for_tomorrow(self):
        self.controller.on_final("Sara, remind me to call the dentist at 7 am")
        result = self.controller.on_final("yes")
        self.assertEqual(result, "mac")
        last = self._remind_calls()[-1][1]
        self.assertEqual(last["task"], "call the dentist")
        self.assertTrue(last["next_day_ok"])
        self.assertEqual(self.overlay.snapshot.phase, Phase.DONE)

    def test_no_creates_nothing(self):
        self.controller.on_final("Sara, remind me to call the dentist at 7 am")
        result = self.controller.on_final("no")
        self.assertEqual(result, "cancelled")
        self.assertEqual(len(self._remind_calls()), 1)
        self.assertEqual(self.overlay.snapshot.phase, Phase.DONE)

    def test_different_time_answer_replaces_the_day(self):
        self.controller.on_final("Sara, remind me to call the dentist at 7 am")
        result = self.controller.on_final("at 9 pm")
        self.assertEqual(result, "mac")
        last = self._remind_calls()[-1][1]
        self.assertEqual(last["task"], "call the dentist")
        self.assertEqual(last["when"], "at 9 pm")
        self.assertEqual(self.overlay.snapshot.phase, Phase.DONE)


class ControllerDispatchTests(unittest.TestCase):
    def _controller(self, calls, followup_seconds=0):
        def execute(action, **kwargs):
            calls.append((action, kwargs))
            return mc.MacResult(True, "mock action")

        return VoiceController(execute=execute, confirm=lambda _: False, followup_seconds=followup_seconds)

    def test_voice_command_dispatches_remind(self):
        calls = []
        controller = self._controller(calls)
        controller.on_partial("Sara, remind me to call the dentist at 5 pm")
        self.assertEqual(calls, [])  # reminders wait for the final transcript
        result = controller.on_final("Sara, remind me to call the dentist at 5 pm")
        self.assertEqual(result, "mac")
        self.assertEqual(calls[0][0], "REMIND")
        self.assertEqual(calls[0][1]["task"], "call the dentist")
        self.assertEqual(calls[0][1]["when"], "at 5 pm")

    def test_reminder_works_in_followup_window(self):
        calls = []
        controller = self._controller(calls, followup_seconds=60)
        controller.on_final("Sara, remind me to call the dentist in 20 minutes")
        result = controller.on_final("remind me to water the plants at 8 am")
        self.assertEqual(result, "mac")
        self.assertEqual(calls[1][0], "REMIND")
        self.assertEqual(calls[1][1]["task"], "water the plants")
        self.assertEqual(calls[1][1]["when"], "at 8 am")

    def test_open_reminders_app_still_works(self):
        calls = []
        controller = self._controller(calls)
        controller.on_final("Sara, open reminders")
        self.assertEqual(calls[0][0], "OPEN_APP")


if __name__ == "__main__":
    unittest.main()
