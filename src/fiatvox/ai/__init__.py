"""AI backends: Pi (default), direct API keys, or none (local heuristics only)."""
from __future__ import annotations

from .base import AIError, Backend, Usage, ask_json  # noqa: F401


class NoAI:
    """Local heuristics only. Always available."""
    name = "none"
    label = "no AI (local heuristics)"

    def __init__(self):
        self.usage = Usage()

    def available(self) -> tuple[bool, str]:
        return False, "AI is turned off."

    def complete(self, prompt: str, *, purpose: str, timeout: int = 900) -> str:
        raise AIError("AI is turned off")


def get_backend(provider: str, model: str = ""):
    if provider == "pi":
        from .pi_backend import PiBackend
        return PiBackend(model)
    if provider in ("anthropic", "openai", "gemini", "openrouter"):
        from .direct import DirectBackend
        return DirectBackend(provider, model)
    return NoAI()
