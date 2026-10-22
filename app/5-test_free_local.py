import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(__file__))

import llm_config
import tts_local


class LLMConfig(unittest.TestCase):
    def test_defaults_unchanged(self):
        s = llm_config.llm_settings({"OPENAI_API_KEY": "k"})
        self.assertEqual(s, {"api_key": "k", "base_url": None, "model": "gpt-5-mini"})

    def test_sara_vars_win(self):
        s = llm_config.llm_settings({
            "OPENAI_API_KEY": "a", "SARA_LLM_API_KEY": "b",
            "OPENAI_MODEL": "x", "SARA_LLM_MODEL": "y",
            "SARA_LLM_BASE_URL": "http://h/v1",
        })
        self.assertEqual((s["api_key"], s["model"], s["base_url"]), ("b", "y", "http://h/v1"))

    def test_blank_ignored(self):
        self.assertEqual(llm_config.llm_settings({"SARA_LLM_MODEL": "  "})["model"], "gpt-5-mini")

    def test_mode(self):
        self.assertIsNone(llm_config.instructor_mode_name({}))
        self.assertEqual(llm_config.instructor_mode_name({"SARA_INSTRUCTOR_MODE": "JSON"}), "JSON")
        self.assertEqual(llm_config.instructor_mode_name({"SARA_INSTRUCTOR_MODE": "json_schema"}), "JSON_SCHEMA")
        with self.assertRaises(ValueError):
            llm_config.instructor_mode_name({"SARA_INSTRUCTOR_MODE": "nope"})


class SayCommand(unittest.TestCase):
    def make(self, env):
        with mock.patch.dict(os.environ, env, clear=True), mock.patch("shutil.which", return_value="/usr/bin/say"):
            return tts_local.SayTTS()

    def test_command_is_argv_with_double_dash(self):
        c = self.make({}).command("/tmp/o.wav", "-rm; $(x) hi")
        self.assertEqual(c[0], "say")
        self.assertEqual(c[-2:], ["--", "-rm; $(x) hi"])
        self.assertNotIn("-v", c)

    def test_voice_and_rate(self):
        c = self.make({"SARA_SAY_VOICE": "Samantha", "SARA_SAY_RATE": "190"}).command("/tmp/o.wav", "hi")
        self.assertIn("Samantha", c)
        self.assertIn("190", c)

    def test_text_capped(self):
        c = self.make({}).command("/tmp/o.wav", "a" * 5000)
        self.assertEqual(len(c[-1]), tts_local.MAX_CHARS)


class MakeTTS(unittest.TestCase):
    def test_none(self):
        with mock.patch.dict(os.environ, {"SARA_TTS": "none"}, clear=True):
            self.assertIsNone(tts_local.make_tts())

    def test_auto_without_key_uses_say(self):
        with mock.patch.dict(os.environ, {}, clear=True), mock.patch("shutil.which", return_value="/usr/bin/say"):
            self.assertIsInstance(tts_local.make_tts(), tts_local.SayTTS)

    def test_no_say_binary_gives_none(self):
        with mock.patch.dict(os.environ, {}, clear=True), mock.patch("shutil.which", return_value=None):
            self.assertIsNone(tts_local.make_tts())

    def test_cartesia_without_key_is_none(self):
        with mock.patch.dict(os.environ, {"SARA_TTS": "cartesia"}, clear=True):
            self.assertIsNone(tts_local.make_tts())


class PartialInterval(unittest.TestCase):
    def test_env(self):
        import voice_commands as vc
        with mock.patch.dict(os.environ, {}, clear=True):
            self.assertEqual(vc.partial_interval_from_env(), vc.PARTIAL_INTERVAL)
        with mock.patch.dict(os.environ, {"SARA_PARTIAL_INTERVAL": "3"}, clear=True):
            self.assertEqual(vc.partial_interval_from_env(), 3.0)
        with mock.patch.dict(os.environ, {"SARA_PARTIAL_INTERVAL": "0"}, clear=True):
            self.assertEqual(vc.partial_interval_from_env(), float("inf"))
        with mock.patch.dict(os.environ, {"SARA_PARTIAL_INTERVAL": "abc"}, clear=True):
            self.assertEqual(vc.partial_interval_from_env(), vc.PARTIAL_INTERVAL)


if __name__ == "__main__":
    unittest.main()
