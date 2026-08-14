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

import contextlib
import typing as t

if t.TYPE_CHECKING:
    from .core import TypeDAL


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

    def connection(self) -> t.AsyncContextManager[t.Any]: ...

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

    def connection(self) -> t.AsyncContextManager[t.Any]:
        return t.cast(t.AsyncContextManager[t.Any], self._pool.connection())

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
    `pool_size = 0` for SQLite (adapters/sqlite.py:26), one connection is all there is. This
    gives it the same `.connection()`/`.commit()`/`.rollback()`/`.close()` shape as
    `PostgresAsyncPool` so `select_async()` etc. don't need to branch on backend.

    `connection()` commits on clean exit and rolls back on exception - unlike psycopg,
    aiosqlite does not do this on its own, and without it a write would still be open (and the
    table still locked for other readers/writers, including pydal's own sync connection) by
    the time an `_async` method returns. This makes every `_async` call its own committed
    transaction, matching what `PostgresAsyncPool` already gets for free from psycopg_pool.
    """

    def __init__(self, conn: t.Any) -> None:
        self._conn = conn

    @contextlib.asynccontextmanager
    async def connection(self) -> t.AsyncIterator[t.Any]:
        try:
            yield self._conn
        except BaseException:
            await self._conn.rollback()
            raise
        else:
            await self._conn.commit()

    async def commit(self) -> None:
        await self._conn.commit()

    async def rollback(self) -> None:
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
    # Reuse pydal's own path/URI resolution and connect kwargs (adapters/sqlite.py:25-38) - in
    # particular the memory-mode shared-cache URI, so this connection sees the same in-memory
    # database as pydal's own sync connection.
    conn = await aiosqlite.connect(adapter.dbpath, **adapter.driver_args)

    # Mirror SQLite.after_connection() (adapters/sqlite.py:82-86): custom functions and PRAGMA
    # are per-connection state, and this connection is not the one pydal set those up on.
    await conn.create_function("web2py_extract", 2, adapter.web2py_extract)
    await conn.create_function("REGEXP", 2, adapter.web2py_regexp)
    if adapter.adapter_args.get("foreign_keys", True):
        await conn.execute("PRAGMA foreign_keys=ON;")

    return SqliteAsyncConnection(conn)


ASYNC_POOL_FACTORIES: dict[str, t.Callable[["TypeDAL"], t.Awaitable[AsyncConnectionPool]]] = {
    "postgres": open_postgres_async_pool,
    "sqlite": open_sqlite_async_connection,
}


async def postgres_lastrowid_async(adapter: t.Any, table: t.Any, cursor: t.Any) -> t.Any:
    """
    Async twin of `Postgre.lastrowid()` (pydal adapters/postgres.py:142-147).

    `adapter._last_insert` was already set as a side effect of the `_insert()` call that built
    the INSERT statement (postgres.py:149-162, sets it whenever the table has a standard `_id`
    column) - if so, the id is already in the RETURNING result of the statement just executed,
    read here with a plain `fetchone()`, no extra round trip. Otherwise (tables with a custom
    `_primarykey` not covered by RETURNING), fall back to `currval()`, a real second query.
    """
    if getattr(adapter, "_last_insert", None):
        row = await cursor.fetchone()
        return int(row[0])

    sequence_name = table._sequence_name
    await cursor.execute("SELECT currval(%s);" % adapter.adapt(sequence_name))
    row = await cursor.fetchone()
    return int(row[0])


async def sqlite_lastrowid_async(adapter: t.Any, table: t.Any, cursor: t.Any) -> t.Any:
    """
    Async twin of the base `SQLAdapter.lastrowid()` (pydal adapters/base.py:529-530), used by
    SQLite (no override there). `cursor.lastrowid` is a plain attribute, not awaitable.
    """
    return cursor.lastrowid


# One lastrowid strategy per backend, mirroring `ASYNC_POOL_FACTORIES` - `insert_async()` looks
# this up by `adapter.dbengine` rather than branching, same reasoning as the pool factories above.
LASTROWID_STRATEGIES: dict[str, t.Callable[[t.Any, t.Any, t.Any], t.Awaitable[t.Any]]] = {
    "postgres": postgres_lastrowid_async,
    "sqlite": sqlite_lastrowid_async,
}


async def base_delete_async(db: "TypeDAL", table: t.Any, query: t.Any) -> t.Any:
    """
    Async twin of the base `SQLAdapter.delete()` (pydal adapters/base.py:604-610): plain
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
        except Exception:  # noqa: BLE001
            return None


async def sqlite_delete_async(db: "TypeDAL", table: t.Any, query: t.Any) -> t.Any:
    """
    Async twin of `SQLite.delete()` (pydal adapters/sqlite.py:93-104) - NOT a plain sandwich:
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
DELETE_STRATEGIES: dict[str, t.Callable[["TypeDAL", t.Any, t.Any], t.Awaitable[t.Any]]] = {
    "postgres": base_delete_async,
    "sqlite": sqlite_delete_async,
}
