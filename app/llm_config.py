"""LLM settings from the environment, so any OpenAI-compatible provider works.

Defaults are unchanged: OPENAI_API_KEY, OPENAI_MODEL (gpt-5-mini) and OpenAI's
own endpoint. To use another provider set, in your local .env (never commit it):

    SARA_LLM_BASE_URL   (or OPENAI_BASE_URL)  provider's OpenAI-compatible URL
    SARA_LLM_API_KEY    (or OPENAI_API_KEY)   that provider's key
    SARA_LLM_MODEL      (or OPENAI_MODEL)     model name at that provider
    SARA_INSTRUCTOR_MODE  tools (default) | json | json_schema | md_json

SARA_* names win over the OPENAI_* ones when both are set.
"""
from __future__ import annotations

import os
from typing import Mapping, Optional

DEFAULT_MODEL = "gpt-5-mini"
INSTRUCTOR_MODES = {
    "tools": "TOOLS",
    "json": "JSON",
    "json_schema": "JSON_SCHEMA",
    "md_json": "MD_JSON",
}


def _first(env: Mapping[str, str], *names: str) -> Optional[str]:
    for n in names:
        v = (env.get(n) or "").strip()
        if v:
            return v
    return None


def llm_settings(env: Optional[Mapping[str, str]] = None) -> dict:
    env = os.environ if env is None else env
    return {
        "api_key": _first(env, "SARA_LLM_API_KEY", "OPENAI_API_KEY"),
        "base_url": _first(env, "SARA_LLM_BASE_URL", "OPENAI_BASE_URL"),
        "model": _first(env, "SARA_LLM_MODEL", "OPENAI_MODEL") or DEFAULT_MODEL,
    }


def instructor_mode_name(env: Optional[Mapping[str, str]] = None) -> Optional[str]:
    """Name of the instructor.Mode member to use, or None to keep instructor's default."""
    env = os.environ if env is None else env
    raw = (env.get("SARA_INSTRUCTOR_MODE") or "").strip().lower()
    if not raw:
        return None
    if raw not in INSTRUCTOR_MODES:
        raise ValueError(f"SARA_INSTRUCTOR_MODE must be one of {sorted(INSTRUCTOR_MODES)}, got '{raw}'")
    return INSTRUCTOR_MODES[raw]
