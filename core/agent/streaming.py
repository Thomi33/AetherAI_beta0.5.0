"""
streaming.py — Canal dedicado para tokens de streaming del LLM.
"""

from __future__ import annotations
import threading
from typing import Callable, Optional

_sink: Optional[Callable[[str], None]] = None
_cancel_event = threading.Event()


class InferenceCancelled(BaseException):
    """Control-flow exception that must not be swallowed by node fallbacks."""


def set_token_sink(callback: Callable[[str], None]) -> None:
    global _sink
    _sink = callback


def clear_token_sink() -> None:
    global _sink
    _sink = None


def reset_cancel() -> None:
    _cancel_event.clear()


def request_cancel() -> None:
    _cancel_event.set()


def is_cancelled() -> bool:
    return _cancel_event.is_set()


def emit_token(fragmento: str) -> None:
    if _sink is not None and fragmento:
        _sink(fragmento)