"""Counterexamples that must produce zero findings (synchronised state)."""

import threading


class Counter:
    """The lock covers every read-modify-write."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.total = 0

    def bump(self) -> None:
        with self._lock:
            self.total += 1


class Registry:
    """Only one check, performed while the lock is held."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._value = None

    def get(self):
        with self._lock:
            if self._value is None:
                self._value = "built"
        return self._value


def run_counter() -> None:
    """Start threads that all go through the locked method."""
    counter = Counter()
    workers = [threading.Thread(target=counter.bump) for _ in range(4)]
    for worker in workers:
        worker.start()
    for worker in workers:
        worker.join()
