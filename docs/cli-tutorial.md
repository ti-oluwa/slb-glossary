# Using the CLI

Audience: anyone using the `slb` command from a terminal, no programming
knowledge assumed. If you have not installed the tool yet, see
[Installation](installation.md) first.

## Your first search

```bash
slb search porosity
```

This opens a browser in the background, searches the live glossary for
"porosity", and prints what it found. The output looks something like
this:

```text
                       Search Results for 'porosity'
┏━━━━━━━━━┳━━━━━━━━━┳━━━━━━━━━━━━━━┳━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┓
┃ Term    ┃ Grammar ┃ Topic        ┃ Definition                              ┃
┡━━━━━━━━━╇━━━━━━━━━╇━━━━━━━━━━━━━━╇━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┩
│ Porosity│ Noun    │ Petrophysics │ The percentage of pore volume or void  │
│         │         │              │ space, or that volume within rock that │
│         │         │              │ can contain fluids...                  │
└─────────┴─────────┴──────────────┴─────────────────────────────────────────┘
```

The first search on a fresh install can take a little longer than
searches after it, since the background browser needs to start up once.
See the [FAQ](faq.md#why-is-the-first-search-slow) if that surprises you.

## Looking up one exact term instead of searching

If you already know the exact name of the term you want, `define` is more
direct than `search`:

```bash
slb define "water saturation"
```

This prints just that one term's definition instead of a table of
possible matches.

## Browsing a whole topic

To see every term filed under one topic in the glossary:

```bash
slb terms "Drilling Fluids" --limit 10
```

`--limit` caps how many terms are fetched. Leave it off to fetch every
term under the topic, which can take a while for a large one.

## Something for fun

`random` picks one or more terms at random, which works well as a "term
of the day" habit:

```bash
slb random --topic "Well Completions"
```

## Saving what you find to a file

Every command that prints results can also save them:

```bash
slb search "gas lift" --save gas_lift.json
```

This writes the same results shown on screen to `gas_lift.json`, so you
can look at them later or send the file to a colleague. The file format
is chosen from the file extension you give `--save`, so `--save
results.csv` writes a CSV file instead.

## Working offline after the first visit

Every search so far has gone to the live glossary site over the network.
`slb` can also keep a local copy on your own computer, so you are not
re-visiting the live site every time you look something up. This local
copy is a small SQLite database file.

To fill it with one term you already searched for:

```bash
slb sync --query porosity
```

`--query` only updates terms matching that free-text query, so this does
not fetch the whole glossary, just the one term. Now run the same search
again, but tell it to read from the local copy instead of the live site:

```bash
slb search porosity --local
```

This answers instantly, with no browser involved at all, because it is
reading the copy on your own machine.

`sync` also has an `--all` flag, which updates the entire glossary rather
than one topic. This is the heaviest option available and should be used
sparingly, since it means visiting every single term page on the live
site in one run. See the [API reference](api-reference.md#slb_glossarylocal)
for the rest of `sync`'s options.

## Where to go from here

Nearly every command shown on this page has more options than covered
here: saving in other formats, choosing a language, adjusting how many
results come back. All of that is covered in the
[API reference](api-reference.md). If you find yourself typing the same
flags on every command, see [Configuration](configuration.md) for how to
set them once instead.
