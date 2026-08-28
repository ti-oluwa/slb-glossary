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

    Regardless of `cache`, an explicit `instance.constant_name = value`
    assignment always overrides every future read, bypassing both
    `default` and the environment, until `reset()` is called. `cache`
    only governs how a *non-overridden* constant resolves from the
    environment (fresh every time, or once and held).
    """

    def __init__(
        self,
        default: T,
        *,
        env_var: str | None = None,
        type: type[T] | typing.Callable[[str], T] | None = None,
        validator: typing.Callable[[T], bool] | None = None,
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
            or a callable to cast the string to the correct type.
            Defaults to `type(default)`. See `slb_glossary.utils.env` for
            exactly what's supported (`bool`/`int`/`float`/`str`, and `Enum`
            subclasses matched by value).
        :param validator: Optional extra check run on every environment-sourced
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
        self.validator = validator
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
        return env(self.env_var, self.default, type=self.type, validator=self.validator)

    def __get__(self, instance: typing.Any, owner: type | None = None) -> T:
        if instance is None:
            # Accessed on the class itself (`Constants.similar_terms_pool_size`),
            # not an instance so we hand back the descriptor for introspection.
            return self  # type: ignore[return-value]

        # An explicit `__set__` override always wins, `cache` or not, as
        # that's the whole point of `__set__` ("bypassing the
        # environment"). Only `reset()` clears this back to `_UNSET`,
        # re-enabling normal env/default resolution below.
        if self._cached is not _UNSET:
            return typing.cast(T, self._cached)

        if self.env_var is None or not self.cache:
            return self._resolve()

        with self._lock:
            if self._cached is _UNSET:
                self._cached = self._resolve()
        return typing.cast(T, self._cached)

    def __set__(self, instance: typing.Any, value: T) -> None:
        """
        Override this constant's value directly (e.g. from a test), bypassing the environment.

        Takes effect immediately and for every subsequent read regardless
        of this `Constant`'s `cache` setting . `cache` only governs how a
        *non-overridden* value resolves from the environment, not whether
        an explicit override is honored. Call `reset()` to remove the
        override and go back to reading `default`/the environment.
        """
        if self.validator is not None and not self.validator(value):
            raise ValueError(f"{self._name!r}: {value!r} is not a valid value for this constant.")
        with self._lock:
            self._cached = value

    def reset(self) -> None:
        """
        Clear this constant back to normal resolution.

        Removes both an explicit `__set__` override, if any, and any
        cached env-resolved value (for a `cache=True` constant).

        Either way, the next access re-resolves fresh from `default`/the
        environment, and (for `cache=True`) caches that fresh result
        again from there.
        """
        with self._lock:
            self._cached = _UNSET

    def __repr__(self) -> str:
        return (
            f"{type(self).__name__}({self.default!r}, env_var={self.env_var!r}, "
            f"cache={self.cache!r})"
        )


SEARCH_MODES = frozenset(("lexical", "semantic", "hybrid"))


class Constants:
    """
    Package-wide constants.

    **Not meant to be instantiated directly!** Import and use the shared
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
        validator=lambda v: v >= 1,
    )
    """
    Default number of live results to buffer before writing an incremental
    upsert batch to the local database (`slb_glossary.local.upsert_results_incrementally`).
    """

    relevance_threshold = Constant(
        0.45,
        env_var="SLB_GLOSSARY_RELEVANCE_THRESHOLD",
        validator=lambda v: 0.0 <= v <= 1.0,
    )
    """
    Default `relevance_threshold` for `slb_glossary.query.search`'s
    `Source.AUTO` behavior: below this score, the local database's best
    match isn't trusted alone and a live search is added on.
    """

    similar_terms_pool_size = Constant(
        5,
        env_var="SLB_GLOSSARY_SIMILAR_POOL_SIZE",
        validator=lambda v: v >= 1,
    )
    """
    Default number of live results pulled while looking for an exact term
    match, and to draw `SimilarResult.similar` alternatives from.
    """

    max_similar_terms = Constant(
        3,
        env_var="SLB_GLOSSARY_MAX_SIMILAR_TERMS",
        validator=lambda v: v >= 0,
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
        validator=lambda v: v >= 1,
    )
    """Default `concurrency` for `slb_glossary.query.compare`: term lookups happen sequentially unless raised."""

    import_batch_size = Constant(
        500,
        env_var="SLB_GLOSSARY_IMPORT_BATCH_SIZE",
        validator=lambda v: v >= 1,
    )
    """
    Default number of rows `slb_glossary.local.loaders.load_file` buffers
    before writing an incremental upsert batch to the local database.
    """

    export_batch_size = Constant(
        500,
        env_var="SLB_GLOSSARY_EXPORT_BATCH_SIZE",
        validator=lambda v: v >= 1,
    )
    """
    Default number of rows `slb_glossary.local.iter_terms` reads from the
    database per batch while streaming a local export, instead of
    loading every matching row into memory before yielding the first one.
    """

    exact_match_score = Constant(
        1.0,
        env_var="SLB_GLOSSARY_EXACT_MATCH_SCORE",
        validator=lambda v: 0.0 <= v <= 1.0,
    )
    """
    Score for a query that exactly matches a result's term name (case/
    whitespace-insensitive). Used by both `slb_glossary.local.lexical_search`
    and `slb_glossary.live.relevance`.
    """

    prefix_match_score = Constant(
        0.9,
        env_var="SLB_GLOSSARY_PREFIX_MATCH_SCORE",
        validator=lambda v: 0.0 <= v <= 1.0,
    )
    """
    Score for a result's term name starting with the query. Used by both
    `slb_glossary.local.lexical_search` and `slb_glossary.live.relevance`.
    """

    content_match_score_cap = Constant(
        0.40,
        env_var="SLB_GLOSSARY_CONTENT_MATCH_SCORE_CAP",
        validator=lambda v: 0.0 <= v <= 1.0,
    )
    """
    Upper bound on a result's score when it only matched by content
    (definition/topic text), never the term name, kept below
    `relevance_threshold` so that kind of match never reads as confident
    as an actual name match. Used by `slb_glossary.local.lexical_search`
    and `slb_glossary.live.relevance`'s lexical scoring; not applied to
    semantic or hybrid scoring, which have their own natural scale.
    """

    embedding_model = Constant(
        "minishlab/potion-retrieval-32M",
        env_var="SLB_GLOSSARY_EMBEDDING_MODEL",
    )
    """
    Hugging Face repo id of the `model2vec` model that embeds terms for
    semantic search (`slb_glossary.local.embeembeddedd_terms`/`vector_search`/
    `hybrid_search`). Downloaded once, then cached locally; no network
    call happens per query.

    Changing this needs a re-embed of every locally stored term
    (`embed_terms(db, only_missing=False)`), and, if the model's output
    size differs, `embedding_dim` updated to match and every old vector
    cleared first (`slb_glossary.local.delete_embeddings`).
    """

    embedding_dim = Constant(
        512,
        env_var="SLB_GLOSSARY_EMBEDDING_DIM",
        validator=lambda v: v > 0,
    )
    """Output size of `embedding_model`'s vectors. Keep this matched to that model exactly."""

    rrf_k = Constant(
        60,
        env_var="SLB_GLOSSARY_RRF_K",
        validator=lambda v: v > 0,
    )
    """
    The `k` constant in reciprocal rank fusion (`weight / (k + rank)`),
    used by `slb_glossary.local.hybrid_search` to combine lexical and
    semantic result rankings. 60 is the standard default most hybrid
    search implementations use. Lower weighs top ranks more heavily;
    higher flattens the difference between them.
    """

    lexical_weight = Constant(
        1.0,
        env_var="SLB_GLOSSARY_LEXICAL_WEIGHT",
        validator=lambda v: v >= 0.0,
    )
    """Weight given to the bm25 ranking in `slb_glossary.local.hybrid_search`'s RRF combination."""

    semantic_weight = Constant(
        1.0,
        env_var="SLB_GLOSSARY_SEMANTIC_WEIGHT",
        validator=lambda v: v >= 0.0,
    )
    """Weight given to the vector ranking in `slb_glossary.local.hybrid_search`'s RRF combination."""

    session_auto_initialize = Constant(
        False,
        env_var="SLB_GLOSSARY_SESSION_AUTO_INITIALIZE",
    )
    """
    Default for `open_session`/`session`'s `initialize` parameter when
    it's left as `None` (the default there too). `False` means a session
    comes back immediately without loading topics/size (lazy), which
    search functions will raise `SessionNotInitializedError` for until
    `session.initialize()` is called. 
    
    Useful because that load is one of the more expensive parts of 
    opening a session, and is wasted work for a caller that's about 
    to check the local database first and only fall back to a live 
    session on a miss. Set to `True` (or pass `initialize=True` explicitly 
    at the call site) to always load eagerly instead.
    """

    hybrid_candidate_pool = Constant(
        50,
        env_var="SLB_GLOSSARY_HYBRID_CANDIDATE_POOL",
        validator=lambda v: v >= 1,
    )
    """
    Candidates pulled from each of the lexical and semantic rankers
    before `slb_glossary.local.hybrid_search` fuses and truncates them
    to the caller's actual `limit`. Higher lets a result ranked outside
    the top few by one ranker still surface if the other ranks it
    highly, at the cost of more work per search.
    """

    hybrid_overfetch_factor = Constant(
        4,
        env_var="SLB_GLOSSARY_HYBRID_OVERFETCH_FACTOR",
        validator=lambda v: v >= 1,
    )
    """
    Multiplier `slb_glossary.local.vector_search` applies to its nearest-
    neighbor request before filtering by topic/language/exclude, since
    the vector database applies its own result cap before those filters
    can run, and asking for exactly as many neighbors as needed can come
    up short once they're applied. Raise this if a `vector_search` result
    that should be findable is going missing under a topic/language filter.
    """

    embed_batch_size = Constant(
        64,
        env_var="SLB_GLOSSARY_EMBED_BATCH_SIZE",
        validator=lambda v: v >= 1,
    )
    """Terms embedded per model call in `slb_glossary.local.embed_terms`."""

    default_search_mode = Constant(
        "lexical",
        env_var="SLB_GLOSSARY_DEFAULT_SEARCH_MODE",
        validator=lambda v: v in SEARCH_MODES,
    )
    """
    Default `mode` for `slb_glossary.local.search`/the `search` CLI
    command when the caller doesn't pass one explicitly. One of
    `"lexical"` (the default), `"semantic"`, or `"hybrid"`; see
    `slb_glossary.types.SearchMode`.

    `"lexical"` needs nothing beyond the base install. `"semantic"`/`"hybrid"`
    need the `semantic` extra installed and terms already embedded
    (`slb_glossary.local.embed_terms`); set this to one of those once a
    database is embedded, it generally ranks better.
    """

    check_internet_before_live = Constant(
        True,
        env_var="SLB_GLOSSARY_CHECK_INTERNET_BEFORE_LIVE",
    )
    """
    Whether `slb_glossary.query`'s `Source.AUTO` functions check
    `slb_glossary.network.has_internet_connection` before attempting a
    live fetch, when both a local database and a live session are
    available. When `True` (the default), if no internet is detected, the
    call logs a warning and serves local results only, skipping the live
    attempt entirely. 
    
    This is much cheaper than opening a browser and waiting
    out a full navigation timeout only to hit a `NetworkError`. Set to
    `False` to always attempt live regardless (e.g. if the check itself
    is unreliable on your network, such as one that blocks the probe
    targets but still reaches the glossary site fine through a proxy).
    Has no effect on `Source.LOCAL`/`Source.LIVE` calls, which never had
    a choice to begin with.
    """

    internet_check_timeout = Constant(
        2.0,
        env_var="SLB_GLOSSARY_INTERNET_CHECK_TIMEOUT",
        validator=lambda v: v > 0.0,
    )
    """
    Seconds `slb_glossary.network.has_internet_connection` waits for each
    probe target before giving up on it. Also the check's worst-case
    total wall time, since every target is probed concurrently, not one
    after another.
    """

    internet_check_cache_ttl = Constant(
        15.0,
        env_var="SLB_GLOSSARY_INTERNET_CHECK_CACHE_TTL",
        validator=lambda v: v >= 0.0,
    )
    """
    Seconds `slb_glossary.network.has_internet_connection` reuses its
    last result for, instead of probing again, when called with its
    default `use_cache=True`. `0` disables caching and every call probes fresh.
    """


constants = Constants()
"""
Shared, package-wide `Constants` instance. 

Use this, not `Constants` itself. `Constants()` always returns this same 
instance anyway, but importing the instance directly makes that explicit 
and saves a call at every use site.
"""
