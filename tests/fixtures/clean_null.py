"""Counterexamples that must produce zero findings (guarded dereferences)."""

from typing import Optional


class Service:
    """The optional attribute is guarded before every use."""

    def __init__(self) -> None:
        self.client: Optional[object] = None

    def send(self, payload: str) -> None:
        if self.client is None:
            raise RuntimeError("service client is not connected")
        self.client.send(payload)

    def short_send(self, payload: str) -> None:
        if self.client is not None:
            self.client.send(payload)


def find(key: str) -> Optional[str]:
    """Return an optional value for the guarded callers below."""
    return None


def use_guard() -> None:
    """Early-exit guard protects the dereference."""
    value = find("alpha")
    if value is None:
        return
    print(value.upper())


def use_inline_guard(mapping: dict) -> None:
    """Short-circuit guard protects the dereference."""
    value = mapping.get("key")
    if value is not None and value.isupper():
        print(value)


def use_ternary(mapping: dict) -> str:
    """The expression-branch guard protects the dereference."""
    value = mapping.get("key")
    return value.upper() if value is not None else ""
