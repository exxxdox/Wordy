from __future__ import annotations

from typing import TypedDict


class AudioIdentity(TypedDict):
    name: str
    host_api_name: str | None


def normalize_identity(name: str, host_api_name: str | None = None) -> AudioIdentity:
    host = host_api_name if host_api_name else None
    return {"name": name, "host_api_name": host}