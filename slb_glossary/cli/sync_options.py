"""Shared `--topic`/`--query`/`--start-letter`/`--all` update filters for `update` and `sync`."""

import typing

import click

from slb_glossary import local
from slb_glossary.live.browser import Session
from slb_glossary.local.sync import SyncSummary
from slb_glossary.local.types import Database

__all__ = [
    "print_sync_summary",
    "run_configured_sync",
    "sync_filter_options",
    "validate_sync_filters",
]


F = typing.TypeVar("F", bound=typing.Callable[..., typing.Any])


def sync_filter_options(func: F) -> F:
    """
    Attach `--topic`/`--query`/`--start-letter`/`--all`/`--limit`/`--concurrency`/`--yes`.

    Shared between `slb-glossary update` and `slb-glossary sync`, so both
    narrow a live fetch the same way.

    :param func: The click command callback to attach options to.
    :return: `func`, with the update-filter options attached.
    """
    func = click.option(
        "--yes",
        "-y",
        "assume_yes",
        is_flag=True,
        help="Don't ask for confirmation before a heavy update (implied by --all).",
    )(func)
    func = click.option(
        "--concurrency",
        type=int,
        default=1,
        show_default=True,
        help="Concurrent term-page fetches. Keep this low; be considerate of the live site.",
    )(func)
    func = click.option(
        "--limit",
        "-n",
        type=int,
        default=0,
        help="Maximum number of terms to update. Defaults to every matching term.",
    )(func)
    func = click.option(
        "--all",
        "sync_everything",
        is_flag=True,
        help="Update the entire glossary. Heavy. See the command's --help notes.",
    )(func)
    func = click.option(
        "--start-letter",
        "-a",
        default=None,
        help="Only update terms starting with this letter.",
    )(func)
    func = click.option(
        "--query",
        "-Q",
        default=None,
        help="Only update terms matching this free-text query.",
    )(func)
    func = click.option(
        "--topic",
        "-t",
        default=None,
        help="Only update terms filed under this topic, or several comma-separated topics.",
    )(func)
    return func


def validate_sync_filters(params: typing.Mapping[str, typing.Any]) -> None:
    """
    Validate `--topic`/`--query`/`--start-letter`/`--all`, prompting to confirm a heavy `--all`.

    :param params: The command's parsed parameters, as attached by `sync_filter_options`.
    :raises click.UsageError: If `--all` was combined with another filter.
    """
    if params["sync_everything"] and (
        params["topic"] or params["query"] or params["start_letter"]
    ):
        raise click.UsageError("--all can't be combined with --topic/--query/--start-letter.")

    if params["sync_everything"] and not params["assume_yes"]:
        click.confirm(
            "This will fetch the entire glossary (every topic, every term). "
            "It's the heaviest update available and the most likely to draw "
            "attention from the live site's own rate limiting. Continue?",
            abort=True,
        )


async def run_configured_sync(
    db: Database, session: Session, params: typing.Mapping[str, typing.Any]
) -> SyncSummary:
    """
    Dispatch to the right `slb_glossary.local.sync` function for the given filter params.

    :param db: The local database to write to.
    :param session: An open live `Session` to fetch from.
    :param params: The command's parsed parameters, as attached by `sync_filter_options`.
    :return: A summary of the sync.
    """
    topic = params["topic"]
    query = params["query"]
    start_letter = params["start_letter"]
    limit = params["limit"] or None
    concurrency = params["concurrency"] or 1

    if params["sync_everything"]:
        return await local.sync_all(db, session, concurrency=concurrency)
    if query:
        return await local.sync_query(
            db,
            session,
            query,
            topic=topic,
            start_letter=start_letter,
            limit=limit,
            concurrency=concurrency,
        )
    if start_letter:
        return await local.sync_letter(
            db, session, start_letter, topic=topic, limit=limit, concurrency=concurrency
        )
    if topic:
        return await local.sync_topic(db, session, topic, limit=limit, concurrency=concurrency)
    return await local.sync_topics(db, session)


def print_sync_summary(summary: SyncSummary) -> None:
    """Print a `SyncSummary` in a short, human-friendly form."""
    click.echo(f"Wrote {summary.terms_written} term(s); {summary.total_terms} stored locally now.")
    if summary.topics:
        top = sorted(summary.topics.items(), key=lambda item: item[1], reverse=True)[:5]
        preview = ", ".join(f"{name} ({count})" for name, count in top)
        more = len(summary.topics) - len(top)
        if more > 0:
            preview += f", +{more} more"
        click.echo(f"Topics: {preview}")
    click.echo(f"Last synced: {summary.synced_at}")
