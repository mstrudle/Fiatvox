"""The AI backend interface. The AI only ever returns structured decisions as JSON; Python applies them."""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Protocol


class AIError(RuntimeError):
    pass


@dataclass
class Usage:
    calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cost: float = 0.0          # in dollars, when the provider reports it
    by_purpose: dict = field(default_factory=dict)

    def add(self, purpose: str, inp: int = 0, out: int = 0, cost: float = 0.0):
        self.calls += 1
        self.input_tokens += inp
        self.output_tokens += out
        self.cost += cost
        p = self.by_purpose.setdefault(purpose, [0, 0, 0])
        p[0] += 1
        p[1] += inp
        p[2] += out


class Backend(Protocol):
    name: str
    label: str
    usage: Usage

    def available(self) -> tuple[bool, str]:
        """Ready to use, and if not, why."""

    def complete(self, prompt: str, *, purpose: str, timeout: int = 900) -> str:
        """The model's raw text reply."""


def extract_json(text: str) -> dict:
    out = re.sub(r"^```(?:json)?\s*|\s*```$", "", (text or "").strip())
    m = re.search(r"\{.*\}", out, re.S)
    if not m:
        if out.strip() in ("", "{}"):
            return {}
        raise ValueError("no JSON object in the reply")
    return json.loads(m.group(0))


def ask_json(backend: Backend, prompt: str, *, purpose: str, timeout: int = 900) -> dict:
    """Ask for a JSON object; one retry with a reminder; a clear error otherwise (the old ask_claude)."""
    last = ""
    for attempt in range(2):
        p = prompt if attempt == 0 else prompt + "\n\nYour previous reply was not valid JSON. Reply with the JSON object only."
        text = backend.complete(p, purpose=purpose, timeout=timeout)
        try:
            return extract_json(text)
        except ValueError:
            last = (text or "")[:300]
    raise AIError(f"{backend.label} did not return valid JSON: {last}")
