"""Direct API keys for people who do not want Pi: Anthropic, OpenAI, Google Gemini, OpenRouter.

Anthropic goes through the official `anthropic` SDK; the others over plain HTTPS with httpx.
Keys come from the Keychain (ai/keys.py).
"""
from __future__ import annotations

import httpx

from . import keys
from .base import AIError, Usage

PROVIDERS = {
    # Sonnet, as in the old pipeline's default (--model sonnet); claude-opus-5-5 is the stronger choice in Settings
    "anthropic": {"label": "Anthropic API", "default_model": "claude-sonnet-5-5",
                  "key_url": "https://console.anthropic.com/settings/keys"},
    "openai": {"label": "OpenAI API", "default_model": "gpt-5.1-mini",
               "key_url": "https://platform.openai.com/api-keys"},
    "gemini": {"label": "Google Gemini API", "default_model": "gemini-2.5-flash",
               "key_url": "https://aistudio.google.com/app/apikey"},
    "openrouter": {"label": "OpenRouter", "default_model": "anthropic/claude-sonnet-5.5",
                   "key_url": "https://openrouter.ai/keys"},
}
MAX_OUTPUT = 16000


class DirectBackend:
    name = "direct"

    def __init__(self, provider: str, model: str = ""):
        if provider not in PROVIDERS:
            raise ValueError(f"Unknown provider: {provider}")
        self.provider = provider
        self.model = model or PROVIDERS[provider]["default_model"]
        self.label = f"{PROVIDERS[provider]['label']} ({self.model})"
        self.usage = Usage()

    def available(self) -> tuple[bool, str]:
        if not keys.get(self.provider):
            return False, f"No {PROVIDERS[self.provider]['label']} key saved. Open Settings, AI, to add one."
        return True, ""

    def complete(self, prompt: str, *, purpose: str, timeout: int = 900) -> str:
        key = keys.get(self.provider)
        if not key:
            raise AIError(self.available()[1])
        try:
            text, inp, out = getattr(self, "_" + self.provider)(key, prompt, timeout)
        except httpx.HTTPStatusError as e:
            raise AIError(f"{PROVIDERS[self.provider]['label']}: HTTP {e.response.status_code}: "
                          f"{e.response.text[:300]}") from e
        except httpx.HTTPError as e:
            raise AIError(f"{PROVIDERS[self.provider]['label']}: {e}") from e
        self.usage.add(purpose, inp, out)
        return text

    def _anthropic(self, key, prompt, timeout):
        import anthropic
        client = anthropic.Anthropic(api_key=key, timeout=timeout, max_retries=3)
        try:
            # fallbacks="default": a policy decline on this model is retried server-side on a suitable model
            r = client.beta.messages.create(
                model=self.model, max_tokens=MAX_OUTPUT, betas=["server-side-fallback-2026-07-01"],
                fallbacks="default", output_config={"effort": "medium"},
                messages=[{"role": "user", "content": prompt}])
        except anthropic.APIConnectionError as e:
            raise AIError(f"Anthropic API: network error ({e})") from e
        except anthropic.AuthenticationError as e:
            raise AIError("Anthropic API: the saved key was rejected. Open Settings, AI, to replace it.") from e
        except anthropic.RateLimitError as e:
            raise AIError("Anthropic API: rate limited; try again later.") from e
        except anthropic.APIStatusError as e:
            raise AIError(f"Anthropic API: HTTP {e.status_code}: {e.message}") from e
        if r.stop_reason == "refusal":
            raise AIError("Anthropic API: the model declined this request.")
        text = "".join(b.text for b in r.content if b.type == "text")
        return text, r.usage.input_tokens, r.usage.output_tokens

    def _chat(self, url, key, prompt, timeout, extra_headers=None):
        r = httpx.post(url, timeout=timeout, headers={"Authorization": f"Bearer {key}", **(extra_headers or {})},
                       json={"model": self.model, "messages": [{"role": "user", "content": prompt}]})
        r.raise_for_status()
        d = r.json()
        text = d["choices"][0]["message"].get("content") or ""
        u = d.get("usage", {})
        return text, u.get("prompt_tokens", 0), u.get("completion_tokens", 0)

    def _openai(self, key, prompt, timeout):
        return self._chat("https://api.openai.com/v1/chat/completions", key, prompt, timeout)

    def _openrouter(self, key, prompt, timeout):
        return self._chat("https://openrouter.ai/api/v1/chat/completions", key, prompt, timeout,
                          {"X-Title": "Fiat Vox"})

    def _gemini(self, key, prompt, timeout):
        r = httpx.post(f"https://generativelanguage.googleapis.com/v1beta/models/{self.model}:generateContent",
                       timeout=timeout, headers={"x-goog-api-key": key},
                       json={"contents": [{"role": "user", "parts": [{"text": prompt}]}],
                             "generationConfig": {"maxOutputTokens": MAX_OUTPUT}})
        r.raise_for_status()
        d = r.json()
        parts = (d.get("candidates") or [{}])[0].get("content", {}).get("parts", [])
        u = d.get("usageMetadata", {})
        return "".join(p.get("text", "") for p in parts), u.get("promptTokenCount", 0), u.get("candidatesTokenCount", 0)
