import io
import json
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(__file__))

import llm_models as lm


class Boom(Exception):
    def __init__(self, msg, status=None):
        super().__init__(msg)
        self.status_code = status


class Fake:
    def __init__(self, outcomes):
        self.outcomes, self.models = list(outcomes), []
        self.chat = self
        self.completions = self

    def create(self, model, **kw):
        self.models.append(model)
        o = self.outcomes.pop(0)
        if isinstance(o, Exception):
            raise o
        return o


class ErrorTests(unittest.TestCase):
    def test_real_error_is_a_model_error(self):
        e = Boom("models/gemini-2.5-flash is no longer available to new users.", 404)
        self.assertTrue(lm.is_model_unavailable(e))
        self.assertIn("llm_models.py", lm.friendly_error(e, "gemini-2.5-flash"))

    def test_wrapped_error_is_found(self):
        inner = Boom("x", 404)
        try:
            try:
                raise inner
            except Boom as b:
                raise RuntimeError("retry failed") from b
        except RuntimeError as outer:
            self.assertTrue(lm.is_model_unavailable(outer))

    def test_other_errors(self):
        self.assertIn("key was rejected", lm.friendly_error(Boom("bad", 401)))
        self.assertIn("usage limit", lm.friendly_error(Boom("quota", 429)))
        self.assertIn("could not reach", lm.friendly_error(Boom("timeout")))
        self.assertFalse(lm.is_model_unavailable(Boom("timeout")))


class FallbackTests(unittest.TestCase):
    def test_lists(self):
        self.assertEqual(lm.fallback_models("https://x/v1", {}), [])
        g = lm.fallback_models("https://generativelanguage.googleapis.com/v1beta/openai/", {})
        self.assertEqual(g[0], "gemini-flash-latest")
        self.assertEqual(lm.fallback_models("u", {"SARA_LLM_FALLBACK_MODELS": "models/a, b"}), ["a", "b"])

    def test_create_falls_back_and_remembers(self):
        gem = "https://generativelanguage.googleapis.com/v1beta/openai/"
        fake = Fake([Boom("no longer available", 404), "ok"])
        cfg = {"instructor": fake, "model": "gemini-2.5-flash"}
        with mock.patch("builtins.print"):
            self.assertEqual(lm.create_with_fallback(cfg, gem, messages=[]), "ok")
        self.assertEqual(fake.models, ["gemini-2.5-flash", "gemini-flash-latest"])
        self.assertEqual(cfg["model"], "gemini-flash-latest")

    def test_no_fallback_for_other_errors_or_other_providers(self):
        fake = Fake([Boom("timeout")])
        with self.assertRaises(Boom):
            lm.create_with_fallback({"instructor": fake, "model": "m"}, "https://generativelanguage.googleapis.com/x")
        fake = Fake([Boom("gone", 404)])
        with self.assertRaises(Boom):
            lm.create_with_fallback({"instructor": fake, "model": "m"}, "https://api.openai.com/v1")
        self.assertEqual(fake.models, ["m"])


class ListingTests(unittest.TestCase):
    def test_list_and_filter(self):
        body = json.dumps({"data": [{"id": "models/gemini-flash-latest"}, {"id": "models/text-embedding-004"},
                                    {"id": "models/gemini-3.5-flash-lite"}, {"id": "models/gemini-live-x"}]}).encode()
        resp = mock.MagicMock()
        resp.read.return_value = body
        resp.__enter__.return_value = resp
        with mock.patch("urllib.request.urlopen", return_value=resp) as op:
            ids = lm.list_models("https://h/v1/", "KEY")
        self.assertEqual(op.call_args[0][0].full_url, "https://h/v1/models")
        self.assertEqual(lm.chat_models(ids), ["gemini-3.5-flash-lite", "gemini-flash-latest"])


if __name__ == "__main__":
    unittest.main()
