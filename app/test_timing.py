import contextlib
import io
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(__file__))

import mac_control as mc


class TimingTests(unittest.TestCase):
    def run_execute(self, clock_values, dry):
        buf = io.StringIO()
        env = {"SARA_MAC_DRY_RUN": "true"} if dry else {}
        with mock.patch.dict(os.environ, env, clear=False), mock.patch("time.monotonic", side_effect=clock_values), \
                contextlib.redirect_stdout(buf), mock.patch.object(mc, "_execute_untimed",
                                                                   return_value=mc.MacResult(True, "ok")):
            if not dry:
                os.environ.pop("SARA_MAC_DRY_RUN", None)
            res = mc.execute("OPEN_APP", app="Notes")
        return res, buf.getvalue()

    def test_fast_action_prints_only_the_time(self):
        res, out = self.run_execute([10.0, 10.2], dry=False)
        self.assertTrue(res.ok)
        self.assertIn("[timing] OPEN_APP took 200 ms", out)
        self.assertNotIn("slow", out)

    def test_slow_action_explains_why(self):
        _, out = self.run_execute([10.0, 15.5], dry=False)
        self.assertIn("took 5500 ms", out)
        self.assertIn("Automation", out)

    def test_dry_run_never_blames_the_mac(self):
        _, out = self.run_execute([10.0, 15.5], dry=True)
        self.assertNotIn("slow", out)

    def test_min_rms_default_lets_her_speech_through(self):
        import voice_commands as vc

        self.assertLessEqual(vc.MIN_RMS, 46)  # her quietest real utterance was level 46


if __name__ == "__main__":
    unittest.main()
