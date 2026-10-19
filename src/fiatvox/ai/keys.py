"""API keys in the macOS Keychain, never in plain files."""
from __future__ import annotations

SERVICE = "Fiat Vox"


def get(provider: str) -> str | None:
    import keyring
    try:
        return keyring.get_password(SERVICE, provider)
    except Exception:
        return None


def put(provider: str, key: str) -> None:
    import keyring
    keyring.set_password(SERVICE, provider, key.strip())


def delete(provider: str) -> None:
    import keyring
    try:
        keyring.delete_password(SERVICE, provider)
    except Exception:
        pass
