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
    """

    def connection(self) -> t.AsyncContextManager[t.Any]: ...

    async def close(self) -> None: ...


class SqliteAsyncConnection:
    """
    Minimal pool-like wrapper around a single aiosqlite connection.

    SQLite has no real concept of a connection pool the way Postgres does - pydal itself sets
    `pool_size = 0` for SQLite (adapters/sqlite.py:26), one connection is all there is. This
    just gives it the same `.connection()`/`.close()` shape as `psycopg_pool.AsyncConnectionPool`
    so `select_async()` doesn't need to branch on backend.
    """

    def __init__(self, conn: t.Any) -> None:
        self._conn = conn

    @contextlib.asynccontextmanager
    async def connection(self) -> t.AsyncIterator[t.Any]:
        yield self._conn

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
    return t.cast(AsyncConnectionPool, pool)


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
