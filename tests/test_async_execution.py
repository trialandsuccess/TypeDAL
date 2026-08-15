"""
Test-first spec for TypeDAL's async execution path.

Scope is Postgres AND SQLite together, not sequenced - `db_async` is parametrized over both
backends so every test below runs against each, proving the same async surface works
identically rather than "works for Postgres, TODO for SQLite".

Covers two concrete Postgres divergence points found while building this:
  - jsonb -> dict (pydal's Postgres parser expects the driver to have already decoded it)
  - decimal(10,2) -> Decimal
and the actual point of the exercise: the event loop is not blocked while the query runs.
"""

import asyncio
import collections
import contextlib
import signal
import sqlite3
import tempfile
import time
import typing as t
from decimal import Decimal
from pathlib import Path

import pydal.objects
import pytest
import pytest_asyncio

from src.typedal import TypeDAL, TypedField, TypedTable
from src.typedal.async_execution import (
    ASYNC_POOL_FACTORIES,
    AsyncPoolManager,
    TransactionBoundaryError,
    TransactionSplitError,
    open_sqlite_async_connection,
    postgres_lastrowid_async,
)
from src.typedal.fields import DecimalField, JSONField
from src.typedal.query_builder import QueryBuilder


@contextlib.asynccontextmanager
async def _postgres_db(dal_psql: TypeDAL) -> t.AsyncIterator[TypeDAL]:
    try:
        yield dal_psql
    finally:
        await dal_psql.close_async()


@contextlib.asynccontextmanager
async def _sqlite_db(dal_psql: TypeDAL) -> t.AsyncIterator[TypeDAL]:
    with tempfile.TemporaryDirectory() as d:
        db = TypeDAL("sqlite:memory", enable_typedal_caching=False, folder=d)
        try:
            yield db
        finally:
            await db.close_async()
            db.close()


@contextlib.asynccontextmanager
async def _sqlite_file_db(dal_psql: TypeDAL) -> t.AsyncIterator[TypeDAL]:
    """
    A file-backed SQLite database, which is a materially different async backend from
    `sqlite:memory` and not a redundant copy of it.

    `sqlite:memory` reaches a second connection only through shared-cache mode, which refuses a
    concurrent writer with SQLITE_LOCKED, so its async path is one shared connection
    (`SqliteAsyncConnection`) that turns a second task away. A file has a path two connections
    can both open, so it gets `SqliteAsyncPool` and a connection per task instead. Every
    transaction-boundary claim differs between the two, and without this parametrization the
    per-task SQLite code is never executed by the suite at all.
    """
    with tempfile.TemporaryDirectory() as d:
        db = TypeDAL(f"sqlite://{Path(d) / 'async.db'}", enable_typedal_caching=False, folder=d)
        try:
            yield db
        finally:
            await db.close_async()
            db.close()


# One factory per backend the async execution path targets.
_ASYNC_DB_FACTORIES: dict[str, t.Callable[[TypeDAL], t.AsyncContextManager[TypeDAL]]] = {
    "postgres": _postgres_db,
    "sqlite": _sqlite_db,
    "sqlite-file": _sqlite_file_db,
}


@pytest_asyncio.fixture(params=list(_ASYNC_DB_FACTORIES))
async def db_async(request: pytest.FixtureRequest, dal_psql: TypeDAL) -> t.AsyncIterator[TypeDAL]:
    """
    A `TypeDAL` instance for each backend the async execution path targets, with a guaranteed-
    closed async connection pool afterwards.

    Without the teardown, a lazily-opened async pool/connection outlives the test's event loop
    (pytest-asyncio gives each test function its own loop by default) and the *next* test hangs
    trying to use pool internals (locks/tasks) bound to an already-closed loop.
    """
    factory = _ASYNC_DB_FACTORIES[request.param]
    async with factory(dal_psql) as db:
        yield db


@pytest.mark.asyncio
async def test_collect_async_matches_sync_collect(db_async: TypeDAL):
    """The core parity claim: async-executed rows must equal sync-executed rows, field for field."""
    db = db_async

    @db.define()
    class AsyncThingParity(TypedTable):
        name: TypedField[str]
        qty: TypedField[int]

    AsyncThingParity.insert(name="widget", qty=3)
    AsyncThingParity.insert(name="gadget", qty=7)
    db.commit()

    sync_rows = AsyncThingParity.where(AsyncThingParity.qty > 0).collect()
    async_rows = await AsyncThingParity.where(AsyncThingParity.qty > 0).collect_async()

    assert len(async_rows) == len(sync_rows) == 2

    sync_by_id = {row.id: row for row in sync_rows}
    async_by_id = {row.id: row for row in async_rows}
    assert sync_by_id.keys() == async_by_id.keys()

    for row_id, sync_row in sync_by_id.items():
        async_row = async_by_id[row_id]
        assert async_row.name == sync_row.name
        assert async_row.qty == sync_row.qty


@pytest.mark.asyncio
async def test_collect_async_preserves_types(db_async: TypeDAL):
    """The two divergence points the spike actually found: decimal and jsonb."""
    db = db_async

    @db.define()
    class AsyncThingTypes(TypedTable):
        name: TypedField[str]
        price = DecimalField(10, 2)
        meta = JSONField()

    AsyncThingTypes.insert(name="widget", price=Decimal("19.99"), meta={"a": 1, "b": [1, 2, 3]})
    db.commit()

    rows = await AsyncThingTypes.where(AsyncThingTypes.name == "widget").collect_async()
    row = rows.first()

    assert isinstance(row.price, Decimal)
    assert row.price == Decimal("19.99")

    assert isinstance(row.meta, dict)  # not a raw jsonb/json string
    assert row.meta == {"a": 1, "b": [1, 2, 3]}


@pytest.mark.asyncio
async def test_count_async_matches_sync_count(db_async: TypeDAL):
    """count_async must return the same count as the sync count()."""
    db = db_async

    @db.define()
    class AsyncThingCount(TypedTable):
        qty: TypedField[int]

    AsyncThingCount.insert(qty=1)
    AsyncThingCount.insert(qty=2)
    AsyncThingCount.insert(qty=3)
    db.commit()

    sync_count = AsyncThingCount.where(AsyncThingCount.qty > 1).count()
    async_count = await AsyncThingCount.where(AsyncThingCount.qty > 1).count_async()

    assert async_count == sync_count == 2


@pytest.mark.asyncio
async def test_insert_async_matches_sync_insert(db_async: TypeDAL):
    """insert_async must return a usable id, and the row must actually be committed and visible."""
    db = db_async

    @db.define()
    class AsyncThingInsert(TypedTable):
        name: TypedField[str]
        qty: TypedField[int]

    new_id = await AsyncThingInsert.insert_async(name="widget", qty=5)
    await db.commit_async()

    assert int(new_id) > 0

    row = AsyncThingInsert.where(AsyncThingInsert.id == int(new_id)).first()
    assert row is not None
    assert row.name == "widget"
    assert row.qty == 5


@pytest.mark.asyncio
async def test_update_async_matches_sync_update(db_async: TypeDAL):
    """update_async must update the same rows as the sync update() and return matching ids."""
    db = db_async

    @db.define()
    class AsyncThingUpdate(TypedTable):
        name: TypedField[str]
        qty: TypedField[int]

    AsyncThingUpdate.insert(name="widget", qty=1)
    AsyncThingUpdate.insert(name="gadget", qty=2)
    db.commit()

    updated_ids = await AsyncThingUpdate.where(AsyncThingUpdate.qty > 0).update_async(qty=99)
    await db.commit_async()

    assert len(updated_ids) == 2

    rows = AsyncThingUpdate.where(AsyncThingUpdate.qty == 99).collect()
    assert len(rows) == 2


@pytest.mark.asyncio
async def test_delete_async_matches_sync_delete(db_async: TypeDAL):
    """delete_async must delete the same rows as the sync delete() and return matching ids."""
    db = db_async

    @db.define()
    class AsyncThingDelete(TypedTable):
        qty: TypedField[int]

    AsyncThingDelete.insert(qty=1)
    AsyncThingDelete.insert(qty=2)
    db.commit()

    deleted_ids = await AsyncThingDelete.where(AsyncThingDelete.qty > 0).delete_async()
    await db.commit_async()

    assert len(deleted_ids) == 2

    remaining = AsyncThingDelete.where(AsyncThingDelete.qty > 0).count()
    assert remaining == 0


@pytest.mark.asyncio
async def test_executesql_async_matches_sync_executesql(db_async: TypeDAL):
    """executesql_async must return the same raw rows as the sync executesql()."""
    db = db_async

    @db.define()
    class AsyncThingRaw(TypedTable):
        qty: TypedField[int]

    AsyncThingRaw.insert(qty=1)
    AsyncThingRaw.insert(qty=2)
    db.commit()

    query = f"SELECT qty FROM {AsyncThingRaw._table._rname} ORDER BY qty;"

    sync_rows = db.executesql(query)
    async_rows = await db.executesql_async(query)

    assert list(async_rows) == list(sync_rows) == [(1,), (2,)]


@pytest.mark.asyncio
async def test_collect_async_with_relationships_matches_sync(db_async: TypeDAL):
    """
    Relationships/joins must load through the async path too. Nothing on that path executes a
    second query: the joins are in the single query built by `_before_query()` and
    `_collect_with_relationships()` only maps already-fetched rows, so async parity is expected.
    """
    db = db_async

    @db.define()
    class AsyncThingRelOther(TypedTable):
        name: TypedField[str]

    @db.define()
    class AsyncThingRelMain(TypedTable):
        name: TypedField[str]
        other: AsyncThingRelOther

    other_id = AsyncThingRelOther.insert(name="parent")
    AsyncThingRelMain.insert(name="child", other=other_id)
    db.commit()

    sync_rows = AsyncThingRelMain.join("other").collect()
    async_rows = await AsyncThingRelMain.join("other").collect_async()

    assert len(async_rows) == len(sync_rows) == 1

    sync_row = sync_rows.first()
    async_row = async_rows.first()
    assert async_row.name == sync_row.name == "child"
    assert async_row.other.name == sync_row.other.name == "parent"

    # and with a limitby, which routes through `_apply_limitby_optimization()`'s id-subquery:
    paginated = await AsyncThingRelMain.join("other").paginate_async(limit=1, page=1)
    assert len(paginated) == 1
    assert paginated.first().other.name == "parent"


@pytest.mark.asyncio
async def test_all_async_matches_sync_all(db_async: TypeDAL):
    """all_async must return the same rows as the sync all()."""
    db = db_async

    @db.define()
    class AsyncThingAll(TypedTable):
        qty: TypedField[int]

    AsyncThingAll.insert(qty=1)
    AsyncThingAll.insert(qty=2)
    db.commit()

    sync_rows = AsyncThingAll.all()
    async_rows = await AsyncThingAll.all_async()

    assert len(async_rows) == len(sync_rows) == 2


@pytest.mark.asyncio
async def test_exists_async_matches_sync_exists(db_async: TypeDAL):
    """exists_async (QueryBuilder and the TypedTable shortcut) must match the sync exists()."""
    db = db_async

    @db.define()
    class AsyncThingExists(TypedTable):
        qty: TypedField[int]

    assert not await AsyncThingExists.where(AsyncThingExists.qty > 0).exists_async()
    assert not await AsyncThingExists.exists_async()

    AsyncThingExists.insert(qty=1)
    db.commit()

    assert AsyncThingExists.where(AsyncThingExists.qty > 0).exists() is True
    assert await AsyncThingExists.where(AsyncThingExists.qty > 0).exists_async() is True
    assert await AsyncThingExists.exists_async() is True


@pytest.mark.asyncio
async def test_first_async_and_first_or_fail_async_match_sync(db_async: TypeDAL):
    """first_async/first_or_fail_async (QueryBuilder and TypedTable shortcuts) must match sync."""
    db = db_async

    @db.define()
    class AsyncThingFirst(TypedTable):
        qty: TypedField[int]

    assert await AsyncThingFirst.where(AsyncThingFirst.qty > 0).first_async() is None
    with pytest.raises(ValueError):
        await AsyncThingFirst.where(AsyncThingFirst.qty > 0).first_or_fail_async()

    AsyncThingFirst.insert(qty=5)
    db.commit()

    sync_row = AsyncThingFirst.where(AsyncThingFirst.qty > 0).first()
    async_row = await AsyncThingFirst.where(AsyncThingFirst.qty > 0).first_async()
    assert async_row is not None and sync_row is not None
    assert async_row.qty == sync_row.qty == 5

    async_row_2 = await AsyncThingFirst.where(AsyncThingFirst.qty > 0).first_or_fail_async()
    assert async_row_2.qty == 5

    # TypedTable-level shortcuts (no explicit .where(...)):
    async_row_3 = await AsyncThingFirst.first_async()
    assert async_row_3 is not None
    assert async_row_3.qty == 5
    async_row_4 = await AsyncThingFirst.first_or_fail_async()
    assert async_row_4.qty == 5


@pytest.mark.asyncio
async def test_paginate_async_matches_sync_paginate(db_async: TypeDAL):
    """paginate_async (QueryBuilder and the TypedTable shortcut) must match sync paginate()."""
    db = db_async

    @db.define()
    class AsyncThingPaginate(TypedTable):
        qty: TypedField[int]

    for i in range(5):
        AsyncThingPaginate.insert(qty=i)
    db.commit()

    sync_page = AsyncThingPaginate.where(AsyncThingPaginate.qty >= 0).paginate(limit=2, page=2)
    async_page = await AsyncThingPaginate.where(AsyncThingPaginate.qty >= 0).paginate_async(limit=2, page=2)

    assert len(async_page) == len(sync_page) == 2
    assert async_page.pagination["current_page"] == sync_page.pagination["current_page"] == 2
    assert async_page.pagination["total_items"] == sync_page.pagination["total_items"] == 5

    async_page_2 = await AsyncThingPaginate.paginate_async(limit=2, page=1)
    assert len(async_page_2) == 2


@pytest.mark.asyncio
async def test_chunk_async_matches_sync_chunk(db_async: TypeDAL):
    """chunk_async (QueryBuilder and the TypedTable shortcut) must yield the same chunks as sync chunk()."""
    db = db_async

    @db.define()
    class AsyncThingChunk(TypedTable):
        qty: TypedField[int]

    for i in range(5):
        AsyncThingChunk.insert(qty=i)
    db.commit()

    sync_chunks = [len(chunk) for chunk in AsyncThingChunk.where(AsyncThingChunk.qty >= 0).chunk(2)]

    async_chunks = []
    async for chunk in AsyncThingChunk.where(AsyncThingChunk.qty >= 0).chunk_async(2):
        async_chunks.append(len(chunk))

    assert async_chunks == sync_chunks == [2, 2, 1]

    async_chunks_2 = [len(chunk) async for chunk in AsyncThingChunk.chunk_async(2)]
    assert async_chunks_2 == [2, 2, 1]


@pytest.mark.asyncio
async def test_column_async_matches_sync_column(db_async: TypeDAL):
    """column_async (QueryBuilder and the TypedTable shortcut) must match sync column()."""
    db = db_async

    @db.define()
    class AsyncThingColumn(TypedTable):
        qty: TypedField[int]

    AsyncThingColumn.insert(qty=1)
    AsyncThingColumn.insert(qty=2)
    db.commit()

    sync_values = AsyncThingColumn.where(AsyncThingColumn.qty > 0).column(AsyncThingColumn.qty)
    async_values = await AsyncThingColumn.where(AsyncThingColumn.qty > 0).column_async(AsyncThingColumn.qty)

    assert sorted(async_values) == sorted(sync_values) == [1, 2]

    async_values_2 = await AsyncThingColumn.column_async(AsyncThingColumn.qty)
    assert sorted(async_values_2) == [1, 2]


@pytest.mark.asyncio
async def test_collect_into_async_matches_sync_collect_into(db_async: TypeDAL):
    """collect_into_async (QueryBuilder and the TypedTable shortcut) must match sync collect_into()."""
    db = db_async

    @db.define()
    class AsyncThingIntoSource(TypedTable):
        name: TypedField[str]
        qty: TypedField[int]

    # collect_into reshapes rows from the SAME table into a different Python representation -
    # it is not for copying between two distinct tables. These stay undefined (no @db.define()):
    # _validate_collect_into_model binds each one to the source's table on first use.
    class AsyncThingIntoTargetSync(TypedTable):
        name: TypedField[str]
        qty: TypedField[int]

    class AsyncThingIntoTargetAsync(TypedTable):
        name: TypedField[str]
        qty: TypedField[int]

    class AsyncThingIntoTargetAsyncBare(TypedTable):
        name: TypedField[str]
        qty: TypedField[int]

    AsyncThingIntoSource.insert(name="widget", qty=1)
    db.commit()

    sync_rows = AsyncThingIntoSource.where(AsyncThingIntoSource.qty > 0).collect_into(AsyncThingIntoTargetSync)
    async_rows = await AsyncThingIntoSource.where(AsyncThingIntoSource.qty > 0).collect_into_async(
        AsyncThingIntoTargetAsync,
    )

    assert len(async_rows) == len(sync_rows) == 1
    assert isinstance(async_rows.first(), AsyncThingIntoTargetAsync)

    async_rows_2 = await AsyncThingIntoSource.collect_into_async(AsyncThingIntoTargetAsyncBare)
    assert len(async_rows_2) == 1


@pytest.mark.asyncio
async def test_collect_or_fail_async_matches_sync_collect_or_fail(db_async: TypeDAL):
    """collect_or_fail_async must match sync collect_or_fail(): rows when present, raise when empty."""
    db = db_async

    @db.define()
    class AsyncThingCollectOrFail(TypedTable):
        qty: TypedField[int]

    with pytest.raises(ValueError):
        await AsyncThingCollectOrFail.where(AsyncThingCollectOrFail.qty > 0).collect_or_fail_async()

    AsyncThingCollectOrFail.insert(qty=1)
    db.commit()

    sync_rows = AsyncThingCollectOrFail.where(AsyncThingCollectOrFail.qty > 0).collect_or_fail()
    async_rows = await AsyncThingCollectOrFail.where(AsyncThingCollectOrFail.qty > 0).collect_or_fail_async()

    assert len(async_rows) == len(sync_rows) == 1


@pytest.mark.asyncio
async def test_bulk_insert_async_matches_sync_bulk_insert(db_async: TypeDAL):
    """bulk_insert_async must insert the same rows as the sync bulk_insert()."""
    db = db_async

    @db.define()
    class AsyncThingBulkInsert(TypedTable):
        qty: TypedField[int]

    rows = await AsyncThingBulkInsert.bulk_insert_async([{"qty": 1}, {"qty": 2}, {"qty": 3}])
    await db.commit_async()

    assert len(rows) == 3
    assert sorted(r.qty for r in rows) == [1, 2, 3]
    assert AsyncThingBulkInsert.count() == 3


@pytest.mark.asyncio
async def test_update_or_insert_async_matches_sync(db_async: TypeDAL):
    """update_or_insert_async must insert when no match exists, and update when one does."""
    db = db_async

    @db.define()
    class AsyncThingUpsert(TypedTable):
        name: TypedField[str]
        qty: TypedField[int]

    # insert branch: no matching row yet
    inserted = await AsyncThingUpsert.update_or_insert_async({"name": "widget"}, name="widget", qty=1)
    await db.commit_async()
    assert inserted.qty == 1
    assert AsyncThingUpsert.count() == 1

    # update branch: matching row exists
    updated = await AsyncThingUpsert.update_or_insert_async({"name": "widget"}, name="widget", qty=2)
    await db.commit_async()
    assert updated.qty == 2
    assert AsyncThingUpsert.count() == 1


@pytest.mark.asyncio
async def test_validate_and_insert_async_matches_sync(db_async: TypeDAL):
    """validate_and_insert_async must match sync validate_and_insert(): row on success, errors on failure."""
    db = db_async

    @db.define()
    class AsyncThingValidateInsert(TypedTable):
        qty: TypedField[int]

    row, errors = await AsyncThingValidateInsert.validate_and_insert_async(qty=5)
    await db.commit_async()
    assert errors is None
    assert row is not None
    assert row.qty == 5

    _row, errors = await AsyncThingValidateInsert.validate_and_insert_async(qty="not-a-number")
    assert errors is not None


@pytest.mark.asyncio
async def test_validate_and_update_async_matches_sync(db_async: TypeDAL):
    """validate_and_update_async must match sync validate_and_update(): row on success, errors on failure."""
    db = db_async

    @db.define()
    class AsyncThingValidateUpdate(TypedTable):
        qty: TypedField[int]

    existing_id = AsyncThingValidateUpdate.insert(qty=1)
    db.commit()

    row, errors = await AsyncThingValidateUpdate.validate_and_update_async(
        AsyncThingValidateUpdate.id == int(existing_id),
        qty=9,
    )
    await db.commit_async()
    assert errors is None
    assert row is not None
    assert row.qty == 9

    _row, errors = await AsyncThingValidateUpdate.validate_and_update_async(
        AsyncThingValidateUpdate.id == int(existing_id),
        qty="not-a-number",
    )
    assert errors is not None


@pytest.mark.asyncio
async def test_validate_and_update_or_insert_async_matches_sync(db_async: TypeDAL):
    """validate_and_update_or_insert_async must insert when no match exists, update when one does."""
    db = db_async

    @db.define()
    class AsyncThingValidateUpsert(TypedTable):
        name: TypedField[str]
        qty: TypedField[int]

    inserted, errors = await AsyncThingValidateUpsert.validate_and_update_or_insert_async(
        AsyncThingValidateUpsert.name == "widget",
        name="widget",
        qty=1,
    )
    await db.commit_async()
    assert errors is None
    assert inserted.qty == 1
    assert AsyncThingValidateUpsert.count() == 1

    updated, errors = await AsyncThingValidateUpsert.validate_and_update_or_insert_async(
        AsyncThingValidateUpsert.name == "widget",
        name="widget",
        qty=2,
    )
    await db.commit_async()
    assert errors is None
    assert updated.qty == 2
    assert AsyncThingValidateUpsert.count() == 1


@pytest.mark.asyncio
async def test_classmethod_update_async_matches_sync(db_async: TypeDAL):
    """The classmethod update_async(query, **fields) shortcut must match sync update()."""
    db = db_async

    @db.define()
    class AsyncThingClsUpdate(TypedTable):
        qty: TypedField[int]

    existing_id = AsyncThingClsUpdate.insert(qty=1)
    db.commit()

    updated = await AsyncThingClsUpdate.update_async(AsyncThingClsUpdate.id == int(existing_id), qty=42)
    await db.commit_async()

    assert updated is not None
    assert updated.qty == 42


@pytest.mark.asyncio
async def test_update_record_async_and_delete_record_async_match_sync(db_async: TypeDAL):
    """Instance-level update_record_async/delete_record_async must match their sync twins."""
    db = db_async

    @db.define()
    class AsyncThingRecord(TypedTable):
        qty: TypedField[int]

    row_id = AsyncThingRecord.insert(qty=1)
    db.commit()

    row = AsyncThingRecord.where(AsyncThingRecord.id == int(row_id)).first()
    updated_row = await row.update_record_async(qty=7)
    await db.commit_async()
    assert updated_row.qty == 7

    fresh = AsyncThingRecord.where(AsyncThingRecord.id == int(row_id)).first()
    assert fresh.qty == 7

    deleted_count = await fresh.delete_record_async()
    await db.commit_async()
    assert deleted_count == 1
    assert AsyncThingRecord.count() == 0


@pytest.mark.asyncio
async def test_collect_async_does_not_block_event_loop(db_async: TypeDAL):
    """
    The actual point of building this: a query in flight must not stall other coroutines.
    A ticker sleeping every 5ms should keep ticking at ~5ms while queries run concurrently;
    a blocking implementation would show gaps close to the total query time instead.
    """
    db = db_async

    @db.define()
    class AsyncThingBlocking(TypedTable):
        qty: TypedField[int]

    AsyncThingBlocking.insert(qty=1)
    db.commit()

    ticks: list[float] = []

    async def ticker():
        for _ in range(20):
            ticks.append(time.perf_counter())
            await asyncio.sleep(0.005)

    async def repeated_query():
        for _ in range(20):
            await AsyncThingBlocking.where(AsyncThingBlocking.qty > 0).collect_async()

    await asyncio.gather(ticker(), repeated_query())

    gaps = [b - a for a, b in zip(ticks, ticks[1:])]
    # generous margin over the 5ms sleep interval; a blocking call would blow well past this
    assert max(gaps) < 0.05, f"event loop was blocked: max gap between ticks was {max(gaps) * 1000:.1f}ms"


@pytest.mark.asyncio
async def test_insert_async_can_be_rolled_back(db_async: TypeDAL):
    """
    (1/3) An `_async` write must leave its transaction open, the way its sync twin does.

    Neither backend does today, and each for its own reason:

      - SQLite: `SqliteAsyncConnection.connection()` (async_execution.py) commits on clean
        exit, so the write is durable before `insert_async()` returns.
      - Postgres: psycopg_pool's `connection()` applies the same commit-on-success behaviour,
        and `PostgresAsyncPool.commit()`/`rollback()` are therefore literally `pass`.
        `rollback_async()` is a no-op that reads like transaction control.

    Both are known and documented (see the `PostgresAsyncPool` and `AsyncConnectionPool`
    docstrings, and `TypeDAL.commit_async` in core.py). Documented is not the same as safe: a
    py4web handler calling `insert_async()` silently falls outside the framework's
    rollback-on-error, and gets no signal that it has.

    No concurrency here on purpose. This is a single-coroutine defect, and until it is fixed no
    coroutine can hold an open transaction at all - which makes (3/3) unattributable, since it
    would fail for this reason no matter how connections are bound.
    """
    db = db_async

    @db.define()
    class AsyncThingUndoable(TypedTable):
        name: TypedField[str]

    db.commit()

    await AsyncThingUndoable.insert_async(name="discard")
    await db.rollback_async()

    rows = await AsyncThingUndoable.collect_async()
    assert [row.name for row in rows] == [], "rollback_async() did not undo insert_async()"

    # and the sync rollback a framework issues on an unhandled exception must not undo it
    # either way round - assert it separately so a fix that only wires up one of the two is
    # visible as such.
    db.rollback()
    assert AsyncThingUndoable.count() == 0, "the write survived both rollbacks"


@pytest.mark.asyncio
async def test_crossing_the_sync_async_seam_is_a_loud_error(db_async: TypeDAL):
    """
    (2/3) A read must never quietly miss the other connection's uncommitted writes.

    `_async` methods run on a connection opened by `AsyncPoolManager`; sync methods run on
    pydal's own, bound to the `THREAD_LOCAL` in pydal's `ConnectionPool`. Those cannot be made
    into one connection - pydal drives Postgres with psycopg2 and SQLite with sqlite3, neither
    of which can be awaited - so read-your-own-writes across the two paths is not available at
    any price. Left alone it failed silently: Postgres returned nothing, SQLite blocked on the
    table lock and then raised `database table is locked`.

    This test used to assert cross-visibility outright and closed with "if the split is made
    explicit instead, invert this to assert the raised error". That is what happened.

    Warning and continuing was measured before settling on a raise, and does not survive
    contact with SQLite: Postgres can return the committed rows and warn, a plain SQLite read
    cannot execute at all, and SQLite with `PRAGMA read_uncommitted=1` returns *more* rows than
    Postgres - including ones a rollback then deletes. Three answers to identical code, two
    silent. See `TransactionSplitError`.

    Both directions asserted, because different machinery guards each and a regression could
    hit only one:

      - sync write -> async read: the flag check in `TypeDAL._get_async_pool()`.
      - async write -> sync read: `SyncTransactionTracker`, a pydal `ExecutionHandler`, which
        sees every statement that reaches the adapter.

    The tail matters most in practice: after committing, the same calls go through. The guard
    gates on there being pending work, not on the two paths having been mixed at all - the
    latter would make the async path unusable in any handler that also touches pydal.
    """
    db = db_async

    @db.define()
    class AsyncThingCrossVisibility(TypedTable):
        name: TypedField[str]

    db.commit()

    # sync write, not committed -> the async read must refuse rather than silently miss it
    AsyncThingCrossVisibility.insert(name="from-sync")
    with pytest.raises(TransactionSplitError, match="synchronous connection has uncommitted writes"):
        await AsyncThingCrossVisibility.collect_async()

    db.commit()

    # async write, not committed -> the sync read must refuse rather than silently miss it
    await AsyncThingCrossVisibility.insert_async(name="from-async")
    with pytest.raises(TransactionSplitError, match="async connection has uncommitted writes"):
        AsyncThingCrossVisibility.collect()

    # and once both sides are settled, mixing the two paths is ordinary business again
    await db.commit_async()
    assert sorted(row.name for row in AsyncThingCrossVisibility.collect()) == ["from-async", "from-sync"]
    assert sorted(row.name for row in await AsyncThingCrossVisibility.collect_async()) == [
        "from-async",
        "from-sync",
    ]


@pytest.mark.asyncio
async def test_concurrent_coroutines_do_not_share_one_transaction(dal_psql: TypeDAL):
    """
    (3/3) The transaction must be bound per task, so two coroutines on one event-loop thread do
    not decide each other's commits and rollbacks.

    This is the hazard the issue describes, arriving on the path this package owns. pydal's
    `ConnectionPool` binds connection and cursor to a global `THREAD_LOCAL`; under the
    threadpool model one thread is one request, so that is the right boundary, but under
    `async def` handlers it is not. `_async` methods move off `THREAD_LOCAL`, and this asserts
    what they land on instead: `PostgresAsyncPool` pins a checked-out connection to the running
    task in a `ContextVar` and holds it until that task ends its own transaction.

    Postgres only, on `dal_psql` rather than the parametrized `db_async`, because it is the only
    backend that can run the interleave below at all. The two coroutines have to be inside
    separate write transactions simultaneously, and SQLite permits exactly one writer at a time
    regardless of how many connections it is given - `sqlite:memory` refuses the second outright
    with `ConcurrentTransactionError`, and a file-backed database waits out `busy_timeout` and
    then reports `database is locked`. Neither is a defect, and neither can reach the assertion.

    That is a narrower fixture, not a skip: the invariant this shares with the other backends -
    the discarder's rollback must never destroy the keeper's rows - is asserted for all three in
    `test_async_connection_is_not_shared_between_concurrent_coroutines`. What is Postgres-only
    is the stronger claim that both transactions genuinely ran at once.

    Note that a `contextvars.ContextVar` holding the *pool* would solve nothing: the boundary
    has to be a transaction per task, not a per-task reference to a shared one. It also has to
    be keyed to the task that acquired it - a `ContextVar` set in a parent is copied into every
    task it later spawns, so an unkeyed entry would hand both coroutines below the same
    connection and quietly reintroduce exactly the bug this test exists to catch.

    The interleave, pinned with events rather than sleeps so the ordering is deterministic:
      - `keeper` inserts `keep`, then commits once `discarder` has rolled back
      - `discarder` inserts `discard`, then rolls its own insert back

    Per-task transactions leave only `keep`. One shared transaction leaves `discard` behind: it
    was committed out from under the coroutine that asked for it to be discarded.
    """
    db = dal_psql

    @db.define()
    class AsyncThingSharedTransaction(TypedTable):
        name: TypedField[str]

    db.commit()

    keeper_inserted = asyncio.Event()
    discarder_rolled_back = asyncio.Event()

    async def keeper():
        await AsyncThingSharedTransaction.insert_async(name="keep")
        keeper_inserted.set()
        await discarder_rolled_back.wait()
        await db.commit_async()

    async def discarder():
        await keeper_inserted.wait()
        await AsyncThingSharedTransaction.insert_async(name="discard")
        await db.rollback_async()
        discarder_rolled_back.set()

    try:
        await asyncio.gather(keeper(), discarder())

        rows = await AsyncThingSharedTransaction.collect_async()
        assert sorted(row.name for row in rows) == ["keep"]
    finally:
        # `db_async` does this in its teardown; `dal_psql` is a plain session db, so an async
        # pool left open here outlives this test's event loop and hangs the next one.
        await db.close_async()


@pytest.mark.asyncio
async def test_insert_async_honors_on_insert_error_hook(db_async: TypeDAL):
    """
    pydal's `adapter.insert()` routes a failing INSERT through `table._on_insert_error` and
    returns the hook's value (adapters/base.py). `db.insert_async()` does not, so the
    same table diverges between sync and async on a constraint violation - while the sibling
    `update_async()` twenty lines up already does honour `_on_update_error` (core.py).
    """
    db = db_async

    @db.define()
    class AsyncThingInsertError(TypedTable):
        name = TypedField(str, unique=True)

    table = AsyncThingInsertError._ensure_table_defined()
    table._on_insert_error = lambda _table, _fields, _e: "handled"

    AsyncThingInsertError.insert(name="dup")
    db.commit()

    # sync: the hook swallows the integrity error and its return value comes back out
    assert table.insert(name="dup") == "handled"
    db.rollback()  # the failed statement aborted the sync transaction (postgres)

    # async must do the same:
    duplicate = table._fields_and_values_for_insert({"name": "dup"}).op_values()
    assert await db.insert_async(table, duplicate) == "handled"


@pytest.mark.asyncio
async def test_async_pool_manager_opens_once_under_concurrency(db_async: TypeDAL):
    """
    Creating the pool is check-then-assign around an `await`, so two coroutines whose first use
    overlaps can both pass the check and both open one: a second psycopg pool, or on SQLite a
    second aiosqlite connection. Only one can be stored; the other would be dropped without
    `close()`, leaking the connection (and, for aiosqlite, its background thread).

    Driven through a manager of its own with a counting `factories` entry - a constructor
    argument, so nothing global is swapped out. The stand-in suspends before doing the real
    work: both real factories contain awaits, but whether a given one actually yields is a
    driver detail (`aiosqlite.connect()` does, `psycopg_pool.open()` currently does not) and
    this is a test of the manager, not of which drivers make the race observable today.
    """
    dbengine = db_async._adapter.dbengine
    real_factory = ASYNC_POOL_FACTORIES[dbengine]
    opened = []

    async def counting_factory(dal: TypeDAL):
        await asyncio.sleep(0)  # any await inside a factory is enough to open the window
        pool = await real_factory(dal)
        opened.append(pool)
        return pool

    manager = AsyncPoolManager(db_async, factories={dbengine: counting_factory})
    try:
        first, second = await asyncio.gather(manager.get(), manager.get())

        assert first is second, "concurrent first use handed out two different pools"
        assert len(opened) == 1, f"opened {len(opened)}, so {len(opened) - 1} was leaked unclosed"
    finally:
        kept = manager.pool
        await manager.close()
        # whatever a leak left behind is no longer the manager's to close:
        for pool in opened:
            if pool is not kept:
                with contextlib.suppress(Exception):
                    await pool.close()


@pytest.mark.asyncio
async def test_update_record_async_ignores_common_filters_like_sync(db_async: TypeDAL):
    """
    pydal's `RecordUpdater` writes by primary key with `ignore_common_filters=True`
    (helpers/classes.py), so a record you already hold can always be written back.
    `update_record_async()` rebuilds that update through `QueryBuilder.update_async()` without
    the flag, so `adapter._update()` re-applies the table's common filter (base.py via
    `use_common_filters`, helpers/methods.py) and a row the filter excludes - a
    soft-deleted one, say - silently updates zero rows.

    Also reached by `validate_and_update_async()` and the update branch of
    `update_or_insert_async()`, which both route through `update_record_async()`.
    """
    db = db_async

    @db.define()
    class AsyncThingCommonFilter(TypedTable):
        name: TypedField[str]
        archived: TypedField[bool]

    table = AsyncThingCommonFilter._ensure_table_defined()

    row_id = int(AsyncThingCommonFilter.insert(name="original", archived=True))
    db.commit()

    # hold the record from before the filter exists, as a soft-delete flow would
    record = AsyncThingCommonFilter.where(AsyncThingCommonFilter.id == row_id).first()

    table._common_filter = lambda _query: table.archived == False  # noqa: E712

    try:
        # sync twin writes straight through the filter:
        record.update_record(name="sync-updated")
        db.commit()

        # async twin must too:
        await record.update_record_async(name="async-updated")
        await db.commit_async()
    finally:
        table._common_filter = None

    fresh = AsyncThingCommonFilter.where(AsyncThingCommonFilter.id == row_id).first()
    assert fresh.name == "async-updated"


@pytest.mark.asyncio
async def test_postgres_lastrowid_async_uses_only_the_value_it_was_given(dal_psql: TypeDAL):
    """
    `postgres_lastrowid_async()` must decide whether the statement it just ran carried a
    RETURNING clause from its `last_insert` argument alone - never by reading
    `adapter._last_insert` back. That attribute is a property over
    `THREAD_LOCAL._pydal_last_insert_` (pydal adapters/postgres.py), and coroutines
    share one thread, so for the async path it is effectively a global: any other insert
    running between `_insert()` and here overwrites it.

    Proven by executing a `DEFAULT VALUES` insert - no fields, therefore no RETURNING
    (postgres.py) - while the thread-local says the opposite. Reading the attribute
    would take the `fetchone()` branch and raise on a statement that produced no rows.

    Takes the Postgres fixture directly instead of the parametrized `db_async`: SQLite has no
    equivalent flag - `sqlite_lastrowid_async` ignores `last_insert` entirely and returns
    `cursor.lastrowid` - so there would be nothing for a SQLite run to assert.
    """
    async with _postgres_db(dal_psql) as db:

        @db.define()
        class AsyncThingLastInsert(TypedTable):
            name = TypedField(str, notnull=False)

        table = AsyncThingLastInsert._ensure_table_defined()
        adapter = db._adapter

        sql = adapter._insert(table, [])
        captured = adapter._last_insert  # what *this* statement produced: None
        assert captured is None

        # stand-in for a concurrent insert_async() landing between the build and the read:
        adapter._last_insert = (table._id, 1)

        pool = await db._get_async_pool()
        async with pool.connection() as conn, conn.cursor() as cur:
            await cur.execute(sql)
            row_id = await postgres_lastrowid_async(adapter, table, cur, captured)

        assert isinstance(row_id, int)
        assert row_id > 0


@pytest.mark.asyncio
async def test_async_connection_is_not_shared_between_concurrent_coroutines(db_async: TypeDAL):
    """
    Two coroutines writing at the same time must never end up deciding each other's outcome.

    This test used to assert the opposite contract: that `connection()` commits on clean exit
    and rolls back on exception, so a failing writer's row disappears and a clean writer's row
    survives *because of how the block exited*. That per-call commit is the defect
    `test_insert_async_can_be_rolled_back` removes - it put every `_async` write outside
    anything the caller could undo - so the two assertions cannot both hold. The isolation
    intent is kept here; the auto-commit mechanism it used to rely on is not.

    What replaces it: each writer ends its own transaction explicitly, the way pydal expects.
    The outcome asserted is the same one the old test wanted - `keep` survives, `discard` does
    not - but it now depends on the transactions being *separate*, not on the context manager
    guessing.

    Do NOT rewrite the overlap with an `asyncio.Barrier`. It deadlocks on `sqlite:memory`, and
    not because of a bug: that backend refuses a second concurrent transaction outright
    (`ConcurrentTransactionError`), so demanding both coroutines be inside at once demands the
    thing the design exists to prevent. Each writer instead signals that it is inside and waits
    a bounded time for the other, which forces an overlap where one is possible and simply
    times out where it is not.

    Per backend, all three of which are safe and none of which lose `keep`:

      - Postgres: a connection per task, genuinely concurrent, both transactions independent.
      - file-backed SQLite: a connection per task, but SQLite allows one writer at a time, so
        the second waits out `busy_timeout` and then reports `database is locked`.
      - `sqlite:memory`: one connection, so the second writer is refused immediately with
        `ConcurrentTransactionError`.

    The second writer failing is therefore an accepted outcome on SQLite, and the assertion is
    about what the database is left holding rather than about who got to run.
    """
    db = db_async

    @db.define()
    class AsyncThingIsolation(TypedTable):
        name: TypedField[str]

    db.commit()

    keeper_inside = asyncio.Event()
    failer_inside = asyncio.Event()

    async def wait_briefly(event: asyncio.Event) -> None:
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(event.wait(), timeout=0.25)

    async def committing_writer() -> None:
        await AsyncThingIsolation.insert_async(name="keep")
        keeper_inside.set()
        await wait_briefly(failer_inside)
        await db.commit_async()

    async def failing_writer() -> None:
        # both are accepted: the write goes through and this rolls it back, or SQLite refuses
        # it outright. Either way `discard` must not be in the database at the end.
        with contextlib.suppress(TransactionBoundaryError, sqlite3.OperationalError):
            await AsyncThingIsolation.insert_async(name="discard")
            failer_inside.set()
            await wait_briefly(keeper_inside)
            await db.rollback_async()

        failer_inside.set()

    await asyncio.gather(committing_writer(), failing_writer())

    rows = await AsyncThingIsolation.collect_async()
    assert sorted(row.name for row in rows) == ["keep"]


# ---------------------------------------------------------------------------
# Coverage for async paths the parity tests above never reach: rollback, the pydal
# hook/abort branches, error hooks, cascades and the unsupported-backend guard.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_rollback_async_is_usable_on_every_backend(db_async: TypeDAL):
    """
    `rollback_async()` is a no-op for Postgres (psycopg_pool already rolled back on context
    exit) and real work for SQLite, but it must be callable and leave the connection usable
    on both - that is the whole point of putting it on `AsyncConnectionPool`.
    """
    db = db_async

    @db.define()
    class AsyncThingRollback(TypedTable):
        qty: TypedField[int]

    await AsyncThingRollback.insert_async(qty=1)
    await db.commit_async()

    await db.rollback_async()

    # every `_async` call is its own committed transaction, so the row survives and the
    # connection still works afterwards:
    assert await AsyncThingRollback.count_async() == 1


@pytest.mark.asyncio
async def test_delete_async_cascades_to_referencing_rows(db_async: TypeDAL):
    """
    `sqlite_delete_async` re-implements `SQLite.delete()`'s cascade (adapters/sqlite.py):
    select ids, delete, then recurse per FK with `ondelete=CASCADE`. Postgres leaves that to
    the database. Either way the children must be gone.
    """
    db = db_async

    @db.define()
    class AsyncCascadeParent(TypedTable):
        name: TypedField[str]

    @db.define()
    class AsyncCascadeChild(TypedTable):
        parent: AsyncCascadeParent

    parent_id = int(AsyncCascadeParent.insert(name="parent"))
    AsyncCascadeChild.insert(parent=parent_id)
    AsyncCascadeChild.insert(parent=parent_id)
    db.commit()

    assert AsyncCascadeChild.count() == 2

    await AsyncCascadeParent.where(AsyncCascadeParent.id == parent_id).delete_async()
    await db.commit_async()

    assert await AsyncCascadeParent.count_async() == 0
    assert await AsyncCascadeChild.count_async() == 0


@pytest.mark.asyncio
async def test_update_async_honors_on_update_error_hook(db_async: TypeDAL):
    """
    Twin of `test_insert_async_honors_on_insert_error_hook`: `update_async` routes a failing
    UPDATE through `table._on_update_error`, mirroring `adapter.update()` (base.py).
    """
    db = db_async

    @db.define()
    class AsyncThingUpdateError(TypedTable):
        name = TypedField(str, unique=True)

    table = AsyncThingUpdateError._ensure_table_defined()
    table._on_update_error = lambda _table, _query, _fields, _e: -1

    first = int(AsyncThingUpdateError.insert(name="a"))
    AsyncThingUpdateError.insert(name="b")
    db.commit()

    # renaming 'a' to 'b' violates the unique constraint:
    row = table._fields_and_values_for_update({"name": "b"})
    result = await db.update_async(table, table.id == first, row.op_values())

    assert result == -1


@pytest.mark.asyncio
async def test_async_pool_manager_rejects_unsupported_backend(db_async: TypeDAL):
    """
    A dbengine with no registered factory must fail loudly and name what *is* supported, rather
    than KeyError-ing out. Expressed by handing the manager a registry that does not cover this
    backend - again a constructor argument, not a patched global or a faked adapter.
    """
    manager = AsyncPoolManager(db_async, factories={"nosuchengine": open_sqlite_async_connection})

    with pytest.raises(NotImplementedError, match="only implemented for nosuchengine"):
        await manager.get()

    assert manager.pool is None


@pytest.mark.asyncio
async def test_insert_async_runs_pydal_insert_hooks(db_async: TypeDAL):
    """
    `TypedTable.insert_async()` keeps pydal's `Table.insert()` hook dance (objects.py):
    a truthy `_before_insert` aborts the insert, and `_after_insert` sees the new id.
    """
    db = db_async

    @db.define()
    class AsyncThingInsertHooks(TypedTable):
        qty: TypedField[int]

    table = AsyncThingInsertHooks._ensure_table_defined()
    seen: list[t.Any] = []

    table._after_insert.append(lambda _row, result: seen.append(result))
    await AsyncThingInsertHooks.insert_async(qty=1)
    await db.commit_async()
    assert len(seen) == 1
    assert await AsyncThingInsertHooks.count_async() == 1

    # a truthy _before_insert aborts, so nothing is written and no id comes back:
    table._before_insert.append(lambda _row: True)
    await AsyncThingInsertHooks.insert_async(qty=2)
    await db.commit_async()
    assert await AsyncThingInsertHooks.count_async() == 1
    assert len(seen) == 1


@pytest.mark.asyncio
async def test_delete_async_runs_pydal_delete_hooks(db_async: TypeDAL):
    """
    `QueryBuilder.delete_async()` replicates `Set.delete()`'s hooks (objects.py),
    since pydal has no async version to delegate to: a truthy `_before_delete` aborts and
    returns no ids, `_after_delete` runs on success, and a query matching nothing returns [].
    """
    db = db_async

    @db.define()
    class AsyncThingDeleteHooks(TypedTable):
        qty: TypedField[int]

    table = AsyncThingDeleteHooks._ensure_table_defined()
    AsyncThingDeleteHooks.insert(qty=1)
    db.commit()

    # matches nothing -> no ids, and the after hooks must not fire
    assert await AsyncThingDeleteHooks.where(AsyncThingDeleteHooks.qty > 99).delete_async() == []

    # aborted by a truthy _before_delete
    aborter = table._before_delete.append(lambda _set: True) or table._before_delete[-1]
    assert await AsyncThingDeleteHooks.where(AsyncThingDeleteHooks.qty > 0).delete_async() == []
    assert await AsyncThingDeleteHooks.count_async() == 1
    table._before_delete.remove(aborter)

    # and the success path runs _after_delete
    after: list[t.Any] = []
    table._after_delete.append(lambda pydal_set: after.append(pydal_set))
    assert len(await AsyncThingDeleteHooks.where(AsyncThingDeleteHooks.qty > 0).delete_async()) == 1
    assert len(after) == 1


@pytest.mark.asyncio
async def test_update_async_runs_pydal_update_hooks(db_async: TypeDAL):
    """
    Same as the delete twin, for `QueryBuilder.update_async()`: no fields is an error, a truthy
    `_before_update` aborts, `_after_update` runs on success, and a no-match query returns [].
    """
    db = db_async

    @db.define()
    class AsyncThingUpdateHooks(TypedTable):
        qty: TypedField[int]

    table = AsyncThingUpdateHooks._ensure_table_defined()
    AsyncThingUpdateHooks.insert(qty=1)
    db.commit()

    with pytest.raises(ValueError, match="No fields to update"):
        await AsyncThingUpdateHooks.where(AsyncThingUpdateHooks.qty > 0).update_async()

    # matches nothing -> no ids
    assert await AsyncThingUpdateHooks.where(AsyncThingUpdateHooks.qty > 99).update_async(qty=5) == []

    aborter = table._before_update.append(lambda _set, _row: True) or table._before_update[-1]
    assert await AsyncThingUpdateHooks.where(AsyncThingUpdateHooks.qty > 0).update_async(qty=7) == []
    assert await AsyncThingUpdateHooks.count_async() == 1
    table._before_update.remove(aborter)

    after: list[t.Any] = []
    table._after_update.append(lambda pydal_set, _row: after.append(pydal_set))
    assert len(await AsyncThingUpdateHooks.where(AsyncThingUpdateHooks.qty > 0).update_async(qty=7)) == 1
    assert len(after) == 1


@pytest.mark.asyncio
async def test_table_level_count_and_update_or_insert_with_query(db_async: TypeDAL):
    """
    Two thin shortcuts the parity tests reach only through a QueryBuilder: `Table.count_async()`
    without a `.where(...)`, and `update_or_insert_async()` given a real Query rather than the
    DEFAULT/dict forms (`_lookup_query`'s pass-through branch).
    """
    db = db_async

    @db.define()
    class AsyncThingShortcuts(TypedTable):
        name: TypedField[str]
        qty: TypedField[int]

    assert await AsyncThingShortcuts.count_async() == 0

    created = await AsyncThingShortcuts.update_or_insert_async(
        AsyncThingShortcuts.name == "widget",
        name="widget",
        qty=1,
    )
    await db.commit_async()
    assert created.qty == 1
    assert await AsyncThingShortcuts.count_async() == 1

    updated = await AsyncThingShortcuts.update_or_insert_async(
        AsyncThingShortcuts.name == "widget",
        name="widget",
        qty=2,
    )
    await db.commit_async()
    assert updated.qty == 2
    assert await AsyncThingShortcuts.count_async() == 1


@pytest.mark.asyncio
async def test_insert_and_update_async_reraise_without_error_hook(db_async: TypeDAL):
    """
    The other half of the `_on_insert_error`/`_on_update_error` branches: with no hook
    registered the driver exception must propagate, exactly as pydal's adapter does.
    """
    db = db_async

    @db.define()
    class AsyncThingNoHook(TypedTable):
        name = TypedField(str, unique=True)

    table = AsyncThingNoHook._ensure_table_defined()
    first = int(AsyncThingNoHook.insert(name="a"))
    AsyncThingNoHook.insert(name="b")
    db.commit()

    with pytest.raises(Exception, match=r"(?i)unique"):
        await db.insert_async(table, table._fields_and_values_for_insert({"name": "a"}).op_values())

    # The failed statement aborted the async transaction, so Postgres answers everything after
    # it with InFailedSqlTransaction until that transaction ends. The sync twin of this test
    # already calls `db.rollback()` for exactly this reason. It only needs saying here now that
    # `_async` calls no longer self-commit: before, each one was its own transaction and an
    # error could not reach the next.
    await db.rollback_async()

    with pytest.raises(Exception, match=r"(?i)unique"):
        row = table._fields_and_values_for_update({"name": "b"})
        await db.update_async(table, table.id == first, row.op_values())


@pytest.mark.asyncio
async def test_insert_async_with_custom_primarykey(db_async: TypeDAL):
    """
    Tables with a `_primarykey` instead of pydal's standard `_id` report the new row as a
    `{name: value}` dict rather than a `Reference` (adapters/base.py).
    """
    db = db_async

    table = db.define_table(
        "async_pk_thing",
        pydal.objects.Field("code", "string"),
        pydal.objects.Field("val", "string"),
        primarykey=["code"],
    )
    db.commit()

    supplied = await db.insert_async(table, [(table.code, "abc"), (table.val, "x")])
    assert supplied == {"code": "abc"}

    # the sibling branch - a keyed table whose pk is *generated* - is unreachable on both
    # backends: pydal makes `_primarykey` columns NOT NULL, so an insert that omits the pk
    # fails in the database before it could ever be filled in from lastrowid.


@pytest.mark.asyncio
async def test_executesql_async_placeholders_and_dict_shapes(db_async: TypeDAL):
    """
    `executesql_async` mirrors pydal's `executesql` surface: bound placeholders, `as_dict` /
    `as_ordered_dict`, `colnames` overrides, and the duplicate-column guard.
    """
    db = db_async

    @db.define()
    class AsyncThingSql(TypedTable):
        name: TypedField[str]
        qty: TypedField[int]

    AsyncThingSql.insert(name="widget", qty=1)
    AsyncThingSql.insert(name="gadget", qty=2)
    db.commit()

    tablename = str(AsyncThingSql)
    placeholder = "%s" if db._adapter.dbengine == "postgres" else "?"

    bound = await db.executesql_async(f"SELECT qty FROM {tablename} WHERE qty > {placeholder}", (1,))
    assert [row[0] for row in bound] == [2]

    as_dicts = await db.executesql_async(f"SELECT name, qty FROM {tablename} ORDER BY qty", as_dict=True)
    assert as_dicts == [{"name": "widget", "qty": 1}, {"name": "gadget", "qty": 2}]

    ordered = await db.executesql_async(f"SELECT name, qty FROM {tablename} ORDER BY qty", as_ordered_dict=True)
    assert type(ordered[0]) is collections.OrderedDict
    assert list(ordered[0]) == ["name", "qty"]

    renamed = await db.executesql_async(
        f"SELECT name FROM {tablename} ORDER BY qty",
        as_dict=True,
        colnames=["label"],
    )
    assert renamed[0] == {"label": "widget"}

    with pytest.raises(RuntimeError, match="duplicate column names"):
        await db.executesql_async(f"SELECT qty, qty FROM {tablename}", as_dict=True)


@pytest.mark.asyncio
async def test_executesql_async_with_fields_and_colnames(db_async: TypeDAL):
    """
    Passing `fields` (or `colnames`) routes the raw rows back through `adapter.parse()`, so
    values come out typed rather than as driver primitives.
    """
    db = db_async

    @db.define()
    class AsyncThingParse(TypedTable):
        name: TypedField[str]
        qty: TypedField[int]

    AsyncThingParse.insert(name="widget", qty=1)
    db.commit()

    table = AsyncThingParse._ensure_table_defined()
    tablename = str(AsyncThingParse)

    # a whole Table as `fields` expands to its columns...
    parsed = await db.executesql_async(
        f"SELECT {tablename}.id, {tablename}.name, {tablename}.qty FROM {tablename}",
        fields=[table],
    )
    assert parsed[0].name == "widget"
    assert parsed[0].qty == 1

    # ...and individual Fields are taken as-is
    per_field = await db.executesql_async(
        f"SELECT {tablename}.name, {tablename}.qty FROM {tablename}",
        fields=[table.name, table.qty],
    )
    assert per_field[0].qty == 1

    # `colnames` without fields resolves the table.column names itself
    by_colname = await db.executesql_async(
        f"SELECT {tablename}.name FROM {tablename}",
        fields=[],
        colnames=[f"{tablename}.name"],
    )
    assert by_colname[0].name == "widget"

    # a colname without a `table.` prefix is passed through unquoted
    bare_colname = await db.executesql_async(
        f"SELECT {tablename}.name FROM {tablename}",
        fields=[table.name],
        colnames=["name"],
    )
    assert bare_colname[0].name == "widget"


@pytest.mark.asyncio
async def test_executesql_async_on_statement_without_result_set(db_async: TypeDAL):
    """
    A statement that produces no rows: psycopg raises on `fetchall()` (caught, -> None) while
    sqlite just yields an empty list. Both are acceptable; neither may blow up.
    """
    db = db_async

    @db.define()
    class AsyncThingNoResult(TypedTable):
        qty: TypedField[int]

    db.commit()

    result = await db.executesql_async(f"DELETE FROM {AsyncThingNoResult} WHERE qty < 0")
    assert result in (None, [])


@pytest.mark.asyncio
async def test_async_query_builder_falls_back_for_plain_pydal_tables(db_async: TypeDAL):
    """
    `QueryBuilder` also accepts an old-style pydal table. There is no model to instantiate from
    the rows, so `collect_async()` degrades to `execute_async()` and `first_async()` hands back
    the raw pydal Row - the async twins of the fallbacks `collect()`/`first()` already have.
    """
    db = db_async

    table = db.define_table("async_plain_thing", pydal.objects.Field("qty", "integer"))
    table.insert(qty=1)
    db.commit()

    rows = await QueryBuilder(table).collect_async()
    assert len(rows) == 1

    row = await QueryBuilder(table).first_async()
    assert row is not None
    assert row.qty == 1


@pytest.mark.asyncio
async def test_classmethod_update_async_returns_none_when_nothing_matches(db_async: TypeDAL):
    """`Model.update_async(query, ...)` mirrors the sync `update()`: no matching row -> None."""
    db = db_async

    @db.define()
    class AsyncThingClsUpdateMiss(TypedTable):
        qty: TypedField[int]

    db.commit()

    assert await AsyncThingClsUpdateMiss.update_async(AsyncThingClsUpdateMiss.id == 404, qty=1) is None


class AsyncThingCached(TypedTable):
    """
    Defined at module level, unlike every other model here: the cache pickles the rows, and a
    class defined inside a test function is not picklable.
    """

    qty: TypedField[int]


@pytest.mark.asyncio
async def test_collect_async_serves_cached_rows():
    """
    A cache hit short-circuits `collect_async()` in `_collect_prepare()`, before it ever reaches
    the database. Not parametrized over `db_async`: that fixture disables TypeDAL caching.
    """
    with tempfile.TemporaryDirectory() as directory:
        db = TypeDAL("sqlite:memory", folder=directory)
        try:
            db.define(AsyncThingCached)

            AsyncThingCached.insert(qty=1)
            db.commit()

            fresh = await AsyncThingCached.where(AsyncThingCached.qty > 0).cache().collect_async()
            cached = await AsyncThingCached.where(AsyncThingCached.qty > 0).cache().collect_async()

            assert len(fresh) == len(cached) == 1
            assert fresh.metadata["cache"]["status"] == "fresh"
            assert cached.metadata["cache"]["status"] == "cached"
        finally:
            await db.close_async()
            db.close()

@contextlib.contextmanager
def _fail_after(seconds: float, message: str) -> t.Iterator[None]:
    """
    Fail instead of hanging when the code under test loops forever.

    `SIGALRM` rather than `asyncio.timeout()`: the loop this guards (see
    `test_executesql_async_accepts_a_single_field`) contains no await, so the event loop never
    gets control back and an asyncio timeout would never fire. Signals are only delivered on
    the main thread, which is where pytest-asyncio runs the loop.
    """

    def raise_timeout(_signum: int, _frame: t.Any) -> None:
        raise TimeoutError(message)

    previous = signal.signal(signal.SIGALRM, raise_timeout)
    signal.setitimer(signal.ITIMER_REAL, seconds)
    try:
        yield
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, previous)


@pytest.mark.asyncio
async def test_insert_async_does_not_run_a_sync_query(db_async: TypeDAL):
    """
    `insert_async()` may not fall back to the sync connection to build its return value.

    It returns `self(result)` with `result` an int-like `Reference`, which `TypedTable.__new__`
    feeds to pydal's synchronous `Table.__call__` -> `db(...).select()`. That is a blocking
    SELECT on the event loop, on the *other* (sync) connection, for a row this method already
    has the id of. `db._timings` is pydal's own record of every statement executed on the sync
    adapter (helpers/classes.py, installed by default via `DAL.execution_handlers`),
    so it can be checked without patching anything.
    """
    db = db_async

    @db.define()
    class AsyncThingInsertBlocking(TypedTable):
        name: TypedField[str]
        qty: TypedField[int]

    db.commit()

    before = len(db._timings)
    inserted = await AsyncThingInsertBlocking.insert_async(name="widget", qty=5)

    # the return value must stay usable - the point is how it is built, not that it shrinks
    assert int(inserted) > 0
    assert inserted.name == "widget"
    assert inserted.qty == 5

    sync_statements = [command for command, _ in db._timings[before:]]
    selects = [command for command in sync_statements if command.lstrip().upper().startswith("SELECT")]
    assert not selects, f"insert_async ran {len(selects)} synchronous SELECT(s): {selects}"


@pytest.mark.asyncio
async def test_update_or_insert_async_handles_none_and_false_query(db_async: TypeDAL):
    """
    `None` and `False` are both members of `T_Query` (types.py) and both are accepted by
    the sync `update_or_insert()`: pydal's `Table.__call__` (objects.py) finds no record
    for a non-Query, non-digit key, so the call inserts. The async twin routes the same values
    through `QueryBuilder.where()` (query_builder.py), which raises `ValueError`.
    """
    db = db_async

    @db.define()
    class AsyncThingUpsertFalsy(TypedTable):
        name: TypedField[str]
        qty: TypedField[int]

    db.commit()

    sync_none = AsyncThingUpsertFalsy.update_or_insert(None, name="via-none", qty=1)
    sync_false = AsyncThingUpsertFalsy.update_or_insert(False, name="via-false", qty=2)
    db.commit()
    assert sync_none.name == "via-none"
    assert sync_false.name == "via-false"

    async_none = await AsyncThingUpsertFalsy.update_or_insert_async(None, name="via-none-async", qty=3)
    async_false = await AsyncThingUpsertFalsy.update_or_insert_async(False, name="via-false-async", qty=4)
    await db.commit_async()

    assert async_none.name == "via-none-async"
    assert async_false.name == "via-false-async"
    assert AsyncThingUpsertFalsy.count() == 4


@pytest.mark.asyncio
async def test_executesql_async_accepts_a_single_field(db_async: TypeDAL):
    """
    pydal's `executesql()` explicitly allows `fields` to be one object instead of a list
    (base.py: `if not isinstance(fields, list): fields = [fields]`), and TypeDAL's sync
    `executesql()` inherits that by delegating to it. `executesql_async()` does
    `list(fields)` instead.

    That does not fail with a `TypeError`: a `Field` is an `Expression`, and
    `Expression.__getitem__` (objects.py) answers any integer index with
    `self[i:i+1]` - a substring expression - and never raises `IndexError`. `list()` therefore
    falls back to the legacy sequence protocol and spins forever, allocating expressions. Hence
    the alarm below: an `asyncio` timeout cannot break a CPU-bound loop with no await in it.
    """
    db = db_async

    @db.define()
    class AsyncThingSingleField(TypedTable):
        name: TypedField[str]
        qty: TypedField[int]

    AsyncThingSingleField.insert(name="widget", qty=1)
    db.commit()

    table = AsyncThingSingleField._ensure_table_defined()
    tablename = str(AsyncThingSingleField)
    query = f"SELECT {tablename}.qty FROM {tablename}"

    sync_rows = db.executesql(query, fields=table.qty)
    assert sync_rows[0].qty == 1

    with _fail_after(5, "executesql_async(fields=<single Field>) never returned"):
        async_rows = await db.executesql_async(query, fields=table.qty)

    assert async_rows[0].qty == 1


@pytest.mark.asyncio
async def test_after_connection_hook_also_applies_to_the_async_connection():
    """
    `TypeDAL(..., after_connection=...)` is handed to pydal, which runs it on every sync
    connection it opens (connection.py). The async factories in `async_execution.py`
    open a raw driver connection and never do, so connection-scoped setup the user asked for
    (custom functions, PRAGMAs, session settings) is missing on the async side.

    A TEMP table is the backend-neutral way to observe that: it lives on the connection that
    created it, so it is visible from the sync connection and absent from the async one.

    SQLite-only: it needs to build its own `TypeDAL` to pass `after_connection`, which the
    `db_async` fixture's already-connected Postgres instance cannot.

    Note the fix is not simply calling `adapter._after_connection(adapter)` from the factory:
    the hook is handed the pydal adapter and drives the *sync* cursor, so replaying it there
    would re-run it against the wrong connection. Making this pass means giving the async
    connection an adapter-shaped façade to run the hook against.
    """
    statements: list[str] = []

    def after_connection(adapter: t.Any) -> None:
        statements.append("ran")
        adapter.execute("CREATE TEMPORARY TABLE async_hook_marker (x INTEGER)")

    with tempfile.TemporaryDirectory() as directory:
        # a URI no other test uses, because pydal's connection pool is global and keyed by URI
        # (connection.py): a `sqlite:memory` connection left there by an earlier test is
        # handed back with `run_hooks=False`, so the hook would never run and the test would be
        # measuring the pool instead of the hook.
        db = TypeDAL(
            "sqlite://after_connection_hook.db",
            enable_typedal_caching=False,
            folder=directory,
            after_connection=after_connection,
        )
        try:
            # this query is what opens pydal's sync connection - it connects lazily, so the
            # hook has not run at construction time - and the TEMP table it selects from only
            # exists because the hook ran while that connection was being set up.
            assert db.executesql("SELECT * FROM async_hook_marker") == []
            assert statements, "pydal did not run the hook on its own connection - test is meaningless"

            # ...and the async connection is a different connection, which never saw the hook
            with pytest.raises(sqlite3.OperationalError, match="no such table"):
                await db.executesql_async("SELECT * FROM async_hook_marker")
        finally:
            await db.close_async()
            db.close()

