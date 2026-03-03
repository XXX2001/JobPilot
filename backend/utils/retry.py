"""Retry decorator for flaky async operations."""

from __future__ import annotations

import asyncio
import logging
from functools import wraps
from typing import Any, Callable, TypeVar

logger = logging.getLogger(__name__)

F = TypeVar("F", bound=Callable[..., Any])


def async_retry(
    max_attempts: int = 3,
    base_delay: float = 1.0,
    backoff_factor: float = 2.0,
    exceptions: tuple[type[Exception], ...] = (Exception,),
) -> Callable[[F], F]:
    """Decorator for retrying async functions with exponential backoff.

    Args:
        max_attempts: Maximum number of attempts.
        base_delay: Initial delay in seconds before first retry.
        backoff_factor: Multiply delay by this factor after each retry.
        exceptions: Tuple of exception types to catch and retry.
    """

    def decorator(func: F) -> F:
        @wraps(func)
        async def wrapper(*args: Any, **kwargs: Any) -> Any:
            delay = base_delay
            last_exception: Exception | None = None
            for attempt in range(1, max_attempts + 1):
                try:
                    return await func(*args, **kwargs)
                except exceptions as exc:
                    last_exception = exc
                    if attempt == max_attempts:
                        logger.error(
                            "%s failed after %d attempts: %s",
                            func.__name__,
                            max_attempts,
                            exc,
                        )
                        raise
                    logger.warning(
                        "%s attempt %d/%d failed: %s — retrying in %.1fs",
                        func.__name__,
                        attempt,
                        max_attempts,
                        exc,
                        delay,
                    )
                    await asyncio.sleep(delay)
                    delay *= backoff_factor
            raise last_exception  # type: ignore[misc]

        return wrapper  # type: ignore[return-value]

    return decorator
