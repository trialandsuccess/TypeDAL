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
import contextvars
import typing as t

import pydal.objects
from pydal.helpers.classes import ExecutionHandler

if t.TYPE_CHECKING:
    from pydal.adapters.base import SQLAdapter

    from .core import TypeDAL


# What pydal's `adapter._insert()` leaves behind to record whether the statement it just built
# carries a RETURNING clause: `(table._id, 1)` when it does, `None` when it does not
# (adapters/postgres.py). Backends without the concept never set it at all, hence None.
type LastInsert = tuple[pydal.objects.Field, int] | None

# SQL verbs that open a transaction on whichever connection runs them, used by
# `SyncTransactionTracker` to decide whether a statement pydal just ran left uncommitted work.
# DDL is left out on purpose: `db.define()` migrates on the sync connection, and treating that
# as pending work would make the first `_async` call after any table definition raise.
#
# A text prefix is a weak test - a leading comment or a CTE hides the verb - and the async side
# deliberately no longer uses it: it asks the connection instead, see
# `UNCOMMITTED_WORK_STRATEGIES`. The sync side cannot do the same without also catching
# migration DDL, so it stays on the prefix for now.
WRITE_STATEMENTS = ("INSERT", "UPDATE", "DELETE", "REPLACE", "MERGE", "TRUNCATE")

# How long a file-backed SQLite connection waits for another one's write to finish before
# giving up with `database is locked`. SQLite allows a single writer at a time, so per-task
# connections queue here rather than failing outright; 5s is aiosqlite's own default order of
# magnitude and well past any statement a request should be issuing.
SQLITE_BUSY_TIMEOUT_MS = 5000

# Ceiling on concurrent per-task Postgres connections from one `TypeDAL`. Has to exceed 1 or
# `PostgresAsyncPool`'s per-task checkout deadlocks as soon as two tasks overlap; kept modest
# because every `TypeDAL` in the process draws from the same server-side max_connections.
POSTGRES_POOL_MAX_SIZE = 10


class TransactionBoundaryError(RuntimeError):
    """
    Base for the two ways a caller can end up on the wrong side of a transaction boundary.

    Both subclasses exist for the same reason: the alternative to raising is a silently wrong
    answer, and this class of bug only shows under concurrency, which is the worst place to
    find it. Catch this to handle either.
    """


class TransactionSplitError(TransactionBoundaryError):
    """
    Raised when sync and async work would be split across the two connections a `TypeDAL` has.

    pydal drives Postgres with psycopg2 and SQLite with sqlite3, both synchronous; the `_async`
    path needs psycopg3-async and aiosqlite. Those are separate connections and therefore
    separate transactions, so uncommitted work on one is invisible to the other. Within a
    single request that is read-your-own-writes quietly disappearing: on Postgres the second
    path simply does not see the row, and on SQLite it blocks on the table lock instead.

    Rather than let either happen, both paths refuse to run while the other holds an open
    transaction. Commit or roll back the side you finished with before using the other one.

    Warning-and-continuing was considered and does not survive contact with SQLite. Measured on
    the same scenario: Postgres returns the committed rows (wrong but warnable), a plain SQLite
    read raises `database table is locked` and cannot proceed at all, and SQLite with
    `PRAGMA read_uncommitted=1` returns *more* rows than Postgres - including ones a rollback
    then deletes. Three answers to identical code, two of them silent. Raising is the only
    behaviour both backends can actually share.
    """


class ConcurrentTransactionError(TransactionBoundaryError):
    """
    Raised when two asyncio tasks would share one transaction on a `sqlite:memory` database.

    Postgres and file-backed SQLite both hand each task its own connection, so their
    transactions are independent. `sqlite:memory` cannot: a second connection only reaches the
    same database through shared-cache mode, which answers a concurrent writer with
    SQLITE_LOCKED. One connection means one transaction, and sharing it means one task's
    `rollback_async()` destroys another task's uncommitted rows.

    So the second task is refused instead. pydal's own synchronous connections hit this same
    wall between two threads on one `sqlite:memory` - this raises deliberately, and says why,
    where pydal surfaces the driver's `database table is locked`.
    """


class SyncTransactionTracker(ExecutionHandler):
    """
    Records whether pydal's own connection has uncommitted writes, and refuses to run a sync
    statement while the async connection has some (see `TransactionSplitError`).

    An `ExecutionHandler` rather than wrappers around `insert`/`update`/`delete`, because this
    has to see *every* statement reaching the adapter - `executesql()`, pydal internals and
    anything a caller reaches around TypeDAL for included - and `DAL.execution_handlers` is
    pydal's own supported seam for that (it is where `TimingHandler` lives).

    Flags live on the `TypeDAL`, not here: pydal builds a handler instance per execution, so
    this object is the wrong place to keep anything that has to outlive one statement.
    """

    def before_execute(self, command: str) -> None:
        """
        Check the async side is settled, then note whether this statement opens a transaction.
        """
        db = getattr(self.adapter, "db", None)
        if db is None:  # pragma: no cover - adapter detached during close()
            return

        # TypeDAL owns the settle-then-check decision, so the sync side cannot accidentally ask
        # the predicate without first reclaiming an abandoned sqlite:memory transaction.
        if db._has_pending_async_writes():
            raise TransactionSplitError(
                "The async connection has uncommitted writes, which this synchronous statement "
                "would not see. Call `await db.commit_async()` or `await db.rollback_async()` "
                "first. If those writes belong to another task, wait for it to settle them: "
                "neither call ends a transaction this task does not own.",
            )

        if command.lstrip().upper().startswith(WRITE_STATEMENTS):
            db._sync_pending = True


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
    figure out per backend) because what they act on genuinely differs: `PostgresAsyncPool`
    ends the transaction on the connection checked out for *this task* and returns it to the
    pool, while `SqliteAsyncConnection` ends the one transaction there is. Keeping both behind
    the same two methods keeps that difference out of core.py.

    Either way `connection()` leaves the transaction open, so `_async` writes obey pydal's
    contract: nothing is durable until the caller commits.
    """

    def connection(self) -> t.AsyncContextManager[AsyncConnection]: ...

    async def commit(self) -> None: ...

    async def rollback(self) -> None: ...

    async def close(self) -> None: ...

    def settle_abandoned_sync(self) -> bool: ...


def _spawn_reclaim(coro: t.Coroutine[t.Any, t.Any, None], tasks: "set[asyncio.Task[None]]") -> None:
    """
    Run a reclaim coroutine on the running loop, holding a reference until it finishes.

    `add_done_callback` is synchronous, so the actual rollback/close has to be scheduled. The
    reference is the point: the event loop only keeps a *weak* one to a task, so a bare
    `create_task(...)` can be garbage-collected part-way through its rollback. That surfaces as
    a connection which is never handed back - only under load, and never twice in the same
    place.

    Best-effort, like everything on the reclaim path: with no running loop there is nothing to
    schedule on, and closing the pool is what reclaims the connection instead.
    """
    try:
        task = asyncio.get_running_loop().create_task(coro)
    except RuntimeError:
        # no running loop; closing the coroutine keeps it from warning about never being awaited
        coro.close()
        return

    tasks.add(task)
    task.add_done_callback(tasks.discard)


class PostgresAsyncPool:
    """
    Wrap `psycopg_pool.AsyncConnectionPool` with one connection per asyncio task.

    A task keeps its connection until `commit()` or `rollback()` so its `_async` calls share a
    transaction. Abandoned task connections are rolled back and returned by a done-callback.
    """

    def __init__(self, pool: t.Any) -> None:
        self._pool = pool
        self._current: contextvars.ContextVar[t.Any] = contextvars.ContextVar(
            f"typedal_async_conn_{id(self):x}",
            default=None,
        )
        self._checked_out: set[t.Any] = set()
        # see `_spawn_reclaim()` - without this the reclaim tasks can be collected mid-flight.
        self._reclaim_tasks: "set[asyncio.Task[None]]" = set()

    def _own_connection(self) -> t.Any:
        """
        The connection this task acquired, or None - including when the value it can see was
        acquired by a different task.

        That last part is the whole reason the entry stores its owner. A `ContextVar` set in a
        parent is *copied into* every task the parent later spawns, so two coroutines under one
        `asyncio.gather()` would both see the parent's connection and hand it around as if it
        were theirs - one task's commit closing the transaction the other was still writing to.
        Isolation only holds if an inherited entry is treated as absent.
        """
        entry = self._current.get()
        if entry is None:
            return None

        owner, conn = entry
        return conn if owner is asyncio.current_task() else None

    async def _acquire(self) -> t.Any:
        """
        This task's connection, checking one out of the pool on first use.
        """
        if (conn := self._own_connection()) is not None:
            return conn

        conn = await self._pool.getconn()
        self._current.set((asyncio.current_task(), conn))
        self._checked_out.add(conn)

        if task := asyncio.current_task():
            task.add_done_callback(lambda _task: self._reclaim(conn))

        return conn

    def _reclaim(self, conn: t.Any) -> None:
        """
        Return a connection its task never ended the transaction on (see the class docstring).

        Sync, because that is all `add_done_callback` can be, so the actual work is scheduled.
        Everything here is best-effort: the loop may already be shutting down, in which case
        closing the pool is what reclaims the connection instead.
        """
        if conn not in self._checked_out:
            # the ordinary case - commit() or rollback() already handed it back
            return

        async def _rollback_and_return() -> None:
            # Claim before doing anything, and claim by *removing* from `_checked_out`. The
            # check-and-discard runs before the first await, so it is atomic against the other
            # two paths that also return connections (`_release` and `close`), and whoever
            # claims first is the only one that acts. Holding membership across the await
            # instead let `close()` return the same connection concurrently, which psycopg
            # answers with `can't return connection to pool, it doesn't come from any pool`.
            if conn not in self._checked_out:
                return

            self._checked_out.discard(conn)

            with contextlib.suppress(Exception):
                await conn.rollback()

            try:
                await self._pool.putconn(conn)
            except Exception:
                # The pool is gone or refused it, so this connection can never be handed back.
                # Close it rather than re-tracking it: `close()` has already run by the time
                # that happens, so nothing would ever drain the set again and the socket would
                # stay open for the life of the process - which exhausts the server's
                # max_connections one abandoned task at a time.
                with contextlib.suppress(Exception):
                    await conn.close()

        _spawn_reclaim(_rollback_and_return(), self._reclaim_tasks)

    async def _release(self, conn: t.Any) -> None:
        """
        Hand this task's connection back, after its transaction has been ended.

        Handed the connection rather than reading the `ContextVar` again: both callers have
        just read it to decide there was a transaction to end at all, and a second read is one
        more opportunity for the two to disagree about which connection this is.
        """
        self._current.set(None)
        self._checked_out.discard(conn)
        await self._pool.putconn(conn)

    @contextlib.asynccontextmanager
    async def connection(self) -> t.AsyncIterator[AsyncConnection]:
        # no commit and no rollback on exit: whether this statement stands is the caller's
        # call, made via commit()/rollback(), exactly as it is on pydal's sync connection.
        yield t.cast(AsyncConnection, await self._acquire())

    def settle_abandoned_sync(self) -> bool:
        """Per-task backends reclaim abandoned connections through their own done-callback."""
        return True

    async def commit(self) -> None:
        if (conn := self._own_connection()) is None:
            return

        await conn.commit()
        await self._release(conn)

    async def rollback(self) -> None:
        if (conn := self._own_connection()) is None:
            return

        await conn.rollback()
        await self._release(conn)

    async def close(self) -> None:
        """
        Close the pool, and every connection still checked out of it.

        `psycopg_pool.close()` only closes the connections currently *idle in* the pool - one
        that a task took and never gave back is not reachable from it, so closing the pool
        leaves that socket open to the server. A task that ends without committing is exactly
        that case, and one leaked connection per such task exhausts `max_connections` in a
        long-running process (or partway through a test suite).

        These are closed outright rather than returned, because the pool they would go back to
        is about to be closed anyway. Claiming the whole set in one statement, with no await in
        between, keeps this atomic against a `_rollback_and_return` racing to claim the same
        connection.
        """
        checked_out, self._checked_out = list(self._checked_out), set()

        for conn in checked_out:
            with contextlib.suppress(Exception):
                await conn.close()

        await self._pool.close()


class SqliteAsyncConnection:
    """
    Pool-like wrapper around the one connection used for `sqlite:memory`.

    Transactions remain open until `commit()` or `rollback()`. Because tasks cannot receive
    separate transactions, a second task is refused while another owns one. `_lock` serializes
    statements; `_owner` defines the transaction boundary.
    """

    def __init__(self, conn: AsyncConnection) -> None:
        self._conn = conn
        # created here rather than bound eagerly: asyncio.Lock() only attaches to a loop on
        # first acquire, and this object is built inside `open_sqlite_async_connection()`.
        self._lock = asyncio.Lock()
        # the task whose transaction is currently open, if any. Not a ContextVar: the point is
        # for *other* tasks to see it and be refused, which is the opposite of what a
        # ContextVar's per-task isolation provides.
        self._owner: "asyncio.Task[t.Any] | None" = None
        # tasks with a reclaim callback armed, so one is armed per task rather than per
        # statement. Entries are dropped when the callback fires.
        self._reclaimable: "set[asyncio.Task[t.Any]]" = set()

    def _is_owned_elsewhere(self) -> bool:
        """
        Whether the open transaction belongs to a task other than the calling one.

        No `done()` term, deliberately. On the `connection()` path the caller settles a
        finished owner first; on the direct `commit()`/`rollback()` path a finished owner is
        still somebody else's transaction and must therefore no-op. Treating a finished owner
        as absent instead - which this used to do - is how a non-owner ended up committing or
        rolling back another task's writes.

        `asyncio.current_task()` answers None off-task, and None is never stored as an owner,
        so an off-task caller correctly reads any owner as somebody else's.
        """
        return self._owner is not None and self._owner is not asyncio.current_task()

    def _abandoned_owner(self) -> "asyncio.Task[t.Any] | None":
        """The finished task whose open transaction needs reclaiming, or None."""
        owner = self._owner
        if owner is None or owner is asyncio.current_task() or not owner.done():
            return None
        return owner

    async def _settle_abandoned_owner(self) -> None:
        """
        Roll back a transaction whose owning task ended without committing it.

        Must be called with `_lock` held. `PostgresAsyncPool` and `SqliteAsyncPool` both hand
        the abandoned connection back and roll it back on the way (`_reclaim`); there is no
        connection to hand back here, so the transaction itself is what gets reclaimed.

        `settle_abandoned_sync()` normally gets there first, from the task's done-callback or
        from `TypeDAL._settle_abandoned_async_writes()`. This is the deterministic backstop for
        when neither has run yet, or when the sync path could not act because the lock was held.

        If the rollback fails, the owner is left set so the next task is refused rather than
        allowed to inherit a transaction that could not be reclaimed.
        """
        if self._abandoned_owner() is None:
            return

        try:
            await self._conn.rollback()
        except Exception:  # pragma: no cover - a hard rollback failure is not reachable through the public API
            return

        self._owner = None

    def _refuse_if_owned_elsewhere(self) -> None:
        """
        Refuse the caller if a different task holds the open transaction.

        Must be called with `_lock` held. Checking on the way *to* the lock instead lets a
        second task read `_owner` while the first is still awaiting inside its `connection()`
        block - before that block's `finally` has recorded the ownership - so it passes the
        check, queues on the lock, and then walks straight into the transaction it should have
        been refused from. Under the lock, the first task's ownership is always already visible.
        """
        if self._is_owned_elsewhere():
            raise ConcurrentTransactionError(
                "Another task holds an open transaction on this sqlite:memory database, and "
                "SQLite cannot give the two of them separate ones - shared-cache mode refuses "
                "a second concurrent writer. Commit or roll back that task before starting "
                "here, or use a file-backed database, which does get a connection per task.",
            )

    def _take_ownership_if_in_transaction(self) -> None:
        """
        Own the connection if the statement just run left a transaction open, else release it.

        Ownership tracks `in_transaction` rather than "used the connection at all", because
        only a write opens a transaction here - sqlite3 implicitly BEGINs before DML and leaves
        SELECT and DDL alone. Claiming on every use instead would mean a single `collect_async()`
        locked every other task out of the database until the reader happened to commit, which
        readers have no reason to do.

        Taking ownership also arms the done-callback that reclaims the transaction if this task
        never ends it, the same safety net the two real pools arm at checkout. Armed here
        rather than on entry to `connection()` because this is the moment there is something to
        reclaim; `_reclaimable` keeps one callback per task rather than one per statement.
        """
        owner = asyncio.current_task() if self._conn.in_transaction else None  # ty: ignore[unresolved-attribute]
        self._owner = owner

        if owner is not None and owner not in self._reclaimable:
            self._reclaimable.add(owner)
            owner.add_done_callback(self._reclaim)

    def settle_abandoned_sync(self) -> bool:
        """
        Roll back a finished owner's transaction for synchronous callers.

        Return `False` when the connection is busy or rollback fails, so callers continue to
        treat the async transaction as pending.
        """
        if self._abandoned_owner() is None:
            return True

        if self._lock.locked():
            return False

        try:
            # aiosqlite's own `in_transaction` reaches through `_conn` the same way; this is
            # the sqlite3 connection behind the queue, not the aiosqlite wrapper.
            self._conn._conn.rollback()  # type: ignore[attr-defined] # ty: ignore[unresolved-attribute]
        except Exception:  # pragma: no cover - a hard rollback failure is not reachable through the public API
            return False

        self._owner = None
        return True

    def _reclaim(self, task: "asyncio.Task[t.Any]") -> None:
        """
        Roll back the transaction of a task that ended without committing it.

        Re-checks ownership rather than trusting the callback fired: by the time it runs the
        task may have committed (so `_owner` is None), or another task may already hold the
        transaction, and rolling *that* back is the very thing this class exists to prevent.

        A False from `settle_abandoned_sync()` is safe to ignore here: `_settle_abandoned_owner()`
        runs on the next async `connection()`, so the still-open owner is refused rather than
        inherited.
        """
        self._reclaimable.discard(task)

        if self._owner is not task:
            # the ordinary case - commit()/rollback() already ended it
            return

        self.settle_abandoned_sync()

    @contextlib.asynccontextmanager
    async def connection(self) -> t.AsyncIterator[AsyncConnection]:
        async with self._lock:
            await self._settle_abandoned_owner()
            self._refuse_if_owned_elsewhere()
            try:
                yield self._conn
            finally:
                # in a finally: a statement that raised may still have opened the transaction,
                # and leaving it unowned would let another task walk into it.
                self._take_ownership_if_in_transaction()

    async def _end_transaction(self, end: t.Callable[[], t.Awaitable[None]]) -> None:
        """
        End this task's transaction, or no-op when the transaction belongs elsewhere.

        Runs under the lock so a commit cannot land halfway through another coroutine's
        `connection()` block and write out a statement it has not finished issuing.

        If another task owns the transaction, this does nothing. `PostgresAsyncPool` and
        `SqliteAsyncPool` already no-op for a task that holds no connection; unlike them, the
        connection here is shared, so acting anyway would commit or roll back writes the owner
        has not finished issuing. The guard stays silent rather than raising because both
        `commit_async()` and `rollback_async()` are what a caller reaches for while cleaning
        up, often in a `finally`, where raising would mask whatever sent it there.
        """
        async with self._lock:
            if self._is_owned_elsewhere():
                return

            await end()
            self._owner = None

    async def commit(self) -> None:
        await self._end_transaction(self._conn.commit)

    async def rollback(self) -> None:
        await self._end_transaction(self._conn.rollback)

    async def close(self) -> None:
        await self._conn.close()


class SqliteAsyncPool:
    """
    A connection per asyncio task for a file-backed SQLite database, giving it the same
    per-task transaction boundary `PostgresAsyncPool` gives Postgres.

    Possible here and not for `sqlite:memory` because a file has a path two connections can
    both open. WAL mode is what makes it worth doing - without it a writer blocks readers on a
    database-wide lock and separate connections buy nothing. SQLite still permits exactly one
    writer at a time, so two writing tasks serialize on `busy_timeout` rather than running
    concurrently; that is a throughput limit, not a correctness one, and it surfaces as
    `database is locked` if a task holds a write open longer than the timeout.

    Connections are opened per task rather than pooled and reused. SQLite connections are cheap
    (no handshake, no network) so there is little to gain from recycling, and closing on
    release keeps the file-handle count bounded by concurrent tasks rather than by peak usage.
    """

    def __init__(self, db: "TypeDAL") -> None:
        self._db = db
        self._current: contextvars.ContextVar[t.Any] = contextvars.ContextVar(
            f"typedal_sqlite_conn_{id(self):x}",
            default=None,
        )
        # every connection handed out and not yet closed, so close() can reach the ones whose
        # tasks ended without committing. Same reasoning as `PostgresAsyncPool._checked_out`.
        self._open: set[t.Any] = set()
        # see `_spawn_reclaim()` - without this the reclaim tasks can be collected mid-flight.
        self._reclaim_tasks: "set[asyncio.Task[None]]" = set()

    def _own_connection(self) -> t.Any:
        """
        The connection this task opened, or None - see `PostgresAsyncPool._own_connection` for
        why an entry inherited from a parent task has to count as None.
        """
        entry = self._current.get()
        if entry is None:
            return None

        owner, conn = entry
        return conn if owner is asyncio.current_task() else None

    async def _acquire(self) -> t.Any:
        if (conn := self._own_connection()) is not None:
            return conn

        conn = await _connect_sqlite_async(self._db)
        self._current.set((asyncio.current_task(), conn))
        self._open.add(conn)

        if task := asyncio.current_task():
            task.add_done_callback(lambda _task: self._reclaim(conn))

        return conn

    def _reclaim(self, conn: t.Any) -> None:
        """
        Close a connection whose task ended without committing or rolling back.

        Handed its connection directly rather than reading the `ContextVar`, because
        `add_done_callback` runs in the loop's context and not the finished task's.
        """
        if conn not in self._open:
            return

        async def _rollback_and_close() -> None:
            # claim by removing from `_open`, before the first await, so this is atomic against
            # `_release` and `close()` - see `PostgresAsyncPool._reclaim`. Unlike there, the
            # connection is closed rather than returned either way, so a lost claim only means
            # somebody else already closed it.
            if conn not in self._open:
                return

            self._open.discard(conn)

            with contextlib.suppress(Exception):
                await conn.rollback()
            with contextlib.suppress(Exception):
                await conn.close()

        _spawn_reclaim(_rollback_and_close(), self._reclaim_tasks)

    async def _release(self, conn: t.Any) -> None:
        # handed the connection for the same reason as `PostgresAsyncPool._release`.
        self._current.set(None)
        self._open.discard(conn)
        await conn.close()

    @contextlib.asynccontextmanager
    async def connection(self) -> t.AsyncIterator[AsyncConnection]:
        # no commit and no rollback on exit - the caller's transaction spans its calls and ends
        # when it says so, exactly as on pydal's sync connection.
        yield t.cast(AsyncConnection, await self._acquire())

    def settle_abandoned_sync(self) -> bool:
        """Per-task backends reclaim abandoned connections through their own done-callback."""
        return True

    async def commit(self) -> None:
        if (conn := self._own_connection()) is None:
            return

        await conn.commit()
        await self._release(conn)

    async def rollback(self) -> None:
        if (conn := self._own_connection()) is None:
            return

        await conn.rollback()
        await self._release(conn)

    async def close(self) -> None:
        for conn in list(self._open):
            self._open.discard(conn)
            with contextlib.suppress(Exception):
                await conn.rollback()
            with contextlib.suppress(Exception):
                await conn.close()


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
    # min_size=1 rather than psycopg_pool's default of 4: connections are held for the length
    # of a task's transaction now, not one statement, but opening four server connections
    # before anyone has asked for one is pure cost - it multiplies every short-lived `TypeDAL`
    # by four against the server's max_connections.
    # max_size must be passed explicitly: psycopg_pool defaults it to min_size, so min_size=1
    # alone would cap the pool at a single connection and deadlock the second concurrent task
    # for the full 30s checkout timeout.
    pool = psycopg_pool.AsyncConnectionPool(uri, min_size=1, max_size=POSTGRES_POOL_MAX_SIZE, open=False)
    await pool.open()
    return PostgresAsyncPool(pool)


def sqlite_is_in_memory(adapter: "SQLAdapter") -> bool:
    """
    Whether pydal resolved this SQLite database to an in-memory one.

    Read off `dbpath` rather than the URI, because that is what pydal itself produced: for
    `sqlite:memory` it builds `file:<uuid>?mode=memory&cache=shared` and sets
    `driver_args["uri"] = True` (adapters/sqlite.py), and it is the shared-cache part that
    decides whether a second connection is possible at all.
    """
    return "mode=memory" in str(adapter.dbpath)


async def _connect_sqlite_async(db: "TypeDAL") -> t.Any:
    """
    One aiosqlite connection configured the way pydal configures its own.
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

    if not sqlite_is_in_memory(adapter):
        # SQLite still allows a single writer, so two writing tasks queue here rather than
        # failing outright. Genuinely per-connection, unlike journal_mode - see
        # `enable_sqlite_wal()`.
        await conn.execute(f"PRAGMA busy_timeout={SQLITE_BUSY_TIMEOUT_MS};")

    return conn


async def enable_sqlite_wal(db: "TypeDAL") -> None:
    """
    Put a file-backed SQLite database into WAL mode, once.

    WAL is what lets one task read while another holds a write open; without it they serialize
    on a database-wide lock and per-task connections buy nothing.

    Deliberately not part of `_connect_sqlite_async`. `journal_mode` is a persistent property
    of the database *file*, not of a connection, so setting it per connection is both redundant
    and actively harmful: switching into WAL needs an exclusive lock, and a second task opening
    its connection while the first holds a write transaction gets `database is locked` for a
    setting that was already applied. Done here instead, on its own connection, before the pool
    exists and therefore before any task can be writing.

    Failure is tolerated. A database that cannot be switched (on a filesystem that does not
    support WAL, say) still works through `SqliteAsyncPool` - tasks just contend more.
    """
    conn = await _connect_sqlite_async(db)
    try:
        with contextlib.suppress(Exception):
            await conn.execute("PRAGMA journal_mode=WAL;")
            await conn.commit()
    finally:
        await conn.close()


async def open_sqlite_async_connection(db: "TypeDAL") -> AsyncConnectionPool:
    """
    Async connection factory for SQLite (registered in `_ASYNC_POOL_FACTORIES`).

    Two shapes, because the two kinds of SQLite database genuinely differ. A file-backed one
    supports a connection per task, so it gets `SqliteAsyncPool` and the same per-task
    transaction boundary Postgres has. `sqlite:memory` does not - see
    `ConcurrentTransactionError` - so it gets the single-connection `SqliteAsyncConnection`,
    which refuses a second task rather than merging it into the first one's transaction.
    """
    if sqlite_is_in_memory(db._adapter):
        return SqliteAsyncConnection(await _connect_sqlite_async(db))

    await enable_sqlite_wal(db)
    return SqliteAsyncPool(db)


# A note the factories above share: `TypeDAL(..., after_connection=...)` does NOT run on these
# connections. That hook is pydal's, is handed the pydal adapter, and drives the sync cursor
# (connection.py) - there is no faithful way to replay it against a connection the adapter does
# not own, and a hook reaching into driver internals (`adapter.connection.create_function`)
# could not be replayed at all. What the factories do instead is mirror the backend's own
# `after_connection()` setup, above, so the async connection matches pydal's on everything
# pydal itself configures. pydal is not absolute about the hook either: a connection recycled
# from its global pool comes back with `run_hooks=False`.
# Covered by `test_after_connection_hook_does_not_reach_the_async_connection`.

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


# Postgres command tags that report a statement which changed nothing. Everything else either
# modified data or changed schema, and therefore left work the sync connection cannot see.
# Inverted like this on purpose: an unrecognised tag then counts as a write, which costs a held
# connection at worst, where the other way round costs a silently discarded statement.
POSTGRES_READ_ONLY_COMMAND_TAGS = frozenset(
    {"SELECT", "SHOW", "EXPLAIN", "FETCH", "MOVE", "CLOSE", "SET", "RESET", "BEGIN", "COMMIT", "ROLLBACK"},
)


def postgres_left_uncommitted_work(_conn: AsyncConnection, cur: AsyncCursor) -> bool:
    """
    Whether the statement this psycopg cursor just ran left uncommitted work.

    Read off the command tag the *server* sent back (`INSERT 0 1`, `UPDATE 3`, `CREATE TABLE`,
    `SELECT 5`), not off the SQL that was sent. That is what makes this reliable where a text
    prefix is not: a CTE-wrapped `INSERT`, a statement behind a leading comment and DDL all
    report their real command here.

    `conn.info.transaction_status` cannot answer this on Postgres - psycopg opens a transaction
    for a plain `SELECT` too, so it reports `INTRANS` for statements with nothing to commit.
    """
    tag = str(getattr(cur, "statusmessage", "") or "").split(" ", 1)[0].upper()
    return tag not in POSTGRES_READ_ONLY_COMMAND_TAGS


def sqlite_left_uncommitted_work(conn: AsyncConnection, _cur: AsyncCursor) -> bool:
    """
    Whether the statement this aiosqlite connection just ran left uncommitted work.

    sqlite3 implicitly BEGINs before DML and leaves SELECT and DDL in autocommit, so
    `in_transaction` *is* the question being asked - no command tag needed (and none exists).

    That DDL is excluded is the driver's behaviour, not a choice made here: a SQLite
    `CREATE TABLE` is durable the moment it runs, so there is nothing pending to report.
    """
    # not on the `AsyncConnection` protocol: psycopg has no counterpart, this is aiosqlite's.
    return bool(conn.in_transaction)  # type: ignore[attr-defined] # ty: ignore[unresolved-attribute]


# One "did this leave uncommitted work" strategy per backend, mirroring `ASYNC_POOL_FACTORIES`.
# Used by `executesql_async()`, which is the one `_async` method handed arbitrary SQL and so the
# only one that cannot know up front whether it is about to write.
UNCOMMITTED_WORK_STRATEGIES: dict[str, t.Callable[[AsyncConnection, AsyncCursor], bool]] = {
    "postgres": postgres_left_uncommitted_work,
    "sqlite": sqlite_left_uncommitted_work,
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
    with db._mark_async_pending():
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
    # `_hold_connection`: the delete below (and the cascades after it) have to share this
    # snapshot's transaction, or the rows cascaded to are chosen from ids read outside it.
    id_rows = await db.select_async(query, table._id, _hold_connection=True)
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
