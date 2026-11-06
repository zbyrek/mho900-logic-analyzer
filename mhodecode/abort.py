"""Cooperative cancellation of the running job (Abort button)."""
from __future__ import annotations

import threading

_requested = threading.Event()


class OperationAborted(Exception):
    """The user pressed Abort."""


def request() -> None:
    _requested.set()


def clear() -> None:
    _requested.clear()


def requested() -> bool:
    return _requested.is_set()


def checkpoint() -> None:
    """Raise OperationAborted if an abort is pending; called from long loops."""
    if _requested.is_set():
        raise OperationAborted()


def sleep(seconds: float) -> None:
    """Sleep that ends early with OperationAborted when an abort arrives."""
    if _requested.wait(seconds):
        raise OperationAborted()
