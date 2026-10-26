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
    """Tokens and cost across one run's AI requests, in the terms Pi and Feynman show: new input, output, cache
    reads and cache writes. `cost` is in dollars as the provider reports it; on a subscription it is the
    equivalent API price, covered by the plan."""
    calls: int = 0
    input_tokens: int = 0          # new input, not served from the cache
    output_tokens: int = 0
    cache_read: int = 0
    cache_write: int = 0
    cost: float = 0.0
    billing: str = ""              # "sub" (a subscription plan) or "api" (paid per token)
    by_purpose: dict = field(default_factory=dict)
    on_change: object = field(default=None, repr=False, compare=False)

    def __post_init__(self):
        import threading
        self._lock = threading.Lock()

    def add(self, purpose: str, inp: int = 0, out: int = 0, cost: float = 0.0, cache_read: int = 0,
            cache_write: int = 0):
        with self._lock:
            self.calls += 1
            self.input_tokens += inp
            self.output_tokens += out
            self.cache_read += cache_read
            self.cache_write += cache_write
            self.cost += cost
            p = self.by_purpose.setdefault(purpose, [0, 0, 0])
            p[0] += 1
            p[1] += inp
            p[2] += out
            snap = self.snapshot()
        if callable(self.on_change):
            self.on_change(snap)

    def snapshot(self) -> dict:
        return {"calls": self.calls, "input": self.input_tokens, "output": self.output_tokens,
                "cache_read": self.cache_read, "cache_write": self.cache_write, "cost": round(self.cost, 6),
                "billing": self.billing}


def short(n: float) -> str:
    """29000 -> 29k, 3500000 -> 3.5M, as Feynman shows token counts."""
    n = float(n)
    if n >= 1e6:
        return f"{n / 1e6:.1f}M"
    if n >= 1e3:
        return f"{n / 1e3:.0f}k" if n >= 1e4 else f"{n / 1e3:.1f}k"
    return f"{int(n)}"


def counter_text(u: dict) -> str:
    """The token counter line: ↑ new input, ↓ output, R cache reads, W cache writes, CH cache hits, cost."""
    seen = u.get("input", 0) + u.get("cache_read", 0)
    hit = int(1000 * u.get("cache_read", 0) / seen) / 10 if seen else 0.0  # rounded down, as Feynman shows it
    bill = f" ({u['billing']})" if u.get("billing") else ""
    return (f"\u2191{short(u.get('input', 0))} \u2193{short(u.get('output', 0))} R{short(u.get('cache_read', 0))} "
            f"W{short(u.get('cache_write', 0))} CH{hit:.1f}% ${u.get('cost', 0):.3f}{bill}  "
            f"{u.get('calls', 0)} AI request{'s' if u.get('calls', 0) != 1 else ''}")


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
