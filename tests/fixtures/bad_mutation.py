"""Intentional defects for AIT-M01 / AIT-M02 (never imported by tests)."""


def add_tag(tags: list, tag: str) -> None:
    """AIT-M01: the caller's list is mutated in place."""
    tags.append(tag)


def remember(item: str, bucket: list = []) -> list:
    """AIT-M02: the default list survives between calls."""
    bucket.append(item)
    return bucket
