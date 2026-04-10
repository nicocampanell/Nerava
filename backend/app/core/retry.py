"""
Retry utility with exponential backoff for external API calls.

Provides decorator and helper functions for retrying failed API calls
with exponential backoff and jitter.
"""
import asyncio
import logging
import random
from functools import wraps
from typing import Callable, TypeVar

import httpx

logger = logging.getLogger(__name__)

T = TypeVar('T')


def should_retry_error(error: Exception) -> bool:
    """
    Determine if an error should be retried.
    
    Retries on:
    - Timeout errors
    - 5xx server errors
    - Network errors
    
    Does NOT retry on:
    - 4xx client errors (bad request, unauthorized, etc.)
    - Other application errors
    """
    if isinstance(error, httpx.TimeoutException):
        return True
    
    if isinstance(error, httpx.HTTPStatusError):
        status_code = error.response.status_code
        # Retry on 5xx errors, not 4xx
        return 500 <= status_code < 600
    
    if isinstance(error, (httpx.NetworkError, httpx.ConnectError)):
        return True
    
    return False


async def retry_with_backoff(
    func: Callable[..., T],
    max_attempts: int = 3,
    initial_delay: float = 1.0,
    max_delay: float = 60.0,
    exponential_base: float = 2.0,
    jitter: bool = True,
    *args,
    **kwargs
) -> T:
    """
    Retry a function with exponential backoff.
    
    Args:
        func: Async function to retry
        max_attempts: Maximum number of attempts (default: 3)
        initial_delay: Initial delay in seconds (default: 1.0)
        max_delay: Maximum delay in seconds (default: 60.0)
        exponential_base: Base for exponential backoff (default: 2.0)
        jitter: Add random jitter to delays (default: True)
        *args, **kwargs: Arguments to pass to func
    
    Returns:
        Result of func if successful
    
    Raises:
        Last exception if all attempts fail
    """
    last_exception = None
    
    for attempt in range(1, max_attempts + 1):
        try:
            return await func(*args, **kwargs)
        except Exception as e:
            last_exception = e
            
            # Check if we should retry this error
            if not should_retry_error(e):
                logger.debug(f"Error {e} is not retryable, stopping")
                raise
            
            # Don't retry on last attempt
            if attempt >= max_attempts:
                logger.warning(f"Max attempts ({max_attempts}) reached, giving up")
                break
            
            # Calculate delay with exponential backoff
            delay = min(initial_delay * (exponential_base ** (attempt - 1)), max_delay)
            
            # Add jitter if enabled
            if jitter:
                jitter_amount = delay * 0.1 * random.random()
                delay += jitter_amount
            
            logger.info(
                f"Attempt {attempt}/{max_attempts} failed: {e}. "
                f"Retrying in {delay:.2f}s"
            )
            
            await asyncio.sleep(delay)
    
    # All attempts failed
    raise last_exception


def retry_sync_with_backoff(
    func: Callable[..., T],
    max_attempts: int = 3,
    initial_delay: float = 1.0,
    max_delay: float = 60.0,
    exponential_base: float = 2.0,
    jitter: bool = True,
    *args,
    **kwargs
) -> T:
    """
    Retry a synchronous function with exponential backoff.
    
    Same as retry_with_backoff but for sync functions.
    Uses time.sleep instead of asyncio.sleep.
    """
    import time
    
    last_exception = None
    
    for attempt in range(1, max_attempts + 1):
        try:
            return func(*args, **kwargs)
        except Exception as e:
            last_exception = e
            
            # Check if we should retry this error
            if not should_retry_error(e):
                logger.debug(f"Error {e} is not retryable, stopping")
                raise
            
            # Don't retry on last attempt
            if attempt >= max_attempts:
                logger.warning(f"Max attempts ({max_attempts}) reached, giving up")
                break
            
            # Calculate delay with exponential backoff
            delay = min(initial_delay * (exponential_base ** (attempt - 1)), max_delay)
            
            # Add jitter if enabled
            if jitter:
                jitter_amount = delay * 0.1 * random.random()
                delay += jitter_amount
            
            logger.info(
                f"Attempt {attempt}/{max_attempts} failed: {e}. "
                f"Retrying in {delay:.2f}s"
            )
            
            time.sleep(delay)
    
    # All attempts failed
    raise last_exception


def retry_async(max_attempts: int = 3, initial_delay: float = 1.0):
    """
    Decorator for async functions with retry logic.
    
    Usage:
        @retry_async(max_attempts=3)
        async def my_api_call():
            ...
    """
    def decorator(func: Callable[..., T]) -> Callable[..., T]:
        @wraps(func)
        async def wrapper(*args, **kwargs) -> T:
            return await retry_with_backoff(
                func,
                max_attempts=max_attempts,
                initial_delay=initial_delay,
                *args,
                **kwargs
            )
        return wrapper
    return decorator


def retry_sync(max_attempts: int = 3, initial_delay: float = 1.0):
    """
    Decorator for sync functions with retry logic.
    
    Usage:
        @retry_sync(max_attempts=3)
        def my_api_call():
            ...
    """
    def decorator(func: Callable[..., T]) -> Callable[..., T]:
        @wraps(func)
        def wrapper(*args, **kwargs) -> T:
            return retry_sync_with_backoff(
                func,
                max_attempts=max_attempts,
                initial_delay=initial_delay,
                *args,
                **kwargs
            )
        return wrapper
    return decorator







