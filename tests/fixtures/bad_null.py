"""Intentional defects for AIT-N01 (never imported by tests)."""

from typing import Optional


class Service:
    """AIT-N01: the client attribute is optional and dereferenced unguarded."""

    def __init__(self) -> None:
        self.client: Optional[object] = None

    def send(self, payload: str) -> None:
        self.client.send(payload)


def find(key: str) -> Optional[str]:
    """Return None so the caller below has an optional value."""
    return None


def use_producer() -> None:
    """AIT-N01: the returned Optional is dereferenced without a guard."""
    value = find("alpha")
    print(value.upper())


def use_mapping(mapping: dict) -> None:
    """AIT-N01: dict.get can return None, and the result is dereferenced."""
    value = mapping.get("key")
    print(value.upper())
