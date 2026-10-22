"""Helpers for "which model can my key use?" and readable LLM errors.

    cd app && python llm_models.py        # lists the chat models your key can see

Reads SARA_LLM_API_KEY / SARA_LLM_BASE_URL (or OPENAI_*) from .env. Never prints the key.
"""
from __future__ import annotations

import json
import os
import re
import urllib.request
from typing import Optional

# Tried in order when the chosen Gemini model is not available for the key.
# Google limits the 2.5 models to existing users and points new projects to the
# newest Flash / Flash-Lite (https://ai.google.dev/gemini-api/docs/changelog).
GEMINI_FALLBACKS = ("gemini-flash-latest", "gemini-3.5-flash-lite", "gemini-3.8-flash")

_NOT_CHAT = re.compile(r"embed|tts|image|imagen|veo|live|audio|transcribe|aqa|whisper|dall|moderation|realtime", re.I)


def _clean(name: str) -> str:
    return name.strip().removeprefix("models/")


def fallback_models(base_url: Optional[str], env=None) -> list:
    """SARA_LLM_FALLBACK_MODELS (comma separated) wins; otherwise a Gemini list for Gemini."""
    env = os.environ if env is None else env
    raw = (env.get("SARA_LLM_FALLBACK_MODELS") or "").strip()
    if raw:
        return [_clean(m) for m in raw.split(",") if m.strip()]
    if base_url and "generativelanguage.googleapis.com" in base_url:
        return list(GEMINI_FALLBACKS)
    return []


def _chain(exc: Optional[BaseException]):
    """The exception and whatever it wraps (instructor wraps API errors)."""
    seen = []
    while exc is not None and exc not in seen and len(seen) < 6:
        seen.append(exc)
        exc = exc.__cause__ or exc.__context__
    return seen


def is_model_unavailable(exc: BaseException) -> bool:
    for e in _chain(exc):
        text = str(e).lower()
        if getattr(e, "status_code", None) == 404:
            return True
        if "no longer available" in text or ("model" in text and ("not found" in text or "does not exist" in text)):
            return True
    return False


def friendly_error(exc: Exception, model: str = "") -> str:
    """One short sentence that is fine to show or speak."""
    text = " ".join(str(e).lower() for e in _chain(exc))
    status = next((getattr(e, "status_code", None) for e in _chain(exc) if getattr(e, "status_code", None)), None)
    if is_model_unavailable(exc):
        return (
            f"The model {model or 'I use'} is not available for this key. "
            "Run python llm_models.py to see which models work, then set SARA_LLM_MODEL."
        )
    if status in (401, 403) or "api key" in text or "permission" in text:
        return "My language model key was rejected. Check the key in your .env file."
    if status == 429 or "quota" in text or "rate limit" in text or "resource_exhausted" in text:
        return "I hit the free usage limit of my language model. Try again in a minute."
    return "Sorry, I could not reach my language model."


def create_with_fallback(client_config: dict, base_url: Optional[str], **kwargs):
    """client_config["instructor"].chat.completions.create(...) with the configured model.
    If that model is unavailable for the key, try each fallback model once and keep the
    one that works in client_config["model"]."""
    tried = [client_config["model"]]
    while True:
        try:
            return client_config["instructor"].chat.completions.create(model=client_config["model"], **kwargs)
        except Exception as exc:
            if not is_model_unavailable(exc):
                raise
            options = [m for m in fallback_models(base_url) if m not in tried]
            if not options:
                raise
            print(
                f"Model '{tried[-1]}' is not available for this key, trying '{options[0]}'. "
                "Set SARA_LLM_MODEL to keep it.",
                flush=True,
            )
            tried.append(options[0])
            client_config["model"] = options[0]


def list_models(base_url: Optional[str] = None, api_key: Optional[str] = None, timeout: float = 10.0) -> list:
    base = (base_url or "https://api.openai.com/v1").rstrip("/")
    req = urllib.request.Request(base + "/models", headers={"Authorization": f"Bearer {api_key or ''}"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        data = json.loads(resp.read())
    items = data.get("data") or data.get("models") or []
    return sorted({_clean(m.get("id") or m.get("name") or "") for m in items if isinstance(m, dict)} - {""})


def chat_models(ids) -> list:
    return [m for m in ids if not _NOT_CHAT.search(m)]


def main() -> int:
    try:
        from dotenv import load_dotenv

        load_dotenv()
    except ImportError:
        pass
    from llm_config import llm_settings

    s = llm_settings()
    if not s["api_key"]:
        print("No API key found. Put SARA_LLM_API_KEY (or OPENAI_API_KEY) in app/.env")
        return 1
    print(f"Endpoint: {s['base_url'] or 'OpenAI default'}")
    print(f"Configured model: {s['model']}")
    try:
        ids = chat_models(list_models(s["base_url"], s["api_key"]))
    except Exception as exc:
        print("Could not list models:", friendly_error(exc, s["model"]), f"({type(exc).__name__})")
        return 1
    print(f"{len(ids)} chat models visible to this key:")
    for m in ids:
        print("  " + m + ("   <- configured" if m == s["model"] else ""))
    if s["model"] not in ids:
        print(f"'{s['model']}' is not in the list. Set SARA_LLM_MODEL to one of the names above.")
    print("A model can be listed and still be refused for new users; Sara then tries the fallbacks automatically.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
