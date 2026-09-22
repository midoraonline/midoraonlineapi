"""Pure-logic checks for near-duplicate helpers (no FastAPI boot)."""
from __future__ import annotations

from listingModeration.stages.phash import _hamming


def test_hamming_identical():
    assert _hamming(0, 0) == 0
    assert _hamming(1, 1) == 0


def test_hamming_one_bit():
    assert _hamming(0, 1) == 1
