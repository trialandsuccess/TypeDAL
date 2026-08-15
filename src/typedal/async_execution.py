"""
Backend-specific plumbing for TypeDAL's async execution path.

`TypeDAL` (core.py) owns the actual `_async` methods (`select_async`, `_get_async_pool`, ...) -
those are legitimately DAL-instance behavior. This module only holds the per-backend detail of
"how do you get an async connection for this dbengine", kept out of core.py so that stays about
the `TypeDAL` class itself, not about psycopg/aiosqlite specifics.

One factory per backend, registered by pydal's `adapter.dbengine` name in
`_ASYNC_POOL_FACTORIES`, rather than an if/elif chain - adding a new backend (e.g. MySQL) means
adding a function + a registry entry here, not editing branching logic in `TypeDAL._get_async_pool`.
"""

from __future__ import annotations

import asyncio
import contextlib
import typing as t

import pydal.objects

if t.TYPE_CHECKING:
    from pydal.adapters.base import SQLAdapter

    from .core import TypeDAL


# What pydal's `adapter._insert()` leaves behind to record whether the statement it just built
# carries a RETURNING clause: `(table._id, 1)` when it does, `None` when it does not
# (adapters/postgres.py). Backends without the concept never set it at all, hence None.
type LastInsert = tuple[pydal.objects.Field, int] | None


class AsyncCursor(t.Protocol):
    """
    The slice of a psycopg / aiosqlite cursor that the async execution path actually uses.

    A Protocol rather than the real driver cursor types, because both drivers are *optional*
    dependencies (`typedal[postgres-async]` / `typedal[sqlite-async]`): naming either one in a
    signature would make type-checking TypeDAL require it to be installed. Structural typing
    gets the checking without the dependency.

    Read-only properties rather than plain attributes so that both drivers match - psycopg and
    aiosqlite both expose `rowcount`/`lastrowid`/`description` as properties, and a Protocol
    declaring them as mutable attributes would reject exactly that.
    """

    @property
    def rowcount(self) -> int: ...

    @property
    def lastrowid(self) -> int | None: ...

    @property
    def description(self) -> t.Any: ...

    async def execute(self, sql: str, parameters: t.Any = ..., /) -> t.Any: ...

    async def fetchone(self) -> t.Any: ...

    # `Iterable`, not `Sequence`: aiosqlite declares `fetchall() -> Iterable[sqlite3.Row]`
    # (aiosqlite/cursor.py), so requiring a Sequence here would reject it.
    async def fetchall(self) -> t.Iterable[t.Any]: ...


class AsyncConnection(t.Protocol):
    """
    The slice of a psycopg / aiosqlite connection the async execution path uses. Same reasoning
    as `AsyncCursor`.

    `cursor()` is typed as returning a context manager, not a cursor or an awaitable, because
    that is the one shape both drivers share: psycopg's `cursor()` returns an `AsyncCursor`
    that doubles as an async context manager, while aiosqlite's is decorated to return a
    `Result[Cursor]` (aiosqlite/context.py) which is both awaitable *and* an async context
    manager. `async with conn.cursor() as cur` is what works for both.
    """

    def cursor(self) -> t.AsyncContextManager[AsyncCursor]: ...

    async def commit(self) -> None: ...

    async def rollback(self) -> None: ...

    async def close(self) -> None: ...


class AsyncConnectionPool(t.Protocol):
    """
    Common shape `select_async()` etc. need from either a real connection pool (Postgres) or a
    single-connection stand-in (SQLite).

    `commit()`/`rollback()` are part of this shape (not left to `TypeDAL.commit_async()` to
    figure out per backend) because what they need to do genuinely differs: psycopg_pool's
    `connection()` already commits/rolls back on context exit for every call (see
    `PostgresAsyncPool`), so there is never anything left open to commit; aiosqlite's default
    transaction mode does not auto-commit, so `SqliteAsyncConnection.commit()` has real work
    to do. Keeping both behind the same two methods keeps that difference out of core.py.
    """

    def connection(self) -> t.AsyncContextManager[AsyncConnection]: ...

    async def commit(self) -> None: ...

    async def rollback(self) -> None: ...

    async def close(self) -> None: ...


class PostgresAsyncPool:
    """
    Thin wrapper around `psycopg_pool.AsyncConnectionPool` giving it the same
    `commit()`/`rollback()` shape as `SqliteAsyncConnection`, even though there is nothing to
    do there: `pool.connection()` already applies "the normal connection context behaviour"
    (psycopg_pool's own docs) - commit on success, rollback on error - on every single
    `async with pool.connection() as conn:` use, so no transaction is ever left open between
    calls for these to act on. This means each `select_async`/`insert_async`/etc. call is its
    own committed transaction; there is currently no way to span one transaction across
    multiple async calls (a real limitation, not just an implementation gap - see the
    "two connections per request" hazard: since this and pydal's own sync connection are
    already separate, spanning transactions here as well would need its own connection
    checkout API, not built here).
    """

    def __init__(self, pool: t.Any) -> None:
        self._pool = pool

    def connection(self) -> t.AsyncContextManager[AsyncConnection]:
        return t.cast(t.AsyncContextManager[AsyncConnection], self._pool.connection())

    async def commit(self) -> None:
        pass

    async def rollback(self) -> None:
        pass

    async def close(self) -> None:
        await self._pool.close()


class SqliteAsyncConnection:
    """
    Minimal pool-like wrapper around a single aiosqlite connection.

    SQLite has no real concept of a connection pool the way Postgres does - pydal itself sets
    `pool_size = 0` for SQLite (adapters/sqlite.py), one connection is all there is. This
    gives it the same `.connection()`/`.commit()`/`.rollback()`/`.close()` shape as
    `PostgresAsyncPool` so `select_async()` etc. don't need to branch on backend.

    `connection()` commits on clean exit and rolls back on exception - unlike psycopg,
    aiosqlite does not do this on its own, and without it a write would still be open (and the
    table still locked for other readers/writers, including pydal's own sync connection) by
    the time an `_async` method returns. This makes every `_async` call its own committed
    transaction, matching what `PostgresAsyncPool` already gets for free from psycopg_pool.

    That promise only holds if calls do not overlap, hence `_lock`: a transaction belongs to
    the *connection*, and there is only one, so two coroutines inside `connection()` at the
    same time would share one transaction and the first to exit would decide for both -
    committing the other's half-finished write, or rolling back a write that had succeeded.
    psycopg_pool avoids this by handing out a different connection per caller; that is not an
    option here (pydal itself runs SQLite at `pool_size = 0`, adapters/sqlite.py), and for
    `sqlite:memory` it would actively break, since shared-cache mode answers a second
    concurrent writer with SQLITE_LOCKED, which no busy-timeout retries. Serializing costs
    concurrency SQLite does not have for writes anyway - it allows exactly one writer.
    """

    def __init__(self, conn: AsyncConnection) -> None:
        self._conn = conn
        # created here rather than bound eagerly: asyncio.Lock() only attaches to a loop on
        # first acquire, and this object is built inside `open_sqlite_async_connection()`.
        self._lock = asyncio.Lock()

    @contextlib.asynccontextmanager
    async def connection(self) -> t.AsyncIterator[AsyncConnection]:
        async with self._lock:
            try:
                yield self._conn
            except BaseException:
                await self._conn.rollback()
                raise
            else:
                await self._conn.commit()

    async def commit(self) -> None:
        # also under the lock: committing mid-way through another coroutine's `connection()`
        # block would commit its partial work, the same bug from the other direction.
        async with self._lock:
            await self._conn.commit()

    async def rollback(self) -> None:
        async with self._lock:
            await self._conn.rollback()

    async def close(self) -> None:
        await self._conn.close()


async def open_postgres_async_pool(db: "TypeDAL") -> AsyncConnectionPool:
    """
    Async pool factory for Postgres (registered in `_ASYNC_POOL_FACTORIES`).
    """
    try:
        import psycopg_pool
    except ImportError as e:  # pragma: no cover
        raise RuntimeError(
            "The async execution path requires `psycopg[binary,pool]`. Install via `typedal[postgres-async]`.",
        ) from e

    # pydal accepts 'postgres://', psycopg wants the standard 'postgresql://':
    uri = db._uri.replace("postgres://", "postgresql://", 1)
    pool = psycopg_pool.AsyncConnectionPool(uri, open=False)
    await pool.open()
    return PostgresAsyncPool(pool)


async def open_sqlite_async_connection(db: "TypeDAL") -> AsyncConnectionPool:
    """
    Async connection factory for SQLite (registered in `_ASYNC_POOL_FACTORIES`).
    """
    try:
        import aiosqlite
    except ImportError as e:  # pragma: no cover
        raise RuntimeError(
            "The async execution path requires `aiosqlite`. Install with `pip install typedal[sqlite-async]`.",
        ) from e

    adapter = db._adapter
    # Reuse pydal's own path/URI resolution and connect kwargs (adapters/sqlite.py) - in
    # particular the memory-mode shared-cache URI, so this connection sees the same in-memory
    # database as pydal's own sync connection.
    conn = await aiosqlite.connect(adapter.dbpath, **adapter.driver_args)

    # Mirror SQLite.after_connection() (adapters/sqlite.py): custom functions and PRAGMA
    # are per-connection state, and this connection is not the one pydal set those up on.
    await conn.create_function("web2py_extract", 2, adapter.web2py_extract)
    await conn.create_function("REGEXP", 2, adapter.web2py_regexp)
    if adapter.adapter_args.get("foreign_keys", True):
        await conn.execute("PRAGMA foreign_keys=ON;")

    return SqliteAsyncConnection(conn)


type PoolFactory = t.Callable[["TypeDAL"], t.Awaitable[AsyncConnectionPool]]

ASYNC_POOL_FACTORIES: dict[str, PoolFactory] = {
    "postgres": open_postgres_async_pool,
    "sqlite": open_sqlite_async_connection,
}


class AsyncPoolManager:
    """
    Owns the lazily-opened async connection for one `TypeDAL`: picking the factory for its
    backend, keeping creation single, and closing/reopening.

    Its own object rather than three attributes and two methods on `TypeDAL`, because the
    lifecycle has behaviour worth exercising on its own - "opened exactly once even when two
    coroutines race for the first use", "an unknown backend fails loudly" - and `factories` as
    a constructor argument makes that reachable directly, instead of only through a patched
    module global.
    """

    def __init__(self, db: "TypeDAL", factories: dict[str, PoolFactory] | None = None) -> None:
        self._db = db
        self._factories = ASYNC_POOL_FACTORIES if factories is None else factories
        self._pool: AsyncConnectionPool | None = None
        self._lock: asyncio.Lock | None = None
        self._lock_loop: asyncio.AbstractEventLoop | None = None

    @property
    def pool(self) -> AsyncConnectionPool | None:
        """
        The connection if one is currently open, else None. Never opens one - use `get()`.
        """
        return self._pool

    def _get_lock(self) -> asyncio.Lock:
        """
        The lock guarding creation, bound to the loop currently running.

        Not created once in `__init__`: an `asyncio.Lock` binds to the loop it is first used on
        and refuses use from another one, while a `TypeDAL` can outlive a loop (every
        pytest-asyncio test gets a fresh one, and `close()` explicitly supports reopening).
        Re-created when the loop changed - safe to decide here because this method never
        awaits, so two coroutines on one loop cannot interleave inside it and always come away
        with the same lock.
        """
        loop = asyncio.get_running_loop()
        if self._lock is None or self._lock_loop is not loop:
            self._lock = asyncio.Lock()
            self._lock_loop = loop

        return self._lock

    async def get(self) -> AsyncConnectionPool:
        """
        The async connection for this db, opening it on first use.

        Creation happens under the lock with the check repeated inside it: the factories await,
        so a plain `if self._pool is None: self._pool = await factory(...)` lets two coroutines
        whose first use overlaps both pass the check and both open one. Only one could be
        stored, and the other would be dropped without `close()` - a leaked pool, or on SQLite
        a leaked connection and its background thread.
        """
        if self._pool is not None:
            # fast path: already open, no need to take the lock at all
            return self._pool

        async with self._get_lock():
            if self._pool is None:
                dbengine = self._db._adapter.dbengine
                try:
                    factory = self._factories[dbengine]
                except KeyError:
                    raise NotImplementedError(
                        f"The async execution path is only implemented for "
                        f"{', '.join(self._factories)}, not {dbengine!r}.",
                    ) from None

                self._pool = await factory(self._db)

        return self._pool

    async def close(self) -> None:
        """
        Close the connection if one was ever opened, leaving this manager reusable.
        """
        if self._pool is not None:
            await self._pool.close()
            self._pool = None


async def postgres_lastrowid_async(
    adapter: SQLAdapter,
    table: pydal.objects.Table,
    cursor: AsyncCursor,
    last_insert: LastInsert,
) -> int | None:
    """
    Async twin of `Postgre.lastrowid()` (pydal adapters/postgres.py).

    `last_insert` is the value `adapter._insert()` set as a side effect of building the INSERT
    statement (postgres.py, set whenever the table has a standard `_id` column), passed
    in by `insert_async()` rather than read back off the adapter here. It has to be passed:
    `adapter._last_insert` is a property over `THREAD_LOCAL._pydal_last_insert_`
    (postgres.py), and every coroutine on this path shares one thread, so reading it
    after the intervening awaits would see whichever insert touched it last.

    Truthy means the id is already in the RETURNING result of the statement just executed, read
    here with a plain `fetchone()`, no extra round trip. Otherwise (a custom `_primarykey` not
    covered by RETURNING, or a `DEFAULT VALUES` insert) fall back to `currval()`, a real second
    query - on this same connection, so it sees this insert's sequence value.
    """
    if last_insert:
        row = await cursor.fetchone()
        return int(row[0])

    sequence_name = table._sequence_name
    await cursor.execute("SELECT currval(%s);" % adapter.adapt(sequence_name))
    row = await cursor.fetchone()
    return int(row[0])


async def sqlite_lastrowid_async(
    _adapter: SQLAdapter,
    _table: pydal.objects.Table,
    cursor: AsyncCursor,
    _last_insert: LastInsert,
) -> int | None:
    """
    Async twin of the base `SQLAdapter.lastrowid()` (pydal adapters/base.py), used by
    SQLite (no override there). `cursor.lastrowid` is a plain attribute, not awaitable, and
    needs no `last_insert` - it takes the argument only to share one strategy signature.
    """
    return cursor.lastrowid


# One lastrowid strategy per backend, mirroring `ASYNC_POOL_FACTORIES` - `insert_async()` looks
# this up by `adapter.dbengine` rather than branching, same reasoning as the pool factories above.
LASTROWID_STRATEGIES: dict[
    str,
    t.Callable[[SQLAdapter, pydal.objects.Table, AsyncCursor, LastInsert], t.Awaitable[int | None]],
] = {
    "postgres": postgres_lastrowid_async,
    "sqlite": sqlite_lastrowid_async,
}


async def base_delete_async(db: "TypeDAL", table: pydal.objects.Table, query: pydal.objects.Query) -> int | None:
    """
    Async twin of the base `SQLAdapter.delete()` (pydal adapters/base.py): plain
    build/execute sandwich, no cascade handling. Used directly for Postgres (no override
    there), and internally by `sqlite_delete_async` for the actual delete statement -
    mirroring how `SQLite.delete()` itself calls `super().delete()` for that part.
    """
    adapter = db._adapter
    sql = adapter._delete(table, query)

    pool = await db._get_async_pool()
    async with pool.connection() as conn, conn.cursor() as cur:
        await cur.execute(sql)
        try:
            return cur.rowcount
        except Exception:  # pragma: no cover
            # defensive, mirroring `adapter.delete()` (adapters/base.py):
            # neither driver's `rowcount` actually raises, it is a plain property.
            return None


async def sqlite_delete_async(db: "TypeDAL", table: pydal.objects.Table, query: pydal.objects.Query) -> int | None:
    """
    Async twin of `SQLite.delete()` (pydal adapters/sqlite.py) - NOT a plain sandwich:
    selects affected ids first, deletes, then recurses per cascaded FK with
    `ondelete=CASCADE`. Recursion goes through `db.delete_async()` again (not this function
    directly), so a cascaded delete on another table gets the dbengine-appropriate treatment
    too, same as the original.
    """
    id_rows = await db.select_async(query, table._id)
    deleted = [row[table._id.name] for row in id_rows]

    counter = await base_delete_async(db, table, query)

    if counter:
        for field in table._referenced_by:
            if field.type == "reference " + table._dalname and field.ondelete == "CASCADE":
                cascade_query = field.belongs(deleted)
                cascade_table = db._adapter.get_table(cascade_query)
                await db.delete_async(cascade_table, cascade_query)

    return counter


# One delete strategy per backend, same reasoning as `ASYNC_POOL_FACTORIES`/`LASTROWID_STRATEGIES`
# - SQLite's isn't a plain sandwich (see `sqlite_delete_async`), Postgres's is.
DELETE_STRATEGIES: dict[
    str,
    t.Callable[["TypeDAL", pydal.objects.Table, pydal.objects.Query], t.Awaitable[int | None]],
] = {
    "postgres": base_delete_async,
    "sqlite": sqlite_delete_async,
}
