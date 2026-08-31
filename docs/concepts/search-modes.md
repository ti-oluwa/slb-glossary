# Search Modes

Every ranked search this library does, local or live, uses one of three ranking strategies, chosen via `mode` (or `--mode`/`-m` on the CLI): `"lexical"`, `"semantic"`, or `"hybrid"`. This page explains what each one actually does, so [Local Search and Cache](../library/local-search.md#search-modes-lexical-semantic-hybrid) and the CLI's `--mode` flag make sense as more than just three interchangeable options.

---

## Lexical: matching words

**The default. Needs nothing beyond the base install.**

Lexical search ranks by [bm25](https://en.wikipedia.org/wiki/Okapi_BM25), a full-text ranking algorithm, over the term, definition, and topic text actually stored locally. It only ever matches words that are actually present (or close misspellings of them, if `fuzzy=True`): searching "rock that holds fluid" under lexical mode will not find "porosity" unless those specific words appear somewhere in a stored definition.

Live search uses a related but simpler technique, since there's no whole result set to rank against ahead of time: plain token overlap between your query and each candidate term/topic, scored as results stream in one page at a time.

## Semantic: matching meaning

**Needs the `semantic` extra installed (`uv add "slb-glossary[semantic]"`), and terms already embedded first.**

Semantic search compares *embeddings*: numeric vectors that capture a phrase's meaning, produced by a small local model ([`minishlab/potion-retrieval-32M`](https://huggingface.co/minishlab/potion-retrieval-32M), via [model2vec](https://github.com/MinishLab/model2vec)), downloaded once and cached, with no network call needed per query afterward. Two phrases with similar meanings end up with similar vectors even if they don't share any words, which is what lets "rock that holds fluid" find "porosity": their embeddings land close together in vector space, measured by cosine similarity.

This only works on terms you've already run through `embed_terms`:

```python
await slb.local.embed_terms(db)   # embeds everything not already embedded
```

From the CLI, the equivalent is `slb local embed` - see [Local Cache and Sync](../cli/sync.md#embedding-for-semantichybrid-search).

`embed_terms` is a one-time (or periodic) cost, separate from ordinary syncing: syncing fetches and stores terms, `embed_terms` computes and stores their vectors. Run it again after a `sync` that added new terms, with `only_missing=True` (the default) so it only pays for what's actually new.

!!! warning "Semantic scores aren't on the same scale as lexical scores"
    Lexical (and hybrid) scores are calibrated to roughly `[0.0, 1.0]`. Semantic search's cosine-similarity scores aren't calibrated the same way, which matters if you're pairing `mode="semantic"` with `source=Source.AUTO`'s `relevance_threshold`: that threshold is being compared against an uncalibrated number in that combination. `mode="hybrid"` is the better pairing with `Source.AUTO` for exactly this reason.

Live search has no semantic mode at all: there's no local embedding table to compare against for a page that was just fetched, so semantic (and hybrid) ranking is local-only.

## Hybrid: both, fused

**Same requirements as semantic: the extra installed, and terms embedded.**

Hybrid search runs both lexical and semantic search over the same query, then fuses their two rankings with **weighted Reciprocal Rank Fusion (RRF)**: each result's score is based on *where it ranked* in each list, not the raw scores themselves, which sidesteps the lexical/semantic scale mismatch entirely. A result that ranks well in either list (or both) surfaces near the top; the fused scores are then min-max normalized back into a `[0.0, 1.0]`-ish band, so `relevance_threshold` behaves sensibly again.

This is generally the best-ranking mode once you've embedded your terms, and the recommended pairing with `Source.AUTO`. It's not the library-wide default (`"lexical"` is) specifically so that a database that's never had `embed_terms` run on it keeps working out of the box, without the `semantic` extra being forced on every install.

---

## Choosing a mode

| | Needs | Matches | Works live | Good `Source.AUTO` pairing |
|---|---|---|---|---|
| `lexical` | Nothing extra | Exact words (or near-misspellings, with `fuzzy=True`) | Yes | Yes |
| `semantic` | `semantic` extra + `embed_terms` | Meaning, not exact words | No (local only) | Only with care — see the scale warning above |
| `hybrid` | `semantic` extra + `embed_terms` | Both, fused by rank | No (local only) | Yes — generally the best default once embedded |

```python
await slb.local.search(db, "porosity", mode="lexical")   # default, exact-word match
await slb.local.search(db, "rock that holds fluid", mode="semantic")  # paraphrase match
await slb.local.search(db, "reservoir rock", mode="hybrid")           # both, fused
```

```bash
slb search porosity --local --mode hybrid
```

## Where this shows up

- [`slb_glossary.local.search`](../library/local-search.md#search-modes-lexical-semantic-hybrid), and the standalone `lexical_search`/`vector_search`/`hybrid_search` functions it dispatches to.
- [`slb_glossary.query.search`](../library/query.md#search-the-one-youll-reach-for-most)'s `mode` parameter, with the live-fallback restriction that a live fetch can't be scored `"hybrid"`.
- The CLI's `search --mode`/`local search --mode`, and `slb local export --query ... --mode`.
