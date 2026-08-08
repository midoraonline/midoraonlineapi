"""Stage 1: banned-keyword check.

Runs in microseconds against the in-code policy list. Kept as regex word
boundaries so 'gunny sack' doesn't match 'gun'.
"""
from __future__ import annotations

import re
from typing import Optional

from ..config import BANNED_KEYWORDS


_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    (kw, re.compile(rf"\b{re.escape(kw)}\b", re.IGNORECASE))
    for kw in BANNED_KEYWORDS
]


def check(title: str, description: str) -> Optional[str]:
    """Return the matched keyword phrase, or None if the text is clean."""
    text = f"{title}\n{description}"
    for kw, pattern in _PATTERNS:
        if pattern.search(text):
            return kw
    return None
