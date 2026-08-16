"""
Async support for TypeDAL, built on thread offload.

There is no async database driver here and no second dialect: every statement is pydal's own
unmodified sync code, executed on a worker thread so the event loop stays free. pydal keeps its
connection in a thread local, so pinning one thread per unit of work gives each unit its own
connection and - therefore - pydal's own transaction model, unchanged.

Two ways in:

- a flat `*_async` call, which runs on a borrowed worker and **commits before returning**;
- `async with db.session():`, which pins one worker (and one connection, and one transaction)
  for its whole scope and commits at the end - or rolls back if the block raised.

The active session lives in a `ContextVar`, so model methods find it without a handle being
passed around. Contextvars are task-scoped: a task spawned inside a session copies the context,
which is exactly the case that must *not* silently join someone else's transaction, so every
binding is stamped with the task that created it and ignored anywhere else.
"""

import asyncio
import concurrent.futures
import contextlib
import contextvars
import functools
import threading
import typing as t
from collections import deque
from types import TracebackType

if t.TYPE_CHECKING:  # pragma: no cover
    from .core import TypeDAL

__all__ = [
    "ACTIVE_SESSIONS",
    "AsyncSession",
    "ConnectionWorker",
    "ConnectionWorkerPool",
    "SessionBinding",
    "current_session",
    "run_and_commit",
    "run_async",
]


class ConnectionWorker:
    """
    One OS thread and - once it has run its first statement - one pydal connection.

    The connection is never closed between jobs: the whole point of pinning the thread is that
    the next job on this worker finds the same thread local, hence the same connection, hence
    the same transaction.
    """

    def __init__(self, db: "TypeDAL", name: str) -> None:
        self._db = db
        self._executor = concurrent.futures.ThreadPoolExecutor(max_workers=1, thread_name_prefix=name)

    async def run[T](self, fn: t.Callable[[], T]) -> T:
        """Run `fn` on this worker's thread and await its result without blocking the loop."""
        return await asyncio.wrap_future(self._executor.submit(fn))

    def shutdown(self) -> None:
        """
        Close this worker's connection on its own thread, then stop the thread.

        The close runs on the worker because the connection is that thread's thread local, and its
        result is collected rather than dropped: this is the one path whose whole job is to release
        that connection, so a close that fails has to be heard rather than disappear. The thread is
        stopped either way - a connection that will not close is still not worth a live thread.
        """
        future = self._executor.submit(self._close_connection)
        try:
            future.result()
        finally:
            self._executor.shutdown(wait=True)

    def _close_connection(self) -> None:
        adapter = getattr(self._db, "_adapter", None)
        if adapter is None:  # pragma: no cover
            return

        # `really=False` lets pydal recycle the connection into its own pool when it has one;
        # it closes for real when it doesn't (sqlite) or when that pool is full.
        adapter.close(action="rollback", really=False)


class ConnectionWorkerPool:
    """
    A bounded set of `ConnectionWorker`s for one `TypeDAL`.

    Worker count is connection count, so the bound matters: it defaults to the database's
    `pool_size`. Handing a worker over uses a plain `concurrent.futures.Future` rather than an
    asyncio primitive, so one pool can serve several event loops (tests, in particular, get a
    fresh loop per test).
    """

    def __init__(self, db: "TypeDAL", max_workers: int) -> None:
        self._db = db
        self._max_workers = max(1, max_workers)
        self._lock = threading.Lock()
        self._idle: list[ConnectionWorker] = []
        self._waiters: deque[concurrent.futures.Future[ConnectionWorker]] = deque()
        self._created = 0

    def acquire(self) -> "concurrent.futures.Future[ConnectionWorker]":
        """Claim a worker, or queue for one if the pool is saturated."""
        future: concurrent.futures.Future[ConnectionWorker] = concurrent.futures.Future()
        with self._lock:
            if self._idle:
                worker = self._idle.pop()
            elif self._created < self._max_workers:
                self._created += 1
                worker = ConnectionWorker(self._db, f"typedal-async-{self._created}")
            else:
                self._waiters.append(future)
                return future

        future.set_result(worker)
        return future

    async def acquire_async(self) -> ConnectionWorker:
        """Await a worker, giving it back if this task is cancelled after it was handed over."""
        future = self.acquire()
        try:
            return await asyncio.wrap_future(future)
        except asyncio.CancelledError:
            if not future.cancel() and not future.cancelled():  # pragma: no cover - timing race
                # lost the race: the worker was handed over anyway, so hand it back.
                self.release(future.result())
            raise

    def release(self, worker: ConnectionWorker) -> None:
        """Give a worker back, straight to the longest-waiting claimant if there is one."""
        with self._lock:
            while self._waiters:
                waiter = self._waiters.popleft()
                if waiter.set_running_or_notify_cancel():
                    waiter.set_result(worker)
                    return

            self._idle.append(worker)

    def discard(self, worker: ConnectionWorker) -> None:
        """
        Drop a worker whose connection can no longer be trusted, freeing its slot in the pool.

        For the connection that can settle neither way: releasing it would hand an open, unsettleable
        transaction to the next borrower, and holding on to it would shrink the pool for good.
        """
        with self._lock:
            self._created -= 1

        worker.shutdown()

    def shutdown(self) -> None:
        """
        Close every idle worker's connection and stop its thread. Blocking; safe from sync code.

        Workers currently held by a session are left alone: their owner is still using them, and
        killing a thread mid-transaction is worse than a late shutdown.

        Every worker is shut down even when one of them fails to close, so a single bad connection
        cannot strand the rest; the failures are raised together once they all have been tried.
        """
        with self._lock:
            workers, self._idle = self._idle, []
            self._created -= len(workers)

        errors: list[Exception] = []
        for worker in workers:
            try:
                worker.shutdown()
            except Exception as e:  # pragma: no cover - needs a connection that refuses to close
                errors.append(e)

        if errors:  # pragma: no cover - same
            raise ExceptionGroup("closing async worker connections failed", errors)


class SessionBinding(t.NamedTuple):
    session: "AsyncSession"
    task: "asyncio.Task[t.Any] | None"


ACTIVE_SESSIONS: contextvars.ContextVar[dict[int, SessionBinding]] = contextvars.ContextVar("typedal_async_sessions")


def current_session(db: "TypeDAL") -> "AsyncSession | None":
    """
    The session `db` is currently in, for *this* task only.

    A binding inherited by a child task is not this task's session: `create_task`/`gather` copy
    the context, and joining the parent's transaction from a sibling task would mean two tasks
    interleaving statements on one connection.
    """
    binding = ACTIVE_SESSIONS.get({}).get(id(db))
    if binding is None or binding.task is not asyncio.current_task():
        return None

    return binding.session


class AsyncSession:
    """
    A transaction with a worker thread pinned to it.

    Entered with `async with db.session():`. The worker (and connection) is taken on the first
    statement, not at scope entry, and given back on commit/rollback. Exiting commits, or rolls
    back if the block raised.
    """

    def __init__(self, db: "TypeDAL") -> None:
        self._db = db
        self._worker: ConnectionWorker | None = None
        self._token: contextvars.Token[dict[int, SessionBinding]] | None = None
        self._joined = False

    async def __aenter__(self) -> "AsyncSession":
        """Bind this session to the current task, or join an outer one for the same database."""
        if outer := current_session(self._db):
            # nested `async with db.session()` in one task: one transaction, not two.
            self._joined = True
            return outer

        sessions = dict(ACTIVE_SESSIONS.get({}))
        sessions[id(self._db)] = SessionBinding(self, asyncio.current_task())
        self._token = ACTIVE_SESSIONS.set(sessions)
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        """Settle the transaction (commit, or rollback when the block raised) and unbind."""
        if self._joined:
            return

        try:
            if exc_type is None:
                await self._commit_or_rollback()
            else:
                await self.rollback()
        finally:
            if self._token is not None:
                ACTIVE_SESSIONS.reset(self._token)
                self._token = None

    async def run_sync[**P, T](self, fn: t.Callable[P, T], *args: P.args, **kwargs: P.kwargs) -> T:
        """
        Run ordinary *sync* TypeDAL code on this session's worker.

        Everything inside `fn` - relationships, caching, hooks, CASCADE - runs on one connection
        inside this transaction, which is what makes the sync parts of the ORM usable from async
        code without reimplementing them.
        """
        worker = await self._acquire()
        return await worker.run(functools.partial(fn, *args, **kwargs))

    async def commit(self) -> None:
        """Commit this transaction and release the worker. A no-op if nothing ran yet."""
        await self._settle(self._db.commit)

    async def rollback(self) -> None:
        """Roll back this transaction and release the worker. A no-op if nothing ran yet."""
        await self._settle(self._db.rollback)

    async def _acquire(self) -> ConnectionWorker:
        if self._worker is None:
            self._worker = await self._db._async_workers.acquire_async()

        return self._worker

    async def _commit_or_rollback(self) -> None:
        """
        Commit, falling back to a rollback so the scope never ends with the transaction open.

        A commit can fail on its own - SQLITE_BUSY, a deferred constraint, a connection that died
        between the last statement and this one - and the transaction is then still there. The
        caller gets the commit error either way; what changes is the state it leaves behind.
        """
        try:
            await self.commit()
        except BaseException:
            try:
                await self.rollback()
            except Exception:
                # neither commit nor rollback got through: this connection is not fit to be reused.
                await self._discard_worker()
            raise

    async def _settle(self, action: t.Callable[[], None]) -> None:
        worker = self._worker
        if worker is None:
            return

        # the worker is released only once the transaction is actually settled: giving it back
        # while the transaction is still open would hand it to the next borrower mid-write, and
        # clearing `self._worker` first would leave this session unable to try again.
        await worker.run(action)

        self._worker = None
        self._db._async_workers.release(worker)

    async def _discard_worker(self) -> None:
        worker, self._worker = self._worker, None
        if worker is None:  # pragma: no cover - only reached from a failed settle, which held one
            return

        loop = asyncio.get_running_loop()
        # shutting a worker down blocks on its thread, and closing a connection this broken may
        # well fail too - neither belongs on the event loop or on top of the error being raised.
        with contextlib.suppress(Exception):
            await loop.run_in_executor(None, self._db._async_workers.discard, worker)


async def run_async[**P, T](db: "TypeDAL", fn: t.Callable[P, T], *args: P.args, **kwargs: P.kwargs) -> T:
    """
    Run a sync callable on a worker thread: the one primitive every `*_async` method is built on.

    Inside a session it joins that session's worker and transaction. Outside one it borrows a
    worker and commits before returning - releasing a worker with an uncommitted write on it
    would silently lose that write, since the next borrower may be a different task entirely.
    """
    call = functools.partial(fn, *args, **kwargs)

    if session := current_session(db):
        return await session.run_sync(call)

    pool = db._async_workers
    worker = await pool.acquire_async()
    try:
        return await worker.run(functools.partial(run_and_commit, db, call))
    finally:
        pool.release(worker)


def run_and_commit[T](db: "TypeDAL", call: t.Callable[[], T]) -> T:
    """
    Run `call` on the worker's thread and settle its transaction: commit, or rollback on error.

    The commit is guarded too, not just the call: it can fail on its own and leave the transaction
    open behind it. `run_async` gives the worker back regardless, so anything still open at that
    point becomes the next borrower's problem - a different task, in a different transaction,
    reading and then committing writes that were never theirs.
    """
    try:
        result = call()
    except BaseException:
        db.rollback()
        raise

    try:
        db.commit()
    except BaseException:
        db.rollback()
        raise

    return result
