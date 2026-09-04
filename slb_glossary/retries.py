"""Configurable backoff strategies for retrying flaky API calls."""

import asyncio
import dataclasses
import enum
import logging
import math
import random
import sys
import time
import typing

if sys.version_info >= (3, 11):
    from typing import Self
else:
    from typing_extensions import Self

T = typing.TypeVar("T")

logger = logging.getLogger(__name__)


__all__ = ["DEFAULT_RETRY_POLICY", "BackoffType", "RetryPolicy", "retry"]


class BackoffType(enum.Enum):
    """A strategy for spacing out retry attempts."""

    CONSTANT = "constant"
    """Wait `base_delay` before every attempt."""

    LINEAR = "linear"
    """Wait `base_delay * attempt` before each attempt."""

    EXPONENTIAL = "exponential"
    """Wait `base_delay * factor ** (attempt - 1)` before each attempt."""

    LOGARITHMIC = "logarithmic"
    """Wait `base_delay * log(attempt + 1, factor)` before each attempt."""


@dataclasses.dataclass(frozen=True)
class RetryPolicy:
    """
    Retry policy controlling how `retry` should space out and bound its retry attempts.
    """

    attempts: int = 3
    """Maximum number of times to call the retried function."""

    base_delay: float = 800
    """Milliseconds used as the base of the backoff calculation."""

    backoff_type: BackoffType = BackoffType.EXPONENTIAL
    """Strategy used to grow the delay between attempts."""

    factor: float = 2.0
    """Growth base for `EXPONENTIAL`, or log base for `LOGARITHMIC`."""

    max_delay: float | None = 10_000.0
    """Upper bound on any single delay (milliseconds). Uncapped if `None`."""

    jitter: bool = True
    """Randomize each delay by up to +/-50% to avoid retry storms."""

    def delay_for_attempt(self, attempt: int) -> float:
        """
        Compute the delay to wait after the given attempt number.

        :param attempt: The 1-indexed attempt that just failed.
        :return: Milliseconds to wait before the next attempt.
        """
        if self.backoff_type is BackoffType.CONSTANT:
            delay = self.base_delay
        elif self.backoff_type is BackoffType.LINEAR:
            delay = self.base_delay * attempt
        elif self.backoff_type is BackoffType.EXPONENTIAL:
            delay = self.base_delay * (self.factor ** (attempt - 1))
        elif self.backoff_type is BackoffType.LOGARITHMIC:
            delay = self.base_delay * math.log(attempt + 1, self.factor)
        else:  # pragma: no cover - exhaustive over BackoffType
            raise ValueError(f"Unsupported backoff type: {self.backoff_type!r}")

        if self.max_delay is not None:
            delay = min(delay, self.max_delay)

        if self.jitter:
            delay *= random.uniform(0.5, 1.5)
        return max(delay, 0.0)

    @classmethod
    def constant(cls, base_delay: float = 800, **kwargs: typing.Any) -> Self:
        """Build a `CONSTANT` policy waiting `base_delay` between attempts."""
        return cls(base_delay=base_delay, backoff_type=BackoffType.CONSTANT, **kwargs)

    @classmethod
    def linear(cls, base_delay: float = 800, **kwargs: typing.Any) -> Self:
        """Build a `LINEAR` policy growing the delay by `base_delay` each attempt."""
        return cls(base_delay=base_delay, backoff_type=BackoffType.LINEAR, **kwargs)

    @classmethod
    def exponential(
        cls, base_delay: float = 800, factor: float = 2.0, **kwargs: typing.Any
    ) -> Self:
        """Build an `EXPONENTIAL` policy, the default and generally safest choice."""
        return cls(
            base_delay=base_delay, backoff_type=BackoffType.EXPONENTIAL, factor=factor, **kwargs
        )

    @classmethod
    def logarithmic(
        cls, base_delay: float = 800, factor: float = 2.0, **kwargs: typing.Any
    ) -> Self:
        """Build a `LOGARITHMIC` policy, for retries that should barely grow."""
        return cls(
            base_delay=base_delay, backoff_type=BackoffType.LOGARITHMIC, factor=factor, **kwargs
        )


DEFAULT_RETRY_POLICY = RetryPolicy()
"""The `RetryPolicy` used wherever a retry is not given one explicitly."""


async def retry(
    func: typing.Callable[[], typing.Awaitable[T]],
    *,
    policy: RetryPolicy = DEFAULT_RETRY_POLICY,
    until: typing.Callable[[T], bool] | None = None,
    raise_exception: bool = True,
) -> T | None:
    """
    Call `func` repeatedly, backing off per `policy`, until it is successful (no error),
    and the condition (*until*) is satisfied, if provided.

    :param func: A zero-argument async callable to retry.
    :param policy: Controls how many attempts are made and how long to wait
        between them.
    :return: The first truthy result of `func`, or its last (falsy) result
        once `policy.attempts` is exhausted.
    """
    started_at = time.monotonic()
    result: T | None = None
    error: BaseException | None = None
    attempts_made = 0
    total_delay = 0.0
    for attempt in range(1, policy.attempts + 1):
        attempts_made = attempt
        attempt_started_at = time.monotonic()
        try:
            result = await func()
            if until is None or until(result):
                logger.debug(
                    "%s succeeded on attempt %d/%d after %.3fs (total %.3fs including %.3fs of backoff)",
                    func,
                    attempt,
                    policy.attempts,
                    time.monotonic() - attempt_started_at,
                    time.monotonic() - started_at,
                    total_delay,
                )
                return result
        except (SystemExit, KeyboardInterrupt, asyncio.CancelledError):
            raise
        except BaseException as exc:
            error = exc
            logger.debug(
                "Error occurred on attempt %d/%d (%.3fs into this attempt): %s",
                attempt,
                policy.attempts,
                time.monotonic() - attempt_started_at,
                exc,
                exc_info=True,
            )

        if attempt < policy.attempts:
            delay = policy.delay_for_attempt(attempt) / 1000
            total_delay += delay
            logger.debug(
                "Attempt %d/%d failed, retrying in %.2fs (%.3fs elapsed so far)",
                attempt,
                policy.attempts,
                delay,
                time.monotonic() - started_at,
            )
            await asyncio.sleep(delay)

    elapsed = time.monotonic() - started_at
    logger.warning(
        "Gave up after %d/%d attempt(s) calling %s in %.3fs (%.3fs of that spent backing off)",
        attempts_made,
        policy.attempts,
        func,
        elapsed,
        total_delay,
    )
    if raise_exception and error is not None:
        raise error
    return result
