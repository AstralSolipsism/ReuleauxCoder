"""Retry strategies for LLM calls."""

import time
from typing import Callable, TypeVar, Optional
from functools import wraps

from openai import RateLimitError, APITimeoutError, APIConnectionError

T = TypeVar("T")


def with_retry(
    max_retries: int = 3,
    base_delay: float = 1.0,
    max_delay: float = 10.0,
    exceptions: tuple = (RateLimitError, APITimeoutError, APIConnectionError),
) -> Callable[[Callable[..., T]], Callable[..., T]]:
    """Decorator that adds retry logic to a function."""

    def decorator(func: Callable[..., T]) -> Callable[..., T]:
        @wraps(func)
        def wrapper(*args, **kwargs) -> T:
            last_exception: Optional[Exception] = None

            for attempt in range(max_retries):
                try:
                    return func(*args, **kwargs)
                except exceptions as e:
                    last_exception = e
                    if attempt == max_retries - 1:
                        raise

                    # Exponential backoff with cap
                    delay = min(base_delay * (2**attempt), max_delay)
                    time.sleep(delay)

            # Should never reach here, but just in case
            if last_exception:
                raise last_exception

        return wrapper

    return decorator
