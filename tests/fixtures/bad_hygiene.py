"""Intentional defects for AIT-H01 / AIT-H02 (never imported by tests)."""


def parse(value: str) -> int:
    """AIT-H01: a bare except swallows every failure."""
    try:
        return int(value)
    except:
        return 0


def classify(rows, flag) -> str:
    """AIT-H02: five nested control levels."""
    for row in rows:
        for cell in row:
            if flag:
                if cell:
                    if isinstance(cell, int):
                        if cell % 2 == 0:
                            return "even"
    return "other"
