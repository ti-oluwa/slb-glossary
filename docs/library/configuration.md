# Saving Results and Config Objects

This page covers `slb_glossary.save`, for writing results to a file, and `slb_glossary.Config`, the programmatic form of everything [Saving, Output and Config Files](../cli/configuration.md) covers for the CLI.

---

## Saving results

```python
results = [lookup.value async for lookup in slb.search("black oil", db=db, session=session)]
await slb.save(results, "black_oil.json")
```

`save` picks a writer for `destination`'s file based on its extension, the same way the CLI's `--save` does. Pass `format` to override that independently of the extension:

```python
await slb.save(results, "black_oil.txt", format="csv")
```

`save` accepts a plain list, but also a bare async generator directly, without you collecting it into a list first:

```python
await slb.save(slb.live.search(session, "flooding", limit=None), "flooding.json")
```

```python
print(
    slb.writers.supported_formats()
)  # ['csv', 'json', 'jsonl', 'ndjson', 'txt', 'xlsx'] on a base install
```

`.xlsx` needs the `xlsx` extra installed (`uv add "slb-glossary[xlsx]"`), since it depends on `openpyxl`; every other format works with no extra at all. `.jsonl`/`.ndjson` write one JSON object per line (the same format either way, just two names for it), and `.txt` writes a numbered, human-readable list rather than a machine-parseable format, useful for a quick `cat` rather than feeding into another tool.

---

## Reading your own data

The read-side counterpart, `slb_glossary.readers`, is what `local.load_file` (covered in [Local Search and Cache](local-search.md#3-import-your-own-data)) uses internally, and it's just as usable directly for tabular data that has nothing to do with the glossary at all:

```python
for row in slb.readers.read_rows("my_data.csv"):
    print(row["some_column"])
```

```python
print(
    slb.readers.supported_formats()
)  # ['csv', 'json', 'xlsm', 'xlsx'] on a base install, plus 'yaml' with the `config` extra
```

`read_rows` picks a reader the same way `save` picks a writer: by `path`'s extension, or an explicit `format` override. It's a plain (non-async) generator, since reading rows out of a file doesn't need `await` the way a browser fetch does.

### Teaching it a new format

```python
import pathlib
import typing

from slb_glossary.readers import reader


@reader("tsv")
def read_tsv_rows(path: pathlib.Path) -> typing.Iterator[dict[str, typing.Any]]:
    with open(path, newline="", encoding="utf-8") as f:
        header = f.readline().rstrip("\n").split("\t")
        for line in f:
            yield dict(zip(header, line.rstrip("\n").split("\t")))
```

Once registered, `.tsv` files work everywhere `read_rows` is used underneath, including `local.load_file`, with no changes needed on that side:

```python
await slb.local.load_file(db, "terms.tsv")
```

See [`slb_glossary.readers`](../api/library.md#slb_glossaryreaders) for the full API, including the built-in `read_csv_rows`/`read_json_rows`/`read_xlsx_rows` if you want to call one directly rather than through `read_rows`'s format dispatch.

---

## `Config`: the same settings, in code

Everything the CLI's [`config` command](../cli/configuration.md#the-config-command) manages is a plain, typed dataclass underneath. `Config`, with three sections the CLI's dotted-key namespaces mirror:

```python
from slb_glossary import Config

config = Config()
print(config.session.browser_type)  # "chromium"
print(config.local.data_dir)  # None -> OS-appropriate default
print(config.output.show_related)  # False
```

- **`config.session`** (`SessionOptions`): everything `slb_glossary.live.session()` takes as keyword arguments, `language`, `browser_type`, `headless`, `timeout`, `retry`, and so on.
- **`config.local`** (`DatabaseOptions`): `data_dir`, `db_filename`, `prefer_local`, `sync_max_age_days`.
- **`config.output`** (`OutputOptions`): default save format, and which columns show by default (`show_url`, `show_topic`, `show_grammar`, `show_image`, `show_related`).

### Loading and saving

```python
config = Config.load()  # the default config path if it exists, else built-in defaults
config = Config.from_file("my-config.toml")  # a specific file; raises if it doesn't exist

config.session.headless = False
config.to_file("my-config.toml")  # format inferred from the extension
```

`Config.load()` is what you'll reach for most. Unlike `from_file`, it never raises `FileNotFoundError`, so a script can always call it and get *something* usable back, config file or not.

### Reading and writing one setting at a time

```python
print(config.get("session.headless"))  # True
config.set("local.sync_max_age_days", 3.5)
```

This is what powers `slb config get`/`slb config set` on the CLI, using the exact same dotted-key paths.

---

## Opening a session from a `Config`

```python
config = Config.load()

async with slb.live.session_from_config(config) as session:
    async for result in slb.live.search(session, "porosity"):
        ...
```

`session_from_config` also accepts a path directly, loading it for you:

```python
async with slb.live.session_from_config("my-config.toml") as session:
    ...
```

Any keyword argument passed alongside `config` overrides that specific setting for this one call, without changing the `Config` object or the file it came from:

```python
async with slb.live.session_from_config(config, headless=False) as session:
    ...  # headed for this run only, regardless of what config.session.headless says
```

There's no equivalent `database_from_config` for the local side, since `slb.local.database()` only ever takes a plain path; read `config.local.data_dir`/`db_filename` yourself to build that path where you want config-driven database placement too:

```python
import pathlib

data_dir = pathlib.Path(config.local.data_dir or ".")
async with slb.local.database(data_dir / config.local.db_filename) as db:
    ...
```

---

## Where to go from here

That's the whole library surface this documentation set out to cover: [Live Search](live-search.md), [Local Search and Cache](local-search.md), [Combined Search](query.md), and this page. For the concepts referenced throughout, what a `Session` actually is, how the three search modes differ, and the full `SearchResult` field set, see [Core Concepts](../concepts/sessions.md).
