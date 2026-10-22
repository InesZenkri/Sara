import os
import unittest

import overlay as ov


class OverlayStateTests(unittest.TestCase):
    def test_flow_and_snapshots(self):
        s = ov.OverlayState()
        seen = []
        s.subscribe(seen.append)
        s.listening("open no")
        s.heard("open notes")
        s.doing("Opening Notes")
        s.done("Opened Notes")
        s.hide()
        self.assertEqual([x.phase for x in seen], [
            ov.Phase.LISTENING, ov.Phase.HEARD, ov.Phase.DOING, ov.Phase.DONE, ov.Phase.IDLE])
        self.assertEqual(seen[0].title, "Listening")
        self.assertTrue(seen[0].visible)
        self.assertFalse(seen[-1].visible)

    def test_duplicate_updates_are_dropped(self):
        s = ov.OverlayState()
        seen = []
        s.subscribe(seen.append)
        s.listening("hi")
        s.listening("hi")
        self.assertEqual(len(seen), 1)

    def test_text_is_clipped_and_whitespace_normalised(self):
        s = ov.OverlayState()
        s.heard("a  b\n" + "x" * 500)
        t = s.snapshot.text
        self.assertLessEqual(len(t), ov.MAX_TEXT)
        self.assertTrue(t.startswith("a b x"))

    def test_broken_listener_does_not_break_others(self):
        s = ov.OverlayState()
        seen = []
        s.subscribe(lambda snap: 1 / 0)
        s.subscribe(seen.append)
        s.listening("x")
        self.assertEqual(len(seen), 1)

    def test_off_by_default(self):
        os.environ.pop("SARA_OVERLAY", None)
        self.assertFalse(ov.overlay_enabled())
        state, gui = ov.make_overlay()
        self.assertIsNone(gui)
        state.listening("works without a GUI")
        os.environ["SARA_OVERLAY"] = "true"
        self.assertTrue(ov.overlay_enabled())
        os.environ.pop("SARA_OVERLAY")


if __name__ == "__main__":
    unittest.main()
