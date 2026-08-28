"""`slb-glossary local` - inspect, search, and maintain the local search database directly."""

import json
import pathlib
import typing

import click

from slb_glossary import local as local_pkg
from slb_glossary.cli.errors import cli_command
from slb_glossary.cli.output_options import output_options, output_results
from slb_glossary.cli.runner import run_async
from slb_glossary.cli.session_options import config_option, log_level_option
from slb_glossary.cli.source_options import database_option, load_config, resolve_db_path
from slb_glossary.local.types import Metadata
from slb_glossary.types import SearchMode
from slb_glossary.utils import as_async_iterator

__all__ = ["local"]


@click.group("local")
def local() -> None:
    """
    Inspect, search, and maintain the local search database directly.

    Every command here talks only to the local database,
    regardless of any --local/--live/--auto flag elsewhere.
    `local sync`/`local update` are the exception (and the only ones here
    that go live): they're the same commands as top-level `sync`/`update`,
    grouped here too for discoverability. `local import` is the other way
    to fill the database: from your own CSV/JSON/XLSX file instead of the
    live glossary.
    """


@local.command("path")
@database_option
@config_option
@log_level_option
@cli_command
def show_path(**params: typing.Any) -> None:
    """
    Print the resolved local database and metadata file paths.

    If you move or back these up by hand, also bring along the
    database's `-wal`/`-shm` sidecar files (it runs in WAL mode) - or
    close the database first (e.g. don't have anything else using it) so
    SQLite folds them back into the main file before you copy it.

    \b
    Examples:
      slb-glossary local path
    """

    async def run() -> tuple[typing.Any, typing.Any]:
        config = load_config(params)
        db_path = resolve_db_path(config, params["db_path"])
        async with local_pkg.database(db_path) as db:
            return db.db_path, db.metadata_path

    db_path, metadata_path = run_async(run())
    click.echo(f"Database: {db_path}")
    click.echo(f"Metadata: {metadata_path}")
    click.echo(
        f"(WAL sidecar files, if present: {db_path}-wal, {db_path}-shm - "
        "move/copy these together with the database above, and metadata "
        "separately; see `slb-glossary local path --help`.)"
    )


@local.command("stats")
@database_option
@config_option
@click.option(
    "--json",
    "json_output",
    is_flag=True,
    help="Print stats as JSON instead of a human-readable summary.",
)
@log_level_option
@cli_command
def stats(**params: typing.Any) -> None:
    """
    Print term counts, topic breakdown, and last-sync info for the local database.

    \b
    Examples:
      slb-glossary local stats
      slb-glossary local stats --json
    """

    async def run() -> tuple[int, dict[str, int], Metadata]:
        config = load_config(params)
        db_path = resolve_db_path(config, params["db_path"])
        async with local_pkg.database(db_path) as db:
            total = await local_pkg.count(db)
            topics = await local_pkg.get_topics(db)
            metadata = Metadata.load(db.metadata_path)
            return total, topics, metadata

    total, topics, metadata = run_async(run())

    if params["json_output"]:
        click.echo(
            json.dumps(
                {
                    "term_count": total,
                    "topics": topics,
                    "last_synced_at": metadata.last_synced_at,
                    "last_sync_language": metadata.last_sync_language,
                    "schema_version": metadata.schema_version,
                },
                indent=2,
            )
        )
        return

    click.echo(f"Terms stored locally: {total}")
    click.echo(f"Last synced: {metadata.last_synced_at or 'never'}")
    if metadata.last_sync_language:
        click.echo(f"Last sync language: {metadata.last_sync_language}")
    if topics:
        click.echo(f"Topics ({len(topics)}):")
        for name, count in sorted(topics.items(), key=lambda item: item[1], reverse=True):
            click.echo(f"  {name:<40} {count}")
    else:
        click.echo("No topics stored locally yet.")


@local.command("search")
@click.argument("query", default="")
@click.option(
    "--topic",
    "-t",
    default=None,
    help="Restrict results to this topic, or several comma-separated topics.",
)
@click.option(
    "--start-letter",
    default=None,
    metavar="LETTER",
    help="Restrict results to terms starting with this letter.",
)
@click.option(
    "--language",
    "-L",
    default=None,
    help="Restrict results to this glossary language edition (e.g. 'en'/'es'). "
    "Unset by default: doesn't filter by language.",
)
@click.option(
    "--fuzzy",
    is_flag=True,
    help="Tolerate minor misspellings/partial names in --topic, matched "
    "against topics actually stored locally, instead of requiring an exact "
    "(case-insensitive) match.",
)
@click.option(
    "--mode",
    "-m",
    type=click.Choice([mode.value for mode in SearchMode], case_sensitive=False),
    default=None,
    show_default="the SLB_GLOSSARY_DEFAULT_SEARCH_MODE / default_search_mode setting",
    help=(
        "Ranking strategy: 'lexical' (bm25 full-text, works out of the "
        "box), 'semantic' (embedding similarity), or 'hybrid' (both, "
        "fused; needs the [semantic] extra and terms already embedded "
        "via `slb_glossary.local.embed_terms`)."
    ),
)
@click.option(
    "--exclude",
    default=None,
    metavar="ENTRIES",
    help="Comma-separated URLs and/or term names to leave out of the results.",
)
@click.option(
    "--limit",
    "-n",
    type=int,
    default=20,
    show_default=True,
    help="Maximum number of results. Use 0 for unlimited.",
)
@database_option
@config_option
@output_options
@log_level_option
@cli_command
def local_search(query: str, **params: typing.Any) -> None:
    """
    Full-text search the local database only. Never touches the live glossary.

    \b
    Examples:
      slb-glossary local search porosity
      slb-glossary local search "drilling fluid" --topic Drilling
      slb-glossary local search viscosity --topic Petrophysic --fuzzy
      slb-glossary local search "reservoir rock" --mode hybrid
    """
    if not query.strip():
        raise click.BadParameter("Missing search query.")

    limit = params["limit"] or None
    title = f"Local Search Results for {query!r}"
    if params["topic"]:
        title += f" (topic: {params['topic']})"
    exclude = (
        tuple(e.strip() for e in params["exclude"].split(",") if e.strip())
        if params["exclude"]
        else None
    )

    async def run() -> int:
        config = load_config(params)
        db_path = resolve_db_path(config, params["db_path"])
        async with local_pkg.database(db_path) as db:
            results = await local_pkg.search(
                db,
                query,
                topic=params["topic"],
                start_letter=params["start_letter"],
                language=params["language"],
                limit=limit,
                fuzzy=params["fuzzy"],
                mode=params["mode"],
                exclude=exclude,
            )
            return await output_results(
                as_async_iterator(results),
                title=title,
                save_paths=params["save_paths"],
                format=params["format"],
                quiet=params["quiet"],
                json_output=params["json_output"],
            )

    count = run_async(run())
    if not params["quiet"] and count == 0:
        click.echo("No local results found.", err=True)


@local.command("get")
@click.argument("term_or_url", default="")
@click.option(
    "--topic",
    "-t",
    default=None,
    help="Pick a specific stored definition if TERM_OR_URL has several "
    "locally (one per topic it's filed under). Without this, and more "
    "than one is stored, which one comes back is deterministic (by "
    "topic name) but not otherwise meaningful.",
)
@click.option(
    "--language",
    "-L",
    default=None,
    help="Restrict the lookup to this glossary language edition (e.g. "
    "'en'/'es'). Unset by default: doesn't filter by language.",
)
@click.option(
    "--suggest/--no-suggest",
    "suggest_similar",
    default=True,
    show_default=True,
    help="When no exact match is found, offer up to --max-similar "
    "similarly-named alternatives instead of just reporting nothing found.",
)
@click.option(
    "--similar-pool-size",
    type=click.IntRange(min=1),
    default=None,
    metavar="N",
    help="Candidates pulled while looking for the exact match, and to draw "
    "--suggest alternatives from. Defaults to constants.similar_terms_pool_size.",
)
@click.option(
    "--max-similar",
    "max_similar_terms",
    type=click.IntRange(min=1),
    default=None,
    metavar="N",
    help="Max --suggest alternatives offered. Defaults to constants.max_similar_terms.",
)
@database_option
@config_option
@output_options
@log_level_option
@cli_command
def local_get(term_or_url: str, **params: typing.Any) -> None:
    """
    Look up a single term by exact name or URL in the local database only.

    \b
    Examples:
      slb-glossary local get porosity
      slb-glossary local get porosity --topic Petrophysics
      slb-glossary local get "https://glossary.slb.com/en/terms/p/porosity"
    """
    if not term_or_url.strip():
        raise click.BadParameter("Missing term or URL.")

    suggest_similar = params["suggest_similar"]

    async def run() -> int:
        config = load_config(params)
        db_path = resolve_db_path(config, params["db_path"])
        async with local_pkg.database(db_path) as db:
            if suggest_similar:
                result, similar = await local_pkg.get_term(
                    db,
                    term_or_url,
                    language=params["language"],
                    topic=params["topic"],
                    with_similar=True,
                    similar_pool_size=params["similar_pool_size"],
                    max_similar_terms=params["max_similar_terms"],
                )
                if result is None and similar and not params["quiet"]:
                    click.secho("Not found exactly. Did you mean:", fg="yellow", err=True)
                    for candidate, score in similar:
                        click.secho(f"  {candidate.term} ({score:.2f})", fg="yellow", err=True)
            else:
                result = await local_pkg.get_term(
                    db, term_or_url, language=params["language"], topic=params["topic"]
                )

            if result is None:
                return 0

            async def one() -> typing.AsyncIterator[typing.Any]:
                yield result

            return await output_results(
                one(),
                title=f"Local: {term_or_url}",
                save_paths=params["save_paths"],
                format=params["format"],
                quiet=params["quiet"],
                json_output=params["json_output"],
            )

    count = run_async(run())
    if not params["quiet"] and count == 0:
        click.echo(f"{term_or_url!r} was not found locally.", err=True)


def _field_or_empty(ctx: click.Context, param: click.Parameter, value: str | None) -> str | None:
    """
    Treat an explicitly empty `--*-field` value as "leave this field unset".

    Every optional `load_file` field defaults to its usual column/key name
    (e.g. `--definition-field` defaults to `"definition"`), so there needs
    to be some way to say "skip this field entirely" instead - passing an
    empty string (`--definition-field ""`) does that, converted to `None`
    here for `load_file` itself.
    """
    return value or None


@local.command("import")
@click.argument(
    "path", type=click.Path(exists=True, dir_okay=False, readable=True, path_type=pathlib.Path)
)
@click.option(
    "--format",
    "-f",
    "import_format",
    type=click.Choice(["csv", "json", "xlsx", "xlsm"], case_sensitive=False),
    default=None,
    help="File format to read PATH as. Inferred from its extension if not given.",
)
@click.option(
    "--term-field",
    default="term",
    show_default=True,
    help="Column/key holding each row's term name. A row missing this is skipped.",
)
@click.option(
    "--definition-field",
    default="definition",
    show_default=True,
    callback=_field_or_empty,
    help="Column/key holding each row's definition. Pass '' to leave it unset on every imported row.",
)
@click.option(
    "--topic-field",
    default="topic",
    show_default=True,
    callback=_field_or_empty,
    help="Column/key holding each row's topic. Pass '' to leave it unset on every imported row.",
)
@click.option(
    "--url-field",
    default="url",
    show_default=True,
    callback=_field_or_empty,
    help=(
        "Column/key holding each row's source URL. Pass '' to always "
        "synthesize a 'local://imported/<slugified-term>' URL instead - "
        "needed since url and topic together are the local database's primary key."
    ),
)
@click.option(
    "--grammatical-label-field",
    default="grammatical_label",
    show_default=True,
    callback=_field_or_empty,
    help="Column/key holding each row's grammatical label (e.g. 'Noun'). Pass '' to leave it unset.",
)
@click.option(
    "--language-field",
    default="language",
    show_default=True,
    callback=_field_or_empty,
    help=(
        "Column/key holding each row's language edition (e.g. 'en'/'es'). "
        "Pass '' to always use --default-language instead, even for a row "
        "that has this column."
    ),
)
@click.option(
    "--default-language",
    default="en",
    show_default=True,
    help="Language stored for a row with no usable --language-field value.",
)
@click.option(
    "--image-field",
    default="image",
    show_default=True,
    callback=_field_or_empty,
    help="Column/key holding each row's image URL. Pass '' to leave it unset.",
)
@click.option(
    "--image-caption-field",
    default="image_caption",
    show_default=True,
    callback=_field_or_empty,
    help="Column/key holding each row's image caption. Pass '' to leave it unset.",
)
@click.option(
    "--related-field",
    default="related",
    show_default=True,
    callback=_field_or_empty,
    help=(
        "Column/key holding each row's related terms. Pass '' to leave it "
        "unset. The value needs to be a list (from a JSON source) or a "
        'JSON array string (any format) of {"term": ..., "url": ...} '
        "objects or [term, url] pairs; anything else is left unset for "
        "that row rather than raising."
    ),
)
@click.option(
    "--source-tag",
    "source_tag",
    default="user",
    show_default=True,
    help=(
        "Provenance tag stored on every imported row, so imported data can "
        "later be told apart from rows fetched live from the glossary "
        "('glossary' - see local stats/local get)."
    ),
)
@click.option(
    "--batch-size",
    type=click.IntRange(min=1),
    default=None,
    metavar="N",
    help=(
        "Number of rows to buffer before writing an incremental batch to "
        "the database, instead of reading the whole file into memory "
        "first. Lower values save progress more often; higher values "
        "write less often but risk losing more unwritten rows if the "
        "import is interrupted before the next flush. Defaults to "
        "constants.import_batch_size (500 unless "
        "`SLB_GLOSSARY_IMPORT_BATCH_SIZE` overrides it)."
    ),
)
@database_option
@config_option
@log_level_option
@cli_command
def import_(path: pathlib.Path, **params: typing.Any) -> None:
    """
    Import term data from a CSV, JSON, or XLSX file into the local database.

    Each row/record needs at least --term-field (default 'term'); every
    other field is optional - pass '' to any --*-field option to leave
    that field unset on every imported row instead of looking it up.
    Matching a row's own field names is case-insensitive.

    A row's own url (or, missing that, a URL synthesized from its term -
    see --url-field) and topic together are the local database's primary
    key, so importing the same file twice updates existing rows rather
    than duplicating them.

    Rows are read and upserted in batches (see --batch-size) rather than
    all at once, so a large import stays memory-bounded, and an
    interruption partway through still keeps whatever batches were
    already written rather than losing the whole import.

    \b
    Examples:
      slb-glossary local import terms.csv
      slb-glossary local import terms.json --source-tag internal-wordlist
      slb-glossary local import terms.xlsx --topic-field Category --url-field ""
      slb-glossary local import terms.json --related-field see_also
      slb-glossary local import terms.csv --batch-size 200 --db-path ./my.db
    """

    async def run() -> int:
        config = load_config(params)
        db_path = resolve_db_path(config, params["db_path"])
        async with local_pkg.database(db_path) as db:
            return await local_pkg.load_file(
                db,
                path,
                format=params["import_format"],
                term_field=params["term_field"],
                definition_field=params["definition_field"],
                topic_field=params["topic_field"],
                url_field=params["url_field"],
                grammatical_label_field=params["grammatical_label_field"],
                language_field=params["language_field"],
                image_field=params["image_field"],
                image_caption_field=params["image_caption_field"],
                related_field=params["related_field"],
                default_language=params["default_language"],
                source=params["source_tag"],
                batch_size=params["batch_size"],
            )

    written = run_async(run())
    click.echo(f"Imported {written} row(s) from {path} into the local database.")


@local.command("flush")
@database_option
@config_option
@click.option("--yes", "-y", "assume_yes", is_flag=True, help="Don't ask for confirmation.")
@log_level_option
@cli_command
def flush(**params: typing.Any) -> None:
    """
    Delete every locally stored term, keeping sync history/metadata intact.

    \b
    Examples:
      slb-glossary local flush --yes
    """
    if not params["assume_yes"]:
        click.confirm("Delete every term stored in the local database?", abort=True)

    async def run() -> None:
        config = load_config(params)
        db_path = resolve_db_path(config, params["db_path"])
        async with local_pkg.database(db_path) as db:
            await local_pkg.flush(db)

    run_async(run())
    click.echo("Local database flushed.")


@local.command("reset")
@database_option
@config_option
@click.option("--yes", "-y", "assume_yes", is_flag=True, help="Don't ask for confirmation.")
@log_level_option
@cli_command
def reset(**params: typing.Any) -> None:
    """
    Flush the local database and forget its sync history too.

    \b
    Examples:
      slb-glossary local reset --yes
    """
    if not params["assume_yes"]:
        click.confirm(
            "Delete every term and reset sync history in the local database?", abort=True
        )

    async def run() -> None:
        config = load_config(params)
        db_path = resolve_db_path(config, params["db_path"])
        async with local_pkg.database(db_path) as db:
            await local_pkg.reset(db)

    run_async(run())
    click.echo("Local database reset.")
