# Managing Sessions in Your App

Opening a live `Session` launches a whole browser. An application that makes many lookups (a web service, a bot, a tool server) should not open one per request, nor write its own sharing and shutdown code. `Runtime` does that for you.

```python
import slb_glossary as slb
from slb_glossary import query

async with slb.Runtime(max_sessions=2) as runtime:
    async with runtime.acquire(query.Source.AUTO, language="en") as (db, session):
        async for result in query.search("porosity", db=db, session=session):
            print(result.value.term)
```

A `Runtime` owns the shared local database and one `SessionPool` per glossary language, all bounded by one browser budget (`max_sessions`).

---

## Getting what a call needs

- `runtime.acquire(source, language=..., capacity=...)` yields the `(db, session)` pair a `Source` needs. Either can be `None` when the source doesn't use it.
- `runtime.session(language, capacity=...)` yields just a live `Session`, for when you don't deal in `Source`.
- `runtime.open_db()` returns the shared `Database`.

`capacity` is how many pages the call expects to use at once (for example its `concurrency`). It only informs whether to reuse a session or open another; page limits are still enforced by the session itself.

---

## Session modes

| `SessionMode` | A browser is opened | Use it for |
| --- | --- | --- |
| `LAZY` (default) | On the first call that needs one, then reused | Most apps |
| `EAGER` | When the runtime starts | Lowest first-call latency |
| `PER_CALL` | For every call, closed right after | Full isolation between callers |

---

## Tuning

All of these are arguments to `Runtime(...)`:

- `max_sessions`: most browsers open at once, across every language. Raising `SessionOptions.max_pages` is cheaper than raising this.
- `idle_timeout`: seconds an unused session lives before it is closed (`None` keeps them until `close()`).
- `capacity_tolerance`: how many pages short an existing session may be before another browser is opened.
- `session_options` / `database_options`: how sessions are opened and where the database lives.
- `live_enabled` / `local_enabled`: switch either side off.

`Runtime.from_config(config, **overrides)` builds one from a `slb_glossary.config.Config`.

When the browser budget is spent, a call for a language that already has a session shares it. A call for a language with none first closes sessions that are idle in other languages, then waits for one.

---

## Inspecting and closing

```python
runtime.stats()  # sessions open, busy, per-language checkouts
await runtime.close_idle_sessions()  # run one reaping cycle now
await runtime.close()  # or use `async with`
```

A browser that crashes is dropped and replaced rather than handed out again.

---

## Using a pool directly

`SessionPool` is the per-language building block, if you want it without the rest:

```python
pool = slb.SessionPool(slb.Language.ENGLISH, slb.config.SessionOptions(), max_sessions=2)
async with pool.checkout(capacity=3) as session:
    ...
await pool.close()
```
