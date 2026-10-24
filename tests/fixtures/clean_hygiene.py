"""Counterexamples that must produce zero findings (narrow, shallow code)."""


def parse(value: str) -> int:
    """Narrow handler keeps interrupts propagating."""
    try:
        return int(value)
    except ValueError:
        return 0


def classify(rows, flag) -> str:
    """Three control levels stay inside the budget."""
    for row in rows:
        if flag:
            for cell in row:
                if cell:
                    return "found"
    return "other"
