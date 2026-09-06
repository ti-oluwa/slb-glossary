"""
Relevance evaluation harness for `slb_glossary.local`'s lexical/semantic/hybrid search.

Not a `pytest` suite itself (see `tests/relevance/test_relevance_regressions.py`
for the handful of things that are asserted in CI). This package is a
reusable *benchmark*: a representative query set (`dataset.py`) run
against a seeded corpus (`corpus.py`), scored by standard ranking
metrics (`metrics.py`), via `harness.py`. See `scripts/relevance_bench.py`
for the runnable comparison script.

**On the corpus**: this sandbox has no network access to the real SLB
glossary site (or to Hugging Face, for the semantic model), so this
benchmark uses a hand-built, domain-accurate but *representative*
subset of oil-and-gas terms, not the actual production corpus. Every
definition here is factually accurate for its term, and the set
deliberately includes closely related/ambiguous term clusters
(porosity/permeability, several lift methods, several logging-while-
drilling terms, several pressure terms) so the benchmark actually
exercises disambiguation, not just easy exact matches. Re-run this
harness against a real synced database (`slb_glossary.local.sync`) for
production-representative numbers; the query *categories* and metric
code are what's meant to be reused as-is.
"""
