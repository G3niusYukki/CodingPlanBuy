import asyncio
import logging
from collections.abc import Callable, Coroutine
from dataclasses import dataclass
from enum import Enum
from typing import Any

logger = logging.getLogger(__name__)


class TerminalError(Exception):
    """Error that should not be retried — e.g. wrong selectors, auth failure."""
    pass


class ErrorCategory(Enum):
    RETRYABLE = "retryable"
    TERMINAL = "terminal"


TERMINAL_PATTERNS = [
    # English
    "sold out", "unavailable", "auth expired", "forbidden", "captcha",
    "session expired", "not logged in",
    # Chinese
    "已售罄", "售罄", "已售完", "暂不可购买", "暂无库存",
    "登录过期", "未登录", "验证码",
    # Selector failures (deterministic — retrying won't help)
    "not found or disabled",
]


@dataclass
class RetryConfig:
    max_retries: int = 5
    base_delay: float = 1.0
    max_delay: float = 30.0
    retryable_exceptions: tuple[type[Exception], ...] = (
        TimeoutError,
        ConnectionError,
        OSError,
    )


@dataclass
class RetryResult:
    success: bool
    attempts: int
    last_error: Exception | None = None
    value: Any = None


def classify_error(error: Exception) -> ErrorCategory:
    # TerminalError is always terminal
    if isinstance(error, TerminalError):
        return ErrorCategory.TERMINAL
    error_msg = str(error).lower()
    for pattern in TERMINAL_PATTERNS:
        if pattern.lower() in error_msg:
            return ErrorCategory.TERMINAL
    return ErrorCategory.RETRYABLE


async def retry_async(
    func: Callable[..., Coroutine],
    config: RetryConfig | None = None,
    on_retry: Callable[[int, Exception], Any] | None = None,
    **kwargs,
) -> RetryResult:
    cfg = config or RetryConfig()
    last_error: Exception | None = None

    for attempt in range(1, cfg.max_retries + 1):
        try:
            value = await func(**kwargs)
            return RetryResult(success=True, attempts=attempt, value=value)
        except TerminalError as e:
            logger.error(f"[Attempt {attempt}] Terminal error: {e}")
            return RetryResult(success=False, attempts=attempt, last_error=e)
        except Exception as e:
            last_error = e

            # Non-retryable exception type? Skip retry.
            if not isinstance(e, cfg.retryable_exceptions):
                category = classify_error(e)
                if category == ErrorCategory.TERMINAL:
                    logger.error(f"[Attempt {attempt}] Terminal error: {e}")
                    return RetryResult(success=False, attempts=attempt, last_error=e)

            if attempt >= cfg.max_retries:
                logger.error(f"[Attempt {attempt}/{cfg.max_retries}] Max retries exceeded: {e}")
                return RetryResult(success=False, attempts=attempt, last_error=e)

            delay = min(cfg.base_delay * (2 ** (attempt - 1)), cfg.max_delay)
            logger.warning(f"[Attempt {attempt}/{cfg.max_retries}] Retrying in {delay:.1f}s: {e}")

            if on_retry:
                on_retry(attempt, e)

            await asyncio.sleep(delay)

    return RetryResult(success=False, attempts=cfg.max_retries, last_error=last_error)
