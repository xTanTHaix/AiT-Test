"""Counterexamples that must produce zero findings (no hidden mutation)."""


def add_tag(tags: list, tag: str) -> list:
    """Return a new list instead of mutating the caller's."""
    return [*tags, tag]


def remember(item: str, bucket: list | None = None) -> list:
    """Build a fresh container when the caller does not supply one."""
    if bucket is None:
        bucket = []
    bucket.append(item)
    return bucket
