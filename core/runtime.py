"""Process/runtime helpers shared by the app factory and feature modules."""
from __future__ import annotations

import os


def is_serverless() -> bool:
    """Vercel sets these at runtime. In-process workers must not start here."""
    return bool(os.getenv("VERCEL") or os.getenv("VERCEL_ENV"))
