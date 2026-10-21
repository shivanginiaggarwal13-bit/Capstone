"""
Model profiles (profiles.yaml) and the chat-model factory the graph uses.

Three providers:
  nvidia     NVIDIA Build API (OpenAI-compatible) -- uses langchain-openai's ChatOpenAI
             with custom base_url pointing to https://integrate.api.nvidia.com/v1
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
NVIDIA_API_URL = "https://integrate.api.nvidia.com/v1"


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
    return _profiles()["profiles"][profile_name(profile)].get("provider", "nvidia")


def agent_settings(agent: str, profile: str | None = None) -> dict:
    prof = _profiles()["profiles"][profile_name(profile)]
    role = "orchestrator" if agent == "orchestrator" else "workers"
    settings = {**(prof.get(role) or {}), **(prof.get(agent) or {})}
    if "model" not in settings:
        raise ValueError(f"profile {profile_name(profile)!r} gives no model for {agent} (or for {role})")
    settings["provider"] = prof.get("provider", "nvidia")
    if prof.get("fallbacks"):
        settings.setdefault("fallbacks", prof["fallbacks"])
    return settings


def chat_model(agent: str, profile: str | None = None, streaming: bool = False):
    s = agent_settings(agent, profile)
    if s["provider"] == "nvidia":
        from langchain_openai import ChatOpenAI
        api_key = os.environ.get("NVIDIA_API_KEY")
        if not api_key:
            raise ValueError("NVIDIA_API_KEY environment variable is required for nvidia provider")
        kwargs = {
            "model": s["model"],
            "max_tokens": s.get("max_tokens", MAX_TOKENS),
            "temperature": s.get("temperature", 0.5),
            "api_key": api_key,
            "base_url": NVIDIA_API_URL,
            # No explicit timeout previously -> the openai SDK's own default (10 minutes)
            # applied per call, and max_retries=2 could multiply that up to ~30 minutes with
            # no visible signal to the caller that anything was wrong. A live multi-turn chat
            # needs a call to fail fast and visibly far sooner than that.
            #
            # First cut was request_timeout=60/max_retries=1 -- too tight: a real multi-part
            # planner request ("rains... beverage surge... dairy fall") timed out on the very
            # first orchestrator call, before the GLM model (apparently reasoning-heavy, see
            # the max_tokens comment in profiles.yaml) could finish. Raised to 180s. Retries
            # dropped to 0 rather than widened further: a retry re-sends the identical request
            # to what's very likely a genuinely-slow response, not a transient fault, so it
            # would just double the wait without improving the odds of success.
            "request_timeout": 180,
            "max_retries": 0,
            "top_p": s.get("top_p", 1),
            "frequency_penalty": s.get("frequency_penalty", 0),
            "presence_penalty": s.get("presence_penalty", 0),
            "streaming": streaming,
        }
        return ChatOpenAI(**kwargs)
    if s["provider"] == "anthropic":
        from langchain_anthropic import ChatAnthropic
        kwargs = {"model": s["model"], "max_tokens": s.get("max_tokens", MAX_TOKENS), "max_retries": 2,
                  "default_request_timeout": 300}
        if s.get("effort"):
            # effort lives in output_config on the Messages API; ChatAnthropic has no `effort` field
            kwargs["output_config"] = {"effort": s["effort"]}
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
