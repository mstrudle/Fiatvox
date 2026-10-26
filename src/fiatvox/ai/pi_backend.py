"""Pi, Fiat Vox's default AI route. Fiat Vox runs its own copy of Pi with its own config folder, non-interactively.

Pi handles sign-in for subscriptions (ChatGPT, Claude Pro/Max, GitHub Copilot, xAI and others), API keys and local
models. Claude subscriptions are only available through Pi, which bills them as Anthropic extra usage, per token.
"""
from __future__ import annotations

import json
import os
import subprocess
import tempfile
from collections.abc import Callable
from pathlib import Path

from .. import paths
from .base import AIError, Usage

PI_PACKAGE = "@earendil-works/pi-coding-agent"


def node_bin() -> Path:
    return paths.runtime_dir() / "node" / "bin" / "node"


def pi_root() -> Path:
    return paths.runtime_dir() / "pi" / "node_modules" / PI_PACKAGE


def pi_cli() -> Path:
    return pi_root() / "dist" / "bundle" / "cli.js"


def pi_sdk() -> Path:
    return pi_root() / "dist" / "index.js"


def agent_dir() -> Path:
    return paths.runtime_dir() / "pi-agent"


def login_script() -> Path:
    return Path(__file__).with_name("pi-login.mjs")


def env() -> dict:
    e = dict(os.environ)
    e.update({"PI_CODING_AGENT_DIR": str(agent_dir()), "PI_SKIP_VERSION_CHECK": "1", "PI_TELEMETRY": "0",
              "PI_SDK": str(pi_sdk())})
    # keys in the environment would bypass the user's choice in Fiat Vox
    for k in list(e):
        if k.endswith("_API_KEY") or k in ("ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_OAUTH_TOKEN"):
            e.pop(k)
    return e


def installed() -> bool:
    return node_bin().exists() and pi_cli().exists()


class PiBackend:
    name = "pi"

    def __init__(self, model: str = ""):
        self.model = model  # "provider/model-id"; empty uses Pi's default
        self.usage = Usage(billing=billing_of(model))
        self.label = f"Pi ({model})" if model else "Pi"

    def available(self) -> tuple[bool, str]:
        if not installed():
            return False, "Pi is not installed. Run `fiatvox doctor` to repair the install."
        provider = self.model.split("/", 1)[0] if "/" in self.model else ""
        if not provider:
            return False, "No AI model chosen. Open Settings, AI, to pick one."
        r = subprocess.run([str(node_bin()), str(pi_cli()), "auth", "check", "--provider", provider, "--json"],
                           capture_output=True, text=True, env=env(), timeout=60)
        if r.returncode == 0:
            return True, ""
        try:
            reason = json.loads(r.stdout).get("reason", "")
        except ValueError:
            reason = (r.stderr or r.stdout).strip()[:200]
        return False, f"Pi is not signed in to {provider} ({reason}). Open Settings, AI, to sign in."

    def command(self) -> list[str]:
        cmd = [str(node_bin()), str(pi_cli()), "--mode", "json", "--no-session", "--no-tools",
               "-ne", "-ns", "-np", "-nc", "--no-mcp", "--thinking", "off"]
        if self.model:
            cmd += ["--model", self.model]
        return cmd

    def complete(self, prompt: str, *, purpose: str, timeout: int = 900) -> str:
        with tempfile.TemporaryDirectory(prefix="fiatvox-pi-") as cwd:
            try:
                r = subprocess.run(self.command(), input=prompt, capture_output=True, text=True, env=env(),
                                   cwd=cwd, timeout=timeout)
            except subprocess.TimeoutExpired as e:
                raise AIError(f"Pi did not answer within {timeout} seconds") from e
        text, usage, error = parse_jsonl(r.stdout)
        if r.returncode != 0 or error:
            raise AIError(error or (r.stderr or r.stdout).strip()[-400:] or f"Pi exited with {r.returncode}")
        self.usage.add(purpose, usage.get("input", 0), usage.get("output", 0), usage.get("cost", 0.0),
                       usage.get("cache_read", 0), usage.get("cache_write", 0))
        return text


def parse_jsonl(stdout: str) -> tuple[str, dict, str]:
    """(final assistant text, usage, error) from Pi's JSON event stream. Split on LF only, as Pi's docs require."""
    text, usage, error = "", {}, ""
    for line in stdout.split("\n"):
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            ev = json.loads(line)
        except ValueError:
            continue
        if ev.get("type") == "message_end":
            msg = ev.get("message") or {}
            if msg.get("role") == "assistant":
                blocks = msg.get("content") or []
                if isinstance(blocks, str):
                    text = blocks
                else:
                    text = "".join(b.get("text", "") for b in blocks if b.get("type") == "text")
                u = msg.get("usage") or {}
                cost = u.get("cost")
                usage = {"input": int(u.get("input") or u.get("inputTokens") or 0),
                         "output": int(u.get("output") or u.get("outputTokens") or 0),
                         "cache_read": int(u.get("cacheRead") or 0), "cache_write": int(u.get("cacheWrite") or 0),
                         "cost": float(cost.get("total", 0) if isinstance(cost, dict) else cost or 0)}
                if msg.get("stopReason") == "error" or msg.get("errorMessage"):
                    error = msg.get("errorMessage") or "the model returned an error"
        elif ev.get("type") == "error":
            error = ev.get("message") or str(ev)
    return text, usage, error


# ----------------------------------------------------------------------------
# sign-in, through the bridge script
# ----------------------------------------------------------------------------

def _bridge(*args) -> subprocess.Popen:
    return subprocess.Popen([str(node_bin()), str(login_script()), *args], stdin=subprocess.PIPE,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=env(), bufsize=1)


def providers() -> list[dict]:
    p = _bridge("providers")
    out, err = p.communicate(timeout=60)
    for line in out.splitlines():
        ev = json.loads(line)
        if ev.get("event") == "providers":
            return ev["providers"]
        if ev.get("event") == "error":
            raise AIError(ev["message"])
    raise AIError(err.strip()[-300:] or "Pi did not list its providers")


def models() -> list[dict]:
    p = _bridge("models")
    out, err = p.communicate(timeout=60)
    for line in out.splitlines():
        ev = json.loads(line)
        if ev.get("event") == "models":
            return ev["models"]
    return []


def login(provider: str, method: str, on_event: Callable[[dict], str | None]) -> None:
    """Run a sign-in. `on_event` receives each event; for a "prompt" event it returns the answer (None cancels).

    Events are read on a background thread, so a prompt that is still waiting for typing can be withdrawn when
    the browser finishes the sign-in first (on_event then receives {"event": "prompt_withdrawn"})."""
    import queue
    import threading

    p = _bridge("login", provider, method)
    assert p.stdout and p.stdin
    events: queue.Queue = queue.Queue()
    withdrawn: set = set()

    def reader():
        for line in p.stdout:
            try:
                ev = json.loads(line)
            except ValueError:
                continue
            if ev.get("event") == "prompt_withdrawn":
                withdrawn.add(ev.get("id"))  # seen at once, even while a prompt is waiting for typing
            events.put(ev)
        events.put({"event": "_eof"})

    threading.Thread(target=reader, daemon=True).start()
    try:
        while True:
            ev = events.get(timeout=900)
            kind = ev.get("event")
            if kind == "_eof":
                raise AIError("sign-in ended without finishing; try again")
            if kind == "prompt":
                answer = on_event(ev | {"_withdrawn": lambda i=ev["id"]: i in withdrawn})
                if ev["id"] in withdrawn:
                    continue
                reply = {"id": ev["id"], "cancel": True} if answer is None else {"id": ev["id"], "value": answer}
                p.stdin.write(json.dumps(reply) + "\n")
                p.stdin.flush()
                continue
            on_event(ev)
            if kind == "error":
                raise AIError(ev.get("message", "sign-in failed"))
            if kind == "done":
                return
    except queue.Empty as e:
        raise AIError("sign-in timed out") from e
    finally:
        try:
            p.stdin.close()
            p.wait(timeout=25)
        except Exception:
            p.kill()


def logout(provider: str) -> None:
    p = _bridge("logout", provider)
    p.communicate(timeout=60)


def billing_of(model: str) -> str:
    """"sub" when Pi is signed in to this provider with a subscription (OAuth), "api" for a key."""
    provider = model.split("/", 1)[0] if "/" in model else ""
    try:
        auth = json.loads((agent_dir() / "auth.json").read_text(encoding="utf-8"))
        return "sub" if (auth.get(provider) or {}).get("type") == "oauth" else "api"
    except (OSError, ValueError):
        return ""
