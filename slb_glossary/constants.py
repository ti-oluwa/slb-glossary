"""
Environment-overridable constants for `slb_glossary`.

Every tunable constant in the package is meant to live here, as a
`Constant` descriptor on `Constants`, instead of as a bare module-level
value scattered across whichever file happens to use it first. Reach for
it through the shared `constants` instance:

```python
from slb_glossary.constants import constants

pool_size = constants.similar_terms_pool_size
```

`Constant` optionally ties a field to an environment variable, so it can
be overridden without editing code (`SLB_GLOSSARY_SIMILAR_POOL_SIZE=10`,
say). See `Constant`'s own docstring for exactly how that resolves.

Adding a new constant is one line on `Constants`:

```python
class Constants:
    ...
    my_new_constant = Constant(42, env_var="SLB_GLOSSARY_MY_NEW_CONSTANT")
```
"""

import builtins
import sys
import threading
import typing

from slb_glossary.utils import env

if sys.version_info >= (3, 11):
    from typing import Self
else:
    from typing_extensions import Self

__all__ = ["Constant", "Constants", "constants"]

T = typing.TypeVar("T")


_UNSET = object()
"""Sentinel distinguishing "never cached yet" from a legitimately falsy cached value (`0`, `False`, `""`)."""


class Constant(typing.Generic[T]):
    """
    A descriptor for one named, optionally environment-overridable constant.

    ```python
    class Constants:
        similar_terms_pool_size = Constant(5, env_var="SLB_GLOSSARY_SIMILAR_POOL_SIZE")
        relevance_threshold = Constant(
            0.55,
            env_var="SLB_GLOSSARY_RELEVANCE_THRESHOLD",
            validate=lambda v: 0.0 <= v <= 1.0,
        )
        log_format = Constant(
            "%(levelname)s  %(message)s"
        )  # no env_var: fixed, but still typed/validated
    ```

    With `env_var` given, every access re-reads that environment variable
    (via `slb_glossary.utils.env`, which handles the actual casting/
    validation) and resolves fresh, so changing the environment mid-process
    (tests, a long-running server picking up a config reload, etc.) takes
    effect on the very next access, not just at import time. Pass
    `cache=True` to resolve it once instead, on first access, and hold
    that value for the rest of the process; use this for a constant that's
    read often enough that re-parsing its environment variable every time
    would matter, or one that must stay stable once read (e.g. anything
    used to size a resource at startup and never revisited).

    Without `env_var`, a `Constant` is just `default`, always and `cache`
    has no effect, since there's nothing to re-read from.
    """

    def __init__(
        self,
        default: T,
        *,
        env_var: str | None = None,
        type: type[T] | None = None,
        validate: typing.Callable[[T], bool] | None = None,
        cache: bool = False,
    ) -> None:
        """
        Initialize a constant.

        :param default: The constant's built-in value, used whenever `env_var`
            isn't set in the environment (or isn't given at all). Also fixes
            the expected type (and so how an environment string is cast)
            unless `type` is given explicitly.
        :param env_var: Name of an environment variable that can override
            `default`. `None` (the default) means this constant is never
            read from the environment.
        :param type: Expected type to cast a raw environment string to.
            Defaults to `type(default)`. See `slb_glossary.utils.env` for
            exactly what's supported (`bool`/`int`/`float`/`str`, and `Enum`
            subclasses matched by value).
        :param validate: Optional extra check run on every environment-sourced
            value (not on `default` itself, which is trusted as correct by
            construction). A `False` return raises `slb_glossary.utils.EnvVarError`.
        :param cache: If `True`, resolve this constant once, on first access,
            and reuse that value for the rest of the process, instead of
            re-reading/re-validating its environment variable on every access.
            Ignored when `env_var` isn't given.
        """
        self.default = default
        self.env_var = env_var
        self.type = type if type is not None else builtins.type(default)
        self.validate = validate
        self.cache = cache
        self._name = ""
        self._cached: typing.Any = _UNSET
        self._lock = threading.Lock()

    def __set_name__(self, owner: type, name: str) -> None:
        self._name = name

    def _resolve(self) -> T:
        """Compute this constant's current value, ignoring any cache."""
        if self.env_var is None:
            return self.default
        return env(self.env_var, self.default, type=self.type, validate=self.validate)

    def __get__(self, instance: typing.Any, owner: type | None = None) -> T:
        if instance is None:
            # Accessed on the class itself (`Constants.similar_terms_pool_size`),
            # not an instance so we hand back the descriptor for introspection.
            return self  # type: ignore[return-value]

        if self.env_var is None or not self.cache:
            return self._resolve()

        if self._cached is _UNSET:
            with self._lock:
                if self._cached is _UNSET:
                    self._cached = self._resolve()
        return typing.cast(T, self._cached)

    def __set__(self, instance: typing.Any, value: T) -> None:
        """Override this constant's value directly (e.g. from a test), bypassing the environment."""
        if self.validate is not None and not self.validate(value):
            raise ValueError(f"{self._name!r}: {value!r} is not a valid value for this constant.")
        with self._lock:
            self._cached = value

    def reset(self) -> None:
        """Clear a cached value, so the next access re-resolves it (from `default`/the environment)."""
        with self._lock:
            self._cached = _UNSET

    def __repr__(self) -> str:
        return (
            f"{type(self).__name__}({self.default!r}, env_var={self.env_var!r}, "
            f"cache={self.cache!r})"
        )


class Constants:
    """
    Package-wide constants.

    Not meant to be instantiated directly! Import and use the shared
    `constants` instance below instead, so every constant is resolved
    (and, where `cache=True`, cached) exactly once across the whole
    process, not per-instance.
    """

    _instance: typing.ClassVar[Self | None] = None

    def __new__(cls) -> Self:
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    persist_batch_size = Constant(
        20,
        env_var="SLB_GLOSSARY_PERSIST_BATCH_SIZE",
        validate=lambda v: v >= 1,
    )
    """
    Default number of live results to buffer before writing an incremental
    upsert batch to the local database (`slb_glossary.local.upsert_results_incrementally`).
    """

    relevance_threshold = Constant(
        0.45,
        env_var="SLB_GLOSSARY_RELEVANCE_THRESHOLD",
        validate=lambda v: 0.0 <= v <= 1.0,
    )
    """
    Default `relevance_threshold` for `slb_glossary.query.search`'s
    `Source.AUTO` behavior: below this score, the local database's best
    match isn't trusted alone and a live search is added on.
    """

    similar_terms_pool_size = Constant(
        5,
        env_var="SLB_GLOSSARY_SIMILAR_POOL_SIZE",
        validate=lambda v: v >= 1,
    )
    """
    Default number of live results pulled while looking for an exact term
    match, and to draw `SimilarResult.similar` alternatives from.
    """

    max_similar_terms = Constant(
        3,
        env_var="SLB_GLOSSARY_MAX_SIMILAR_TERMS",
        validate=lambda v: v >= 0,
    )
    """Default max number of alternatives returned in `SimilarResult.similar`."""

    log_format = Constant(
        "%(levelname)s  %(asctime)s  [%(name)s.%(funcName)s:%(lineno)d]:  %(message)s",
        env_var="SLB_GLOSSARY_LOG_FORMAT",
    )
    """Default `logging.Formatter` format string used for every sink."""

    compare_concurrency = Constant(
        1,
        env_var="SLB_GLOSSARY_COMPARE_CONCURRENCY",
        validate=lambda v: v >= 1,
    )
    """Default `concurrency` for `slb_glossary.query.compare`: term lookups happen sequentially unless raised."""

    import_batch_size = Constant(
        500,
        env_var="SLB_GLOSSARY_IMPORT_BATCH_SIZE",
        validate=lambda v: v >= 1,
    )
    """
    Default number of rows `slb_glossary.local.loaders.load_file` buffers
    before writing an incremental upsert batch to the local database.
    """


constants = Constants()
"""
Shared, package-wide `Constants` instance. Import this, not `Constants`
itself.

`Constants()` always returns this same instance anyway, but importing
the instance directly makes that explicit and saves a call at every use site.
"""
