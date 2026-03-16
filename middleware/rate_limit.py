"""Optional rate limiting. Install slowapi and add to app: RateLimitMiddleware or use slowapi's Limiter."""
from fastapi import FastAPI

# Example: from slowapi import Limiter; limiter = Limiter(key_func=get_remote_address)
# Then app.state.limiter = limiter; app.add_exception_handler(RateLimitExceeded, ...)


def register_rate_limit(app: FastAPI) -> None:
    """Register rate limit middleware. No-op if slowapi not installed."""
    try:
        from slowapi import Limiter, _rate_limit_exceeded_handler
        from slowapi.util import get_remote_address
        from slowapi.errors import RateLimitExceeded
        limiter = Limiter(key_func=get_remote_address)
        app.state.limiter = limiter
        app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)
    except ImportError:
        pass
