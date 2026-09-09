"""Shared output options for CLI commands that display or persist results."""

import contextlib
import json
import logging
import pathlib
import time
import typing

import click

from slb_glossary.query import Source
from slb_glossary.types import RecordLike
from slb_glossary.utils import Lookup, print_async_records
from slb_glossary.writers import records_to_dicts, save

logger = logging.getLogger(__name__)

__all__ = [
    "annotate_option",
    "output_options",
    "output_results",
    "should_annotate",
    "show_options",
]


F = typing.TypeVar("F", bound=typing.Callable[..., typing.Any])
RecordT = typing.TypeVar("RecordT", bound=RecordLike)


def show_options(
    *,
    default_url: bool = True,
    default_topic: bool = True,
    default_grammar: bool = True,
    default_image: bool = False,
    default_related: bool = False,
) -> typing.Callable[[F], F]:
    """
    Attach `--url`/`--show-topic`/`--show-grammar`/`--show-image`/`--show-related`
    to a command whose results are rendered as a `SearchResult` table.

    Stack the returned decorator directly above a command's `def`. The
    decorated callback receives `show_url`, `show_topic`, `show_grammar`,
    `show_image`, and `show_related` - pass these through to `output_results`.

    :param default_url: Default for `--url/--no-url`.
    :param default_topic: Default for `--show-topic/--hide-topic`.
    :param default_grammar: Default for `--show-grammar/--hide-grammar`.
    :param default_image: Default for `--show-image/--hide-image`.
    :param default_related: Default for `--show-related/--hide-related`.
    :return: A decorator attaching the five options, with the given defaults.
    """

    def decorator(func: F) -> F:
        func = click.option(
            "--show-related/--hide-related",
            "show_related",
            default=default_related,
            show_default=True,
            help="Show/hide the related-terms column.",
        )(func)
        func = click.option(
            "--show-image/--hide-image",
            "show_image",
            default=default_image,
            show_default=True,
            help="Show/hide the illustrative image URL column.",
        )(func)
        func = click.option(
            "--show-grammar/--hide-grammar",
            "show_grammar",
            default=default_grammar,
            show_default=True,
            help="Show/hide the grammatical label column.",
        )(func)
        func = click.option(
            "--show-topic/--hide-topic",
            "show_topic",
            default=default_topic,
            show_default=True,
            help="Show/hide the topic column.",
        )(func)
        func = click.option(
            "--url/--no-url",
            "show_url",
            default=default_url,
            show_default=True,
            help="Show/hide the source URL column.",
        )(func)
        return func

    return decorator


def annotate_option(func: F) -> F:
    """
    Attach `--annotate` to a command whose results may come from either
    local or live search, ambiguously, per result.

    :param func: The click command callback to attach the option to.
    :return: `func`, with `--annotate` attached.
    """
    return click.option(
        "--annotate",
        "annotate",
        type=click.Choice(["auto", "always", "never"], case_sensitive=False),
        default="auto",
        show_default=True,
        help=(
            "Show each result's origin (local/live) and relevance score, as "
            "extra table columns or, with --json, extra JSON keys. 'auto' "
            "shows them only for an --auto search, where results can "
            "genuinely come from either source, or be content-only matches "
            "worth a second look. A search pinned to --local/--live is "
            "already unambiguous about its source, so 'auto' leaves those "
            "unannotated. Has no effect on --save output, whose file formats "
            "have fixed columns."
        ),
    )(func)


def should_annotate(annotate: str, source: Source) -> bool:
    """
    Resolve `--annotate`'s tri-state value against the resolved `source`.

    :param annotate: The raw `--annotate` choice: `"always"`, `"never"`, or `"auto"`.
    :param source: The command's resolved `Source` (after `--local`/`--live`/`--auto`).
    :return: Whether to show each result's origin/score. `"auto"` shows
        them only for `Source.AUTO`. A search pinned to one source with
        `--local`/`--live` is already unambiguous about where results
        came from, so `"auto"` leaves those unannotated.
    """
    if annotate == "always":
        return True
    if annotate == "never":
        return False
    return source is Source.AUTO


def output_options(func: F) -> F:
    """
    Attach shared output options to a Click command.

    Adds options for controlling console output and optionally persisting
    command results, including `--quiet`, `--json`, `--save`, and `--format`.

    Stack this directly above a command's `def`, alongside
    `@click.command()`. The decorated callback receives `quiet`,
    `json_output`, `save_paths`, and `format`; pass these values through to
    `output_results`.

    :param func: The Click command callback to attach the output options to.
    :return: `func`, with the output options attached.
    """
    func = click.option(
        "--quiet",
        "-q",
        is_flag=True,
        help="Don't print results to the console (useful with --save).",
    )(func)
    func = click.option(
        "--json",
        "json_output",
        is_flag=True,
        help="Print results as JSON to stdout instead of a table. Ignored with --quiet.",
    )(func)
    func = click.option(
        "--format",
        "-f",
        "format",
        default=None,
        help=(
            "File format to save results as, e.g. 'csv'. Overrides each "
            "--save destination's file extension. See `slb-glossary formats`."
        ),
    )(func)
    func = click.option(
        "--save",
        "-o",
        "save_paths",
        type=click.Path(dir_okay=False, path_type=pathlib.Path),
        multiple=True,
        help="Save results to this file. Repeatable, to save to several files/formats at once.",
    )(func)
    return func


@typing.overload
async def output_results(
    results: typing.AsyncIterable[RecordT] | typing.AsyncIterator[RecordT],
    *,
    title: str | None = None,
    save_paths: typing.Sequence[pathlib.Path],
    format: str | None,
    quiet: bool,
    json_output: bool = False,
    print_limit: int | None = None,
    show_url: bool = True,
    show_topic: bool = True,
    show_grammar: bool = True,
    show_image: bool = False,
    show_related: bool = False,
    annotate: typing.Literal[False] = False,
) -> int: ...
@typing.overload
async def output_results(
    results: typing.AsyncIterable[Lookup[RecordT]] | typing.AsyncIterator[Lookup[RecordT]],
    *,
    title: str | None = None,
    save_paths: typing.Sequence[pathlib.Path],
    format: str | None,
    quiet: bool,
    json_output: bool = False,
    print_limit: int | None = None,
    show_url: bool = True,
    show_topic: bool = True,
    show_grammar: bool = True,
    show_image: bool = False,
    show_related: bool = False,
    annotate: typing.Literal[True],
) -> int: ...


async def output_results(
    results: typing.AsyncIterable[typing.Any] | typing.AsyncIterator[typing.Any],
    *,
    title: str | None = None,
    save_paths: typing.Sequence[pathlib.Path],
    format: str | None,
    quiet: bool,
    json_output: bool = False,
    print_limit: int | None = None,
    show_url: bool = True,
    show_topic: bool = True,
    show_grammar: bool = True,
    show_image: bool = False,
    show_related: bool = False,
    annotate: bool = False,
) -> int:
    """
    Collect, display, and optionally persist an async stream of results.

    Results are collected once so the same records can be displayed on the
    console and written to one or more files without re-running the
    network-bound operation that produced them. The console table (and
    JSON, when requested) streams/updates as `results` is consumed,
    rather than waiting to buffer everything before showing anything;
    "collected once" is about not re-iterating `results` a second time
    for saving, not about delaying output until the stream ends.

    :param results: The async stream of records to consume. With
        `annotate=True`, each item is a `slb_glossary.query.QueryResult`
        rather than a bare record.
    :param title: Title shown above the printed table, e.g.
        `f"Terms under {topic}"`. Passed straight through to
        `slb_glossary.utils.print_async_records`; ignored when
        `json_output` is `True` (JSON output has no table to title).
        Defaults to a generic type-based title. See `print_records`.
    :param save_paths: File paths to save the collected results to. Each
        path's format is inferred from its extension unless `format` is given.
        Always the plain record fields, even with `annotate=True` - the
        file formats here have fixed columns, with no natural place for
        per-row source/score metadata.
    :param format: File format to use for every path in `save_paths`,
        overriding each path's extension.
    :param quiet: If `True`, do not print results to the console. Results are
        still written to `save_paths`.
    :param json_output: If `True` and `quiet` is `False`, print results as
        JSON instead of a table.
    :param print_limit: Maximum number of results to print as a table.
        Ignored when `json_output` is `True`. Every collected result is
        still saved regardless of this limit.
    :param show_url: For `SearchResult`s, whether to print the source URL column.
    :param show_topic: For `SearchResult`s, whether to print the topic column.
    :param show_grammar: For `SearchResult`s, whether to print the grammatical label column.
    :param show_image: For `SearchResult`s, whether to print the image URL column.
    :param show_related: For `SearchResult`s, whether to print the related-terms column.
    :param annotate: If `True`, `results` is a stream of
        `slb_glossary.query.QueryResult`s, and both the table and JSON
        output get "source"/"score" alongside each record's own fields
        (the table as extra columns, JSON as extra keys). `save_paths`
        output is unaffected either way, see `save_paths` above.
    :return: The total number of results collected.
    :raises UnsupportedFormatError: If a save path (or
        `format`) resolves to a file format with no registered writer.
    :raises WriterError: If writing to a save path fails.
    """
    started_at = time.monotonic()
    async with contextlib.aclosing(results) as results:  # type: ignore[type-var]
        count = await collect_and_output(
            results,  # type: ignore[arg-type]
            title=title,
            save_paths=save_paths,
            format=format,
            quiet=quiet,
            json_output=json_output,
            print_limit=print_limit,
            show_url=show_url,
            show_topic=show_topic,
            show_grammar=show_grammar,
            show_image=show_image,
            show_related=show_related,
            annotate=annotate,
        )
    elapsed = time.monotonic() - started_at
    logger.debug(
        "Output %d result(s) in %.3fs (avg %.3fs/result)",
        count,
        elapsed,
        elapsed / count if count else 0.0,
    )
    return count


async def collect_and_output(
    results: typing.AsyncIterator[typing.Any],
    *,
    title: str | None,
    save_paths: typing.Sequence[pathlib.Path],
    format: str | None,
    quiet: bool,
    json_output: bool,
    print_limit: int | None,
    show_url: bool,
    show_topic: bool,
    show_grammar: bool,
    show_image: bool,
    show_related: bool,
    annotate: bool = False,
) -> int:
    """The body of `output_results`, run inside its `contextlib.aclosing(results)` block."""
    collected: list[typing.Any] = []
    count = 0
    was_collected = False

    if not quiet:

        async def _async_gen() -> typing.AsyncIterator[typing.Any]:
            nonlocal collected, count, was_collected
            was_collected = True
            async for record in results:
                collected.append(record)
                count += 1
                yield record

        iter_records = _async_gen()
        if json_output:
            exclude = []
            if not show_url:
                exclude.append("url")
            if not show_topic:
                exclude.append("topic")
            if not show_grammar:
                exclude.append("grammatical_label")
            if not show_image:
                exclude.append("image")
                exclude.append("image_caption")
            if not show_related:
                exclude.append("related")

            output: list[dict[str, typing.Any]] = []
            output_count = 0
            async for record in iter_records:
                item = record.value if annotate else record
                item_dict = records_to_dicts([item], exclude=exclude)[0]
                if annotate:
                    item_dict["source"] = record.source.value
                    item_dict["score"] = record.score
                output.append(item_dict)
                output_count += 1
                if print_limit and output_count >= print_limit:
                    break

            click.echo(json.dumps(output, indent=2, ensure_ascii=False))
        else:
            printed_count = await print_async_records(
                iter_records,
                title=title,
                limit=print_limit,
                show_url=show_url,
                show_topic=show_topic,
                show_grammar=show_grammar,
                show_image=show_image,
                show_related=show_related,
                annotate=annotate,
            )
            assert printed_count == count

    if save_paths:
        # If we printed an output then `collected` and `count` must have been updated.
        # But if `print_limit` was defined then there's a high likelihood that not all results
        # were collected and `count == print_limit` not `len(results)`. In that case,
        # we must collect the targets again fully.
        if was_collected and not print_limit:
            targets = collected
        elif was_collected:
            # We collect the remaining records + already collected ones
            targets = collected + [record async for record in results]
        else:
            targets = [record async for record in results]
        count = len(targets)  # Make sure to update count

        if annotate:
            targets = [record.value for record in targets]

        for path in save_paths:
            await save(targets, path, format=format)
            click.echo(f"Saved {count} result(s) to {path}", err=True)
    return count
