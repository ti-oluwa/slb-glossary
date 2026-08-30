# Using the library

Audience: a Python developer, comfortable with `async`/`await` at least at
a beginner level. If you have not written `async def`/`await` code before,
[Real Python's asyncio walkthrough](https://realpython.com/async-io-python/)
is a good place to start first; this page does not teach asyncio itself.

If you have not installed the library yet, see [Installation](installation.md).

## Your first live search

```python
import asyncio

import slb_glossary as slb


async def main() -> None:
    # Opens a browser session for the duration of this `async with` block,
    # and closes it again automatically when the block exits.
    async with slb.live.session() as session:
        async for result in slb.live.search(session, "porosity"):
            print(result.term, ":", result.definition)


if __name__ == "__main__":
    asyncio.run(main())
```

`slb.live.session()` starts the background browser the CLI also uses
under the hood. `async with` guarantees it gets closed even if
`slb.live.search` raises partway through, so you never leave a browser
process running past the end of your script.

## Reading a result

Each item `slb.live.search` yields is a `SearchResult`. It is a plain
`NamedTuple`, so you can unpack it positionally or access fields by name:

| Field                | What it holds                                              |
| --------------------- | ----------------------------------------------------------- |
| `term`                | The glossary term this result defines.                     |
| `definition`          | Full text of the definition, or `None` if it could not be parsed. |
| `grammatical_label`   | Part of speech (e.g. `"Noun"`), or `None`.                  |
| `topic`               | Topic or discipline this definition is filed under.         |
| `url`                 | The glossary page the definition came from.                 |
| `related`             | A tuple of `RelatedTerm(term, url)` pairs, or `None`.        |

```python
async for result in slb.live.search(session, "porosity"):
    print(result.term)              # by name
    term, definition, *_ = result   # or unpacked positionally, ignoring the rest
```

## Caching what you look up locally

Every live search re-visits the live site. For a script you run more than
once, that is wasted network time for terms you already have the answer
to. `slb_glossary.local` keeps a small SQLite database on disk for
exactly this:

```python
import asyncio

import slb_glossary as slb


async def main() -> None:
    async with slb.local.database("glossary.db") as db, slb.live.session() as session:
        # `slb.search` (from `slb_glossary.query`) checks `db` first and
        # only opens a live request if the term is not already cached.
        # `persist=True` writes any live result back to `db` as it
        # arrives, so the next run of this exact script does not need
        # the network at all.
        async for lookup in slb.search(
            "water saturation", db=db, session=session, persist=True
        ):
            print(lookup.source, ":", lookup.value.term, "-", lookup.value.definition)


if __name__ == "__main__":
    asyncio.run(main())
```

`slb.search` is the function that does local-first, live-fallback
automatically: it is worth reaching for over calling `slb_glossary.live`
or `slb_glossary.local` directly once a script needs both. Each item it
yields is a `QueryResult`, wrapping the actual `SearchResult` in
`lookup.value` alongside `lookup.source` (whether it came from `db` or a
live fetch) and `lookup.persisted`.

## Saving results to a file

The same function the CLI's own `--save` flag uses underneath is
available directly:

```python
results = [lookup.value async for lookup in slb.search("black oil", db=db, session=session)]
await slb.save(results, "black_oil.json")
```

`slb.save` picks a writer based on `destination`'s file extension, so
saving as CSV or XLSX instead only means changing the file name (XLSX
needs the `xlsx` extra installed).

## Where to go from here

The [API reference](api-reference.md) covers every function's full
parameter list, the `Config` object for anything bigger than a script,
and the local database's semantic and hybrid search for matching a
paraphrase rather than exact words.
