"""
Model profiles (profiles.yaml) and the chat-model factory the graph uses.

Two providers:
  anthropic  langchain-anthropic's ChatAnthropic -- the API, with per-agent model,
             effort, adaptive thinking and (for the Opus 5.5 family) server-side
             refusal fallback
  desktop    the Claude Desktop bridge -- no API key; a Claude Desktop session answers
             each model call through the mailbox in runtime/bridge.py

Per-model notes that shape the settings: Haiku 4.5 takes neither `effort` nor adaptive
thinking, so worker profiles on Haiku set neither; Opus 5.5 always thinks adaptively,
so its profile sets only effort.
"""
from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

import yaml

PROFILES_PATH = Path(__file__).with_name("profiles.yaml")
FALLBACK_BETA = "server-side-fallback-2026-07-01"
MAX_TOKENS = 16000


@lru_cache(maxsize=1)
def _profiles() -> dict:
    return yaml.safe_load(PROFILES_PATH.read_text(encoding="utf-8"))


def profile_name(name: str | None = None) -> str:
    data = _profiles()
    name = name or os.environ.get("SUPPLY_BRAIN_AGENT_PROFILE") or data["default_profile"]
    if name not in data["profiles"]:
        raise ValueError(f"unknown profile {name!r}; profiles.yaml defines {sorted(data['profiles'])}")
    return name


def provider_of(profile: str | None = None) -> str:
    return _profiles()["profiles"][profile_name(profile)].get("provider", "anthropic")


def agent_settings(agent: str, profile: str | None = None) -> dict:
    prof = _profiles()["profiles"][profile_name(profile)]
    role = "orchestrator" if agent == "orchestrator" else "workers"
    settings = {**(prof.get(role) or {}), **(prof.get(agent) or {})}
    if "model" not in settings:
        raise ValueError(f"profile {profile_name(profile)!r} gives no model for {agent} (or for {role})")
    settings["provider"] = prof.get("provider", "anthropic")
    if prof.get("fallbacks"):
        settings.setdefault("fallbacks", prof["fallbacks"])
    return settings


def chat_model(agent: str, profile: str | None = None):
    s = agent_settings(agent, profile)
    if s["provider"] == "anthropic":
        from langchain_anthropic import ChatAnthropic
        kwargs = {"model": s["model"], "max_tokens": s.get("max_tokens", MAX_TOKENS), "max_retries": 2}
        if s.get("effort"):
            kwargs["effort"] = s["effort"]
        if s.get("thinking") == "adaptive":
            kwargs["thinking"] = {"type": "adaptive"}
        if s.get("fallbacks"):
            kwargs["betas"] = [FALLBACK_BETA]
            kwargs["model_kwargs"] = {"fallbacks": s["fallbacks"]}
        return ChatAnthropic(**kwargs)
    if s["provider"] == "desktop":
        from .bridge import DesktopBridgeChatModel
        return DesktopBridgeChatModel(agent=agent, model_alias=s["model"])
    raise ValueError(f"unknown provider {s['provider']!r} in profiles.yaml")
