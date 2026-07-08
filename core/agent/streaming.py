"""
streaming.py — Canal dedicado para tokens de streaming del LLM.
"""

from __future__ import annotations
from typing import Callable, Optional

_sink: Optional[Callable[[str], None]] = None


def set_token_sink(callback: Callable[[str], None]) -> None:
    global _sink
    _sink = callback


def clear_token_sink() -> None:
    global _sink
    _sink = None


def emit_token(fragmento: str) -> None:
    if _sink is not None and fragmento:
        _sink(fragmento)