"""Choosing and signing in to an AI provider. Shared by `fiatvox signin` and the setup screen."""
from __future__ import annotations

from dataclasses import dataclass

from . import keys, pi_backend
from .direct import PROVIDERS as DIRECT

CLAUDE_NOTE = ("Claude Pro and Max subscriptions work only through Pi. Anthropic bills that use as extra usage, "
               "per token, not as part of your plan. For a fixed monthly cost, use a ChatGPT or GitHub Copilot "
               "subscription through Pi; or use an API key, or no AI.")

# Models that suit Fiat Vox's short JSON tasks, per Pi provider; the first available one is preselected.
PREFERRED = {
    "anthropic": ["claude-sonnet-5-5", "claude-opus-5-5"],
    "openai-codex": ["gpt-5.1", "gpt-5", "gpt-5-codex"],
    "openai": ["gpt-5.1", "gpt-5"],
    "github-copilot": ["claude-sonnet-5-5", "gpt-5.1", "gpt-5"],
    "xai": ["grok-4", "grok-4-fast"],
    "openrouter": ["anthropic/claude-sonnet-5.5", "openai/gpt-5.1"],
}


@dataclass
class Choice:
    kind: str        # "pi-oauth", "pi-key", "direct", "none"
    provider: str
    label: str


def choices() -> list[Choice]:
    out = []
    if pi_backend.installed():
        try:
            provs = pi_backend.providers()
        except Exception:
            provs = []
        for p in provs:
            if p["oauth"]:
                out.append(Choice("pi-oauth", p["id"], f"{p['oauthName'] or p['name']} (sign in through Pi)"))
        for p in provs:
            if p["apiKey"] and p["id"] in ("anthropic", "openai", "google", "openrouter", "xai", "ollama"):
                out.append(Choice("pi-key", p["id"], f"{p['name']} API key (through Pi)"))
    for pid, info in DIRECT.items():
        out.append(Choice("direct", pid, f"{info['label']} key (stored in your Keychain, without Pi)"))
    out.append(Choice("none", "none", "No AI (local heuristics only)"))
    return out


def pick_model(provider: str, available: list[dict]) -> str:
    """'provider/model' for Pi, preferring a known good model, else the first one offered."""
    ids = [m["id"] for m in available if m["provider"] == provider]
    for want in PREFERRED.get(provider, []):
        if want in ids:
            return f"{provider}/{want}"
    return f"{provider}/{ids[0]}" if ids else ""


def save_direct_key(provider: str, key: str) -> None:
    keys.put(provider, key)
