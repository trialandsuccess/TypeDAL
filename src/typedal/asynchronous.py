"""
Async support for TypeDAL via thread offload.

All statements are pydal's unmodified sync code, run on a worker thread so the event loop
stays free. pydal keeps its connection in a thread local, so each pinned thread owns one
connection and one transaction.

Two entry points:

- flat `*_async` calls, which borrow a worker and **commit before returning**;
- `async with db.session():`, which pins one worker (and connection, and transaction) for its
  scope and commits on clean exit, or rolls back if the block raised.

Sessions live in a `ContextVar`; each binding is stamped with the task that created it, so a
task spawned inside a session doesn't silently join the parent's transaction.
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
    One OS thread and, once it has run its first statement, one pydal connection.

    The connection is never closed between jobs, so the next job on this worker finds the same
    connection and transaction.
    """

    def __init__(self, db: "TypeDAL", name: str) -> None:
        self._db = db
        self._executor = concurrent.futures.ThreadPoolExecutor(max_workers=1, thread_name_prefix=name)

    def submit[T](self, fn: t.Callable[[], T]) -> "concurrent.futures.Future[T]":
        """
        Queue `fn` on this worker's thread without waiting for it.

        Unlike `run`, the returned future survives cancellation of the submitter (see
        `AsyncSession._abandon_worker`).
        """
        return self._executor.submit(fn)

    async def run[T](self, fn: t.Callable[[], T]) -> T:
        """Run `fn` on this worker's thread and await its result without blocking the loop."""
        return await asyncio.wrap_future(self.submit(fn))

    def shutdown(self) -> None:
        """
        Close this worker's connection on its own thread, then stop the thread.

        The close's result is collected so a failure is surfaced; the thread is stopped either way.
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

    Worker count is connection count, so the bound defaults to `max(4, pool_size)`.
    Hand-over uses a plain `concurrent.futures.Future` rather than an asyncio primitive, so one
    pool can serve several event loops (tests get a fresh loop per test).
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

    def _next_waiter(self) -> "concurrent.futures.Future[ConnectionWorker] | None":
        """The longest-waiting claimant that hasn't cancelled, if any. Caller holds `self._lock`."""
        while self._waiters:
            waiter = self._waiters.popleft()
            if waiter.set_running_or_notify_cancel():
                return waiter

        return None

    def release(self, worker: ConnectionWorker) -> None:
        """Give a worker back, straight to the longest-waiting claimant if there is one."""
        with self._lock:
            if waiter := self._next_waiter():
                waiter.set_result(worker)
                return

            self._idle.append(worker)

    def discard(self, worker: ConnectionWorker) -> None:
        """
        Drop a worker whose connection can no longer be trusted, freeing its slot in the pool.

        A replacement worker is built on the spot for the next claimant, which would otherwise
        never see the freed slot.
        """
        with self._lock:
            self._created -= 1
            if waiter := self._next_waiter():
                self._created += 1
                waiter.set_result(ConnectionWorker(self._db, f"typedal-async-{self._created}"))

        worker.shutdown()

    def shutdown(self) -> None:
        """
        Close every idle worker's connection and stop its thread. Blocking; safe from sync code.

        Held workers are left to their owners; queued waiters are failed. All idle workers are
        tried even if some fail, and the failures are raised together.
        """
        with self._lock:
            workers, self._idle = self._idle, []
            self._created -= len(workers)
            waiters, self._waiters = self._waiters, deque()

        while waiters:
            waiter = waiters.popleft()
            if waiter.set_running_or_notify_cancel():
                waiter.set_exception(RuntimeError("The async worker pool is shutting down."))

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

    Bindings inherited from a parent task are ignored, so sibling tasks can't interleave
    statements on one connection.
    """
    binding = ACTIVE_SESSIONS.get({}).get(id(db))
    if binding is None or binding.task is not asyncio.current_task():
        return None

    return binding.session


class AsyncSession:
    """
    A transaction with a worker thread pinned to it. Entered with `async with db.session():`.

    The worker (and connection) is taken on the first statement and given back on
    commit/rollback. Exiting commits, or rolls back if the block raised.
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
            # the settle above is made of ordinary awaits, and a cancellation delivered into one of
            # them unwinds this scope with the worker still held and the transaction still open.
            # a no-op when the settle got through - by then there is no worker left to hold.
            self._abandon_worker()

            if self._token is not None:
                ACTIVE_SESSIONS.reset(self._token)
                self._token = None

    async def run_sync[**P, T](self, fn: t.Callable[P, T], *args: P.args, **kwargs: P.kwargs) -> T:
        """
        Run ordinary *sync* TypeDAL code on this session's worker, sharing its connection and
        transaction.
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
        """Commit, falling back to a rollback so the scope never ends with the transaction open."""
        try:
            await self.commit()
        except BaseException:
            try:
                await self.rollback()
            except Exception:
                # neither commit nor rollback got through: this connection is not fit to be reused.
                self._discard_worker()
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

    def _abandon_worker(self) -> None:
        """
        Give back a worker whose settle never finished, without awaiting anything.

        Last resort when cancellation unwinds `__aexit__` mid-settle: the rollback is *submitted*
        to the worker, which returns itself to the pool from its own thread once that is through.
        """
        worker, self._worker = self._worker, None
        if worker is None:
            return

        pool = self._db._async_workers

        def rollback_and_release() -> None:
            try:
                self._db.rollback()
            except Exception:
                # a connection that will not roll back is not fit for the next borrower either.
                self._discard_elsewhere(worker)
            else:
                pool.release(worker)

        with contextlib.suppress(RuntimeError):  # the worker may already have been shut down
            worker.submit(rollback_and_release)

    def _discard_worker(self) -> None:
        worker, self._worker = self._worker, None
        if worker is None:  # pragma: no cover - only reached from a failed settle, which held one
            return

        self._discard_elsewhere(worker)

    def _discard_elsewhere(self, worker: ConnectionWorker) -> None:
        """
        Discard `worker` from a dedicated thread, since `discard` blocks on the worker's own thread.

        Runs without waiting; failures are swallowed, as callers are already carrying an error.
        """
        threading.Thread(
            target=self._discard_quietly,
            args=(worker,),
            name="typedal-async-discard",
            daemon=True,
        ).start()

    def _discard_quietly(self, worker: ConnectionWorker) -> None:
        with contextlib.suppress(Exception):
            self._db._async_workers.discard(worker)


async def run_async[**P, T](db: "TypeDAL", fn: t.Callable[P, T], *args: P.args, **kwargs: P.kwargs) -> T:
    """
    Run a sync callable on a worker thread: the primitive every `*_async` method is built on.

    Inside a session it joins that session's worker and transaction. Outside one it borrows a
    worker and commits before returning, so the write isn't silently lost to the next borrower.
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

    The commit is guarded too: a failed commit leaving the transaction open would poison the
    next borrower of this worker.
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
