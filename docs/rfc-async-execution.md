# RFC: async execution path for TypeDAL

**Status:** feasibility confirmed, implementation not started.
**Scope decided:** Postgres + SQLite, all five ops (`select`/`insert`/`update`/`delete`/`count`).
**Constraints:** pydal is never forked or patched; TypeDAL's public API is unchanged (new methods only).

pydal tested: `20260520.0` (satisfies TypeDAL's `pydal>=20251012.3` pin). Drivers tested:
psycopg2 2.9.12 (sync baseline), psycopg 3.3.4 (async), asyncpg 0.31.0.

## The question

pydal's per-query work is a sandwich: build SQL (pure, microseconds) → execute (the only I/O)
→ parse rows (pure). If that split is reachable from outside pydal, TypeDAL can let pydal build
and parse as it always has, and only replace the execute step with a real async driver — no
greenlets, no pydal rewrite.

## Verdict: clean

Not just "a subclass can intercept `execute`/`parse`" — pydal already keeps build, execute, and
parse as three separate method calls with nothing to split. `Set.select()`
(`pydal/objects.py:2961-2971`) calls `adapter.tables()` → `adapter.expand_all()` →
`adapter.select()`. `SQLAdapter.select()` (`adapters/base.py:905-910`) calls
`self._select_wcols()` (pure, returns `(colnames, sql)`) then `_select_aux()`
(`base.py:864-891`), which does `execute(sql)` + `cursor.fetchall()` (the only I/O), then
`self.parse(rows, fields, colnames)` (pure). `_select_wcols` is called directly by
`Set.select()` itself — it isn't a hidden internal, it's part of the normal call graph. An
outside caller can call the build half, do its own I/O, and call `parse()` on the result,
skipping `select()`/`_select_aux()`/`execute()` entirely:

```python
colnames, sql = adapter._select_wcols(query, fields, **attributes)  # build (pydal, unmodified)
rows = await async_driver.execute_and_fetch(sql)  # our I/O
result = adapter.parse(rows, fields, colnames, cacheable=...)  # parse (pydal, unmodified)
```

No pydal method body is copied or patched. Verified live in the PoC (below): output is
field-for-field identical to `db(...).select()` run synchronously, and a formalized non-blocking
test shows the event loop keeps ticking at ~5ms while real queries run.

**Per operation:**

| op | Postgres | SQLite |
|---|---|---|
| `select` / `count` / `executesql` | clean sandwich | clean sandwich |
| `insert` | clean; one round trip in the standard case — `INSERT ... RETURNING id` is sent once, `cursor.fetchone()` just reads the row already returned by that statement (`adapters/postgres.py:142-162`). A second real round trip (`SELECT currval(...)`) only happens for tables with a custom `_primarykey` where the pk value wasn't supplied. | clean |
| `update` | clean sandwich | clean sandwich |
| `delete` | clean sandwich (`adapters/base.py:604-610`) | **not a sandwich** — `SQLite.delete()` (`adapters/sqlite.py:93-104`) runs a nested `SELECT` then recurses into `.delete()` per cascaded FK. Needs its own async reimplementation of the cascade, not a wrapped execute call. |

SQLite's `select()` also has a side effect the base class doesn't: `for_update=True` triggers a
real `BEGIN IMMEDIATE TRANSACTION` *before* the build step (`adapters/sqlite.py:88-91`) — the
async wrapper has to special-case this, it can't assume every adapter's `select()` is side-effect
free just because the base one is.

## Hypothesis B (greenlet bridge) — not needed, killed early

`pydal/_globals.py:4`: `THREAD_LOCAL = threading.local()`, imported **by value** into six modules
(`connection.py:8`, `base.py:148`, `helpers/classes.py:17`, `adapters/postgres.py:5`,
`adapters/snowflake.py`, `adapters/google.py`). `ConnectionPool` closes over that name directly —
no subclass hook exists to redirect it to a contextvars-backed registry. Doable only as a
monkeypatch across all six modules, before first import, version-fragile. Since hypothesis A
worked, this wasn't built out further (no fake driver module, no contextvars registry).

## Secondary findings

1. **Two connections per request (confirmed hazard, no guard built yet).** Only the ops we
   reroute touch the async connection; DDL, `commit()`/`rollback()` (`base.py:849-855`), and lazy
   `Reference` resolution still go through the thread-local sync connection via
   `@with_connection_or_raise`. A write on one connection is invisible to a read on the other
   until commit. Mitigation is procedural (one path per request) until a guard is written.

2. **Driver type mapping — verified, not inferred.** psycopg3 async matches psycopg2 exactly on
   everything tested (int/str/Decimal/jsonb→dict). **asyncpg returns jsonb as raw `str`**, not
   dict — pydal's `Postgre._config_json()` picks `PostgreAutoJSONParser`
   (`parsers/postgre.py:12-13`, no json handler, expects the driver to have already decoded it),
   which would silently leave jsonb as a string with asyncpg unless `self.parser` is forced to
   the string-expecting variant. **Recommend psycopg3** for this reason — compatibility over
   throughput, as scoped. Not tested: UUID, arrays, tstz, intervals.

3. **Parameterisation — confirmed and quantified.** `adapt()` (`adapters/base.py:442-443`)
   splices literals into the SQL text; every ORM call site (`select`/`insert`/`update`/`delete`)
   calls `execute(sql)` with no extra args, so no DB-API placeholders are ever used outside
   `executesql(..., placeholders=...)`. Measured (localhost, 400 iterations): literal-interpolated
   vs. server-prepared identical queries — no measurable difference (0.078ms vs 0.084ms/query).
   Not tested at scale or over a real network.

4. **Lazy `Reference` access — confirmed, unresolved.** `Reference.__allocate()`
   (`helpers/classes.py:189-196`) fires a blocking query from plain attribute access. `Reference`
   is constructed directly in two modules (`parsers/base.py`, `adapters/base.py:561`), same
   by-value-import fragility as `THREAD_LOCAL`. TypeDAL's own `Reference`/`Row`
   (`src/typedal/types.py:197-198`) are mypy-only stubs today with no runtime behavior to hook
   into. No clean fix; mitigation is documentation (eager-load via joins) + convention, not code.

5. **SQLite — the easy case, and why.** `aiosqlite` wraps the same stdlib `sqlite3` module
   pydal already relies on (`register_converter`/`PARSE_DECLTYPES`,
   `adapters/sqlite.py:38,42-43`; `parsers/sqlite.py:20-28` expects native `date`/`datetime`
   already). No driver-swap problem. The real SQLite complications are the `for_update` and
   `delete()` items above, not type mapping.

## PoC

`select_async()` — build via `adapter._select_wcols`, execute via `psycopg` async, parse via
`adapter.parse`, zero pydal code touched — proved live against a disposable Postgres container:
field-for-field equal to `db(...).select()`, and a ticker interleaved with 20 real async queries
stays at ~5ms gaps (event loop never blocked). Script was a throwaway spike, not committed;
available on request / can be recreated from this doc in ~30 min.

## Size estimate (confirmed scope: Postgres + SQLite, all five ops)

- Shared async execution primitives (both backends, incl. SQLite's `for_update`/cascade
  special-cases): 1–2 days
- `collect_async`/`select_async`/`first_async` on top of `QueryBuilder.collect()`
  (`query_builder.py:611-674` already separates build/execute/shape into three steps — the async
  twin reuses steps 1 and 3 as-is, replaces step 2): 1 day
- Async connection/pool lifecycle per `TypeDAL` instance, `self.parser` override if asyncpg is
  ever added, SQLite custom-function registration (`create_function` equivalent to
  `after_connection()`): 1–2 days
- Relationship/join queries + `insert`/`update`/`delete` async twins incl. SQLite delete cascade:
  2–3 days
- Hardening + tests (parity per backend, two-connections guard, `Reference` docs): 2–3 days

**Total: ~1.5–2 weeks.** Excludes a durable fix for lazy `Reference` access (unresolved by
design) and full type-matrix verification beyond json/decimal/int/str/None.

## Next steps

Test-first, deliberately: constraint 2 (no public API change) means the test *is* the design
decision for the new methods' shape. Writing it before the implementation exists pins that down
instead of letting it drift out of implementation convenience.

1. Add `pytest-asyncio` as a dev dependency — no async test runner exists in this repo yet
   (`pyproject.toml` has no `asyncio`/`anyio` entry; `tests/conftest.py:7-30` is sync-only).
2. Write the test against the existing `dal_psql` fixture (`tests/conftest.py:23-30`): same query
   via `.collect()` vs `.collect_async()`, asserting parity on the two divergence points this
   spike actually found (jsonb→dict, decimal→Decimal), plus a formalized non-blocking/interleave
   assertion. This fails (method doesn't exist) until step 3.
   (Correction from an earlier draft of this doc: TypeDAL's `QueryBuilder.select()`,
   `query_builder.py:172-202`, is a lazy builder step — it returns a new `QueryBuilder` and does
   no I/O. `collect()`/`execute()` are the actual execution points, so those are what get async
   twins, not `select()`.)
3. Implement `collect_async` for Postgres in `src/typedal/` — ported from the PoC's
   `select_async()` helper, not a rewrite — to turn step 2 green.
4. Extend to SQLite and the remaining ops once the Postgres/select path is green in CI.
