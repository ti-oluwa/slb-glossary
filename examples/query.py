"""
A real-world tour of `slb_glossary.query`: local-first search with a live
fallback, comparing terms, saving results, and enabling semantic search on
whatever ends up cached.

Run it twice in a row (`python -m examples.query`) - the second run answers
every lookup from the local database alone, with no browser involved at all,
since the first run's `persist=True` calls already cached everything it needed.
"""

import asyncio
import pathlib

import slb_glossary as slb

DB_PATH = pathlib.Path(__file__).parent / "example_glossary.db"


async def search_and_cache(db: slb.local.Database, session: slb.live.Session) -> None:
    """Local-first search, falling back to (and caching) a live lookup."""
    print("--- search: 'clathrates' ---")
    async for lookup in slb.search("clathrates", db=db, session=session, persist=True):
        origin = "cache" if lookup.source is slb.Source.LOCAL else "live, now cached"
        print(f"[{origin}] {lookup.value.term}: {lookup.value.definition}")


async def compare_terms(db: slb.local.Database, session: slb.live.Session) -> None:
    """Look up several terms concurrently and print them side by side."""
    print("\n--- compare: water flooding vs. gas flooding ---")
    results = await slb.compare(
        ["water flooding", "gas flooding"], db=db, session=session, persist=True
    )
    for term, lookup in results.items():
        if lookup.value is None:
            print(f"{term}: not found")
            continue
        print(f"{term} ({lookup.source.value}): {lookup.value.definition}")


async def a_random_term(db: slb.local.Database, session: slb.live.Session) -> None:
    """`get_random_term` for "term of the day"-style exploration."""
    print("\n--- a random term ---")
    lookup = await slb.get_random_term(db=db, session=session)
    if lookup.value is not None:
        print(f"{lookup.value.term}: {lookup.value.definition}")


async def save_a_batch(db: slb.local.Database, session: slb.live.Session) -> None:
    """Fetch every term under one topic and save the results to a file."""
    print("\n--- saving every term under 'Reservoir Engineering' ---")
    results = [
        lookup.value
        async for lookup in slb.get_terms_on(
            "Reservoir Engineering", db=db, session=session, limit=10, persist=True
        )
        if lookup.value is not None
    ]
    out_path = pathlib.Path(__file__).parent / "reservoir_engineering_terms.json"
    await slb.save(results, out_path)
    print(f"Saved {len(results)} term(s) to {out_path}")


async def enable_semantic_search(db: slb.local.Database) -> None:
    """
    Embed whatever's now cached, so a paraphrase can find it too.

    Needs the `semantic` extra installed (`uv add "slb-glossary[semantic]"`).
    Skipped gracefully if it isn't, since this is an optional step.
    """
    print("\n--- embedding cached terms for semantic search ---")
    try:
        embedded = await slb.local.embed_terms(db)
    except slb.EmbeddingError as exc:
        print(f"Skipping semantic search: {exc}")
        return

    print(f"Embedded {embedded} term(s).")
    hits = await slb.local.search(db, "rock that holds fluid", mode="semantic")
    for hit in hits:
        print(f"(semantic match) {hit.term}: {hit.definition}")


async def main() -> None:
    async with (
        slb.local.database(DB_PATH) as db,
        # `headless=False` here only so a first-time run is easy to watch;
        # drop it (or set `headless=True`) for a normal, background session.
        slb.live.session(headless=False, block=True) as session,
    ):
        try:
            await search_and_cache(db, session)
            await compare_terms(db, session)
            await a_random_term(db, session)
            await save_a_batch(db, session)
            await enable_semantic_search(db)
        except slb.NetworkError as exc:
            print(f"Network problem talking to the live glossary: {exc}")
        except slb.BrowserError as exc:
            print(
                f"Browser problem (is the browser build installed? try `slb install chromium`): {exc}"
            )


if __name__ == "__main__":
    asyncio.run(main())
