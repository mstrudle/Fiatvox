"""Claude Code, the way the old pipeline used it: the user's own installed and signed-in `claude` command, run
non-interactively. Requests count against the user's Claude plan, as any Claude Code use does.

Fiat Vox implements no Claude login of its own: the user installs Claude Code and signs in to it with Anthropic's
own login. Fiat Vox only runs `claude -p` with tools, MCP servers and session history turned off.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

from .base import AIError, Usage

CANDIDATES = [Path.home() / ".local" / "bin" / "claude", Path.home() / ".claude" / "local" / "claude",
              Path("/opt/homebrew/bin/claude"), Path("/usr/local/bin/claude")]
MODELS = ["sonnet", "opus", "haiku"]   # Claude Code's own aliases for the current models


def find_claude() -> str | None:
    found = shutil.which("claude")
    if found:
        return found
    return next((str(p) for p in CANDIDATES if p.exists()), None)


def _env() -> dict:
    e = dict(os.environ)
    # an API key in the environment would make Claude Code bill the API instead of the plan
    for k in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_BASE_URL"):
        e.pop(k, None)
    # when Fiat Vox itself runs inside a Claude Code session, the host session's variables would make this
    # `claude` use the host's credentials relay instead of the user's own sign-in
    for k in list(e):
        if k == "CLAUDECODE" or k.startswith(("CLAUDE_CODE_", "CLAUDE_AGENT_SDK_")) or k in ("CLAUDE_PID", "CLAUDE_EFFORT"):
            e.pop(k)
    return e


SIGN_IN_HELP = "Open Terminal, run `claude`, type /login and sign in with your Claude account, then try again."


class ClaudeCodeBackend:
    name = "claude-code"

    def __init__(self, model: str = ""):
        self.model = model or "sonnet"
        self.label = f"Claude Code ({self.model})"
        self.usage = Usage(billing="sub")

    def available(self) -> tuple[bool, str]:
        if not find_claude():
            return False, "Claude Code is not installed. Install it from claude.com/claude-code, sign in, then try again."
        return True, ""

    def command(self) -> list[str]:
        return [find_claude() or "claude", "-p", "--output-format", "json", "--tools", "", "--strict-mcp-config",
                "--no-session-persistence", "--model", self.model]

    def complete(self, prompt: str, *, purpose: str, timeout: int = 900) -> str:
        if not find_claude():
            raise AIError(self.available()[1])
        with tempfile.TemporaryDirectory(prefix="fiatvox-cc-") as cwd:
            try:
                r = subprocess.run(self.command(), input=prompt, capture_output=True, text=True, env=_env(),
                                   cwd=cwd, timeout=timeout)
            except subprocess.TimeoutExpired as e:
                raise AIError(f"Claude Code did not answer within {timeout} seconds") from e
        try:
            d = json.loads(r.stdout)
        except ValueError:
            raise AIError(f"Claude Code failed: {(r.stderr or r.stdout).strip()[-400:]}")
        if d.get("is_error"):
            msg = str(d.get("result") or d.get("subtype") or "unknown error")
            if "authenticate" in msg.lower() or "login" in msg.lower() or "oauth" in msg.lower():
                msg += ". " + SIGN_IN_HELP
            raise AIError(f"Claude Code: {msg}")
        self.usage.add(purpose, **usage_of(d))
        return str(d.get("result") or "")


def usage_of(d: dict) -> dict:
    """Tokens and cost from Claude Code's JSON result."""
    u = d.get("usage") or {}
    return {"inp": int(u.get("input_tokens") or 0), "out": int(u.get("output_tokens") or 0),
            "cache_read": int(u.get("cache_read_input_tokens") or 0),
            "cache_write": int(u.get("cache_creation_input_tokens") or 0),
            "cost": float(d.get("total_cost_usd") or 0.0)}
