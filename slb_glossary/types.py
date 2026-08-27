"""Core data types and structures."""

import dataclasses
import enum
import typing
from collections.abc import AsyncIterable, Iterable, Sequence

__all__ = [
    "Language",
    "RecordLike",
    "RelatedTerm",
    "SearchMode",
    "SearchResult",
    "Updatable",
    "materialize_records",
]


class SearchMode(enum.StrEnum):
    """
    Ranking strategy for searching or scoring glossary terms, local or live.

    `LEXICAL` (the default) needs nothing beyond the base install.
    `SEMANTIC`/`HYBRID` need the `semantic` extra installed.

    `HYBRID` also needs a local database with terms already embedded via
    `slb_glossary.local.embed_terms`, and isn't available for live
    results at all (see `slb_glossary.live.score_result`).
    """

    LEXICAL = "lexical"
    """Bm25 (local) or token-overlap (live) ranking only."""

    SEMANTIC = "semantic"
    """Embedding similarity ranking only."""

    HYBRID = "hybrid"
    """Lexical and semantic ranking, fused. Local only."""


@typing.runtime_checkable
class RecordLike(typing.Protocol):
    """Interface for a record like datastructure."""

    @property
    def fields(self) -> Sequence[str]:
        """Return a list of the field names in this record."""
        ...

    def asdict(self) -> dict[str, typing.Any]:
        """Return a dict mapping each field name to its value in this record."""
        ...


RecordT = typing.TypeVar("RecordT", bound=RecordLike)


async def materialize_records(
    records: Iterable[RecordT] | AsyncIterable[RecordT],
) -> list[RecordT]:
    """
    Collect `records` into a list, consuming it if it is a lazy iterable.

    :param records: A sync iterable, or an async iterable such as the
        generators `slb_glossary.query` yields results from.
    :return: `records` as a plain list.
    """
    if isinstance(records, AsyncIterable):
        return [record async for record in records]
    return list(records)


class Language(enum.Enum):
    """A language edition of the SLB glossary."""

    ENGLISH = "en"
    SPANISH = "es"


class RelatedTerm(typing.NamedTuple):
    """A single term linked from within another term's definition."""

    term: str
    """Display text of the link - usually the related term's name."""

    url: str
    """Glossary URL the link points to."""


class SearchResult(typing.NamedTuple):
    """A single term definition extracted from the glossary."""

    term: str
    """The glossary term this result defines."""

    definition: str | None
    """Full text of the definition, or `None` if it could not be parsed."""

    grammatical_label: str | None
    """Part of speech of the term (e.g. "Noun"), or `None` if unavailable."""

    topic: str | None
    """Topic/discipline this definition is filed under in the glossary."""

    url: str | None
    """URL of the glossary page the definition was extracted from."""

    image: str | None = None
    """URL of the term's illustrative image, or `None` if the page has none."""

    image_caption: str | None = None
    """Caption text accompanying `image`, or `None` if the page has none."""

    related: tuple[RelatedTerm, ...] | None = None
    """Terms linked from this definition's "See related terms" list, or
    `None` if the page has none."""

    language: str = "en"
    """Glossary language edition (`Language.value`, e.g. `"en"`/`"es"`) this result was found in."""

    @property
    def fields(self) -> list[str]:
        """Return a list of the field names in this result."""
        return list(self._fields)

    def asdict(self) -> dict[str, typing.Any]:
        """Return a dictionary representation of this result."""
        return self._asdict()


class Updatable:
    """
    Mixin adding `.update(**changes)` to a `@dataclasses.dataclass`, as a
    shorter, more efficient alternative to `dataclasses.replace` for the
    common case of changing a few top-level fields.

    ```python
    @dataclasses.dataclass(frozen=True, slots=True, kw_only=True)
    class Options(Updatable):
        timeout: float = 30.0
        retries: int = 3


    opts = Options()
    opts2 = opts.update(timeout=60.0)  # instead of dataclasses.replace(opts, timeout=60.0)
    ```

    For a **frozen** dataclass, `update` returns a new instance with
    `changes` applied so `self` is untouched, exactly like
    `dataclasses.replace`, just shorter to write and to chain
    (`config.update(a=1).update(b=2)`).

    For a **non-frozen** one, `update` mutates `self` in place, field by field,
    and returns `self` so a caller that doesn't know (or care) whether a particular config is
    frozen can still call `.update(...)` and either use the return value or not, uniformly.

    `changes` are applied via `dataclasses.replace`/`setattr`.

    Declare this *before* other bases so it doesn't shadow a dataclass
    field actually named `update`, e.g. `class Foo(Updatable): ...` not
    `class Foo(SomethingElse, Updatable): ...` if `SomethingElse` has an
    `update` field/method of its own.
    """

    __slots__ = ()

    def update(self: "UpdatableT", **changes: typing.Any) -> "UpdatableT":
        """
        Apply `changes` to this dataclass instance.

        :param changes: Field name to new value. Every name must be an
            actual field of this dataclass.
        :return: A new instance with `changes` applied, if this dataclass
            is frozen; `self`, mutated in place, otherwise.
        :raises TypeError: If this class isn't a `dataclasses.dataclass`,
            or `changes` includes a name that isn't one of its fields.
        """
        if not dataclasses.is_dataclass(self):
            raise TypeError(
                f"`{type(self).__name__}.update()` requires a `dataclasses.dataclass`."
            )
        if not changes:
            return self

        valid = {f.name for f in dataclasses.fields(self)}
        unknown = changes.keys() - valid
        if unknown:
            raise TypeError(
                f"`{type(self).__name__}.update()` got unexpected field(s): "
                f"{', '.join(sorted(unknown))}. Expected one of: {', '.join(sorted(valid))}."
            )

        if dataclasses.is_dataclass(self) and type(self).__dataclass_params__.frozen:  # type: ignore[attr-defined]
            return dataclasses.replace(self, **changes)

        for name, value in changes.items():
            setattr(self, name, value)
        return self


UpdatableT = typing.TypeVar("UpdatableT", bound=Updatable)
