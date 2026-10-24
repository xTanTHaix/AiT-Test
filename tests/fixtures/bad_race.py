"""Intentional defects for AIT-R01 / AIT-R02 (never imported by tests)."""

import threading


class Counter:
    """AIT-R01: the lock exists but the increment ignores it."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.total = 0

    def bump(self) -> None:
        self.total += 1


class Registry:
    """AIT-R02: state is checked before the lock and rechecked inside it."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._value = None

    def get(self):
        if self._value is None:
            with self._lock:
                if self._value is None:
                    self._value = "built"
        return self._value


def run_counter() -> None:
    """Start four threads that all share one Counter instance."""
    counter = Counter()
    workers = [
        threading.Thread(target=counter.bump) for _ in range(4)
    ]
    for worker in workers:
        worker.start()
    for worker in workers:
        worker.join()
