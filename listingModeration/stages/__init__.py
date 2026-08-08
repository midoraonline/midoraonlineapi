"""Individual moderation stages.

Each stage is a small, focused function or class. `pipeline.py` decides the
order and short-circuits on hits from the cheap stages before touching
Gemini.
"""
from __future__ import annotations
