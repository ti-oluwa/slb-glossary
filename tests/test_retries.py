"""`RetryPolicy` delay math and the `retry()` helper's control flow."""

import asyncio

import pytest

from slb_glossary.retries import BackoffType, RetryPolicy, retry

pytestmark = pytest.mark.unit


class TestRetryPolicyDelayForAttempt:
    @pytest.mark.parametrize("attempt", [1, 2, 5])
    def test_constant_delay_is_fixed_across_attempts(self, attempt: int):
        """`CONSTANT` backoff waits `base_delay` regardless of attempt number."""
        policy = RetryPolicy.constant(base_delay=800, jitter=False, max_delay=None)
        assert policy.delay_for_attempt(attempt) == 800

    def test_linear_delay_grows_by_base_delay_per_attempt(self):
        """`LINEAR` backoff grows by `base_delay` per attempt."""
        policy = RetryPolicy.linear(base_delay=100, jitter=False, max_delay=None)
        assert policy.delay_for_attempt(1) == 100
        assert policy.delay_for_attempt(2) == 200
        assert policy.delay_for_attempt(3) == 300

    def test_exponential_delay_grows_by_factor_per_attempt(self):
        """`EXPONENTIAL` backoff grows by `base_delay * factor ** (attempt - 1)`."""
        policy = RetryPolicy.exponential(base_delay=100, factor=2.0, jitter=False, max_delay=None)
        assert policy.delay_for_attempt(1) == 100
        assert policy.delay_for_attempt(2) == 200
        assert policy.delay_for_attempt(3) == 400

    def test_logarithmic_delay_grows_by_log_of_attempt(self):
        """`LOGARITHMIC` backoff grows by `base_delay * log(attempt + 1, factor)`."""
        import math

        policy = RetryPolicy.logarithmic(base_delay=100, factor=2.0, jitter=False, max_delay=None)
        for attempt in (1, 2, 5):
            expected = 100 * math.log(attempt + 1, 2.0)
            assert policy.delay_for_attempt(attempt) == pytest.approx(expected)

    def test_delay_is_capped_at_max_delay(self):
        """A delay that would exceed `max_delay` is capped to it."""
        policy = RetryPolicy.exponential(
            base_delay=1000, factor=10.0, jitter=False, max_delay=5000
        )
        assert policy.delay_for_attempt(5) == 5000

    def test_max_delay_none_means_uncapped(self):
        """`max_delay=None` leaves an exponentially growing delay uncapped."""
        policy = RetryPolicy.exponential(
            base_delay=1000, factor=10.0, jitter=False, max_delay=None
        )
        assert policy.delay_for_attempt(5) == 1000 * 10.0**4

    def test_jitter_off_gives_deterministic_delay(self):
        """`jitter=False` returns the exact, unrandomized delay."""
        policy = RetryPolicy.constant(base_delay=500, jitter=False, max_delay=None)
        assert policy.delay_for_attempt(1) == 500
        assert policy.delay_for_attempt(1) == 500

    def test_jitter_on_stays_within_plus_minus_50_percent(self, monkeypatch):
        """`jitter=True` scales the (already-capped) delay by a factor in `[0.5, 1.5]`."""
        monkeypatch.setattr("random.uniform", lambda a, b: 1.25)
        policy = RetryPolicy.constant(base_delay=400, jitter=True, max_delay=None)
        assert policy.delay_for_attempt(1) == pytest.approx(400 * 1.25)

    def test_delay_never_negative(self):
        """`delay_for_attempt` never returns a value below zero."""
        policy = RetryPolicy.constant(base_delay=0, jitter=False, max_delay=None)
        assert policy.delay_for_attempt(1) >= 0.0


class TestRetryPolicyConstructors:
    @pytest.mark.parametrize(
        ("constructor", "expected_backoff"),
        [
            (RetryPolicy.constant, BackoffType.CONSTANT),
            (RetryPolicy.linear, BackoffType.LINEAR),
            (RetryPolicy.exponential, BackoffType.EXPONENTIAL),
            (RetryPolicy.logarithmic, BackoffType.LOGARITHMIC),
        ],
    )
    def test_constant_classmethod_sets_backoff_type(self, constructor, expected_backoff):
        """Each shortcut constructor sets the matching `BackoffType`."""
        policy = constructor()
        assert policy.backoff_type is expected_backoff


@pytest.fixture
def anyio_backend(anyio_backend_asyncio_only):
    """`retry()` calls raw `asyncio.sleep` internally, so it isn't trio-safe either."""
    return anyio_backend_asyncio_only


@pytest.mark.anyio
class TestRetry:
    async def test_returns_first_successful_result_without_retrying_further(self):
        """A func that succeeds on attempt 1 is only called once."""
        calls = 0

        async def func():
            nonlocal calls
            calls += 1
            return "ok"

        result = await retry(func, policy=RetryPolicy.constant(base_delay=0))
        assert result == "ok"
        assert calls == 1

    async def test_retries_until_success_within_attempts_budget(self):
        """A func that fails twice then succeeds is called exactly 3 times."""
        calls = 0

        async def func():
            nonlocal calls
            calls += 1
            if calls < 3:
                raise ValueError("not yet")
            return "ok"

        result = await retry(func, policy=RetryPolicy.constant(base_delay=0, attempts=5))
        assert result == "ok"
        assert calls == 3

    async def test_gives_up_after_attempts_exhausted_and_reraises_by_default(self):
        """Once `policy.attempts` is exhausted, the last error is reraised."""

        async def func():
            raise ValueError("always fails")

        with pytest.raises(ValueError, match="always fails"):
            await retry(func, policy=RetryPolicy.constant(base_delay=0, attempts=3))

    async def test_raise_exception_false_returns_last_falsy_result_instead_of_raising(self):
        """`raise_exception=False` returns the last (falsy) result instead of raising."""

        async def func():
            raise ValueError("boom")

        result = await retry(
            func,
            policy=RetryPolicy.constant(base_delay=0, attempts=2),
            raise_exception=False,
        )
        assert result is None

    async def test_until_predicate_controls_when_a_result_counts_as_success(self):
        """A falsy-but-no-exception result keeps retrying until `until` passes."""
        calls = 0

        async def func():
            nonlocal calls
            calls += 1
            return calls

        result = await retry(
            func,
            policy=RetryPolicy.constant(base_delay=0, attempts=5),
            until=lambda value: value >= 3,
        )
        assert result == 3
        assert calls == 3

    @pytest.mark.parametrize(
        "exception_type", [SystemExit, KeyboardInterrupt, asyncio.CancelledError]
    )
    async def test_system_exit_and_keyboard_interrupt_and_cancelled_error_propagate_immediately_without_retry(
        self, exception_type
    ):
        """`SystemExit`/`KeyboardInterrupt`/`asyncio.CancelledError` propagate immediately, without retry."""
        calls = 0

        async def func():
            nonlocal calls
            calls += 1
            raise exception_type("stop")

        with pytest.raises(exception_type):
            await retry(func, policy=RetryPolicy.constant(base_delay=0, attempts=5))
        assert calls == 1
