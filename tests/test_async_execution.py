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
import tempfile
import time
import typing as t
from decimal import Decimal

import pydal.objects
import pytest
import pytest_asyncio

from src.typedal import TypeDAL, TypedField, TypedTable
from src.typedal.async_execution import (
    ASYNC_POOL_FACTORIES,
    AsyncPoolManager,
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


# One factory per backend the async execution path targets. Adding a new backend (e.g. MySQL)
# is adding a function + an entry here, not editing branching logic in the fixture below.
# (Every factory currently takes `dal_psql` as input for simplicity; a backend needing a
# differently-shaped upstream fixture - e.g. its own testcontainer - would need its factory
# signature adjusted accordingly, but the registry/dispatch shape stays the same.)
_ASYNC_DB_FACTORIES: dict[str, t.Callable[[TypeDAL], t.AsyncContextManager[TypeDAL]]] = {
    "postgres": _postgres_db,
    "sqlite": _sqlite_db,
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


# ---------------------------------------------------------------------------
# Known defects in the async execution path.
#
# Each test below asserts the behaviour the async path SHOULD have - in every case parity
# with the sync path it is a twin of. They fail against the current implementation; they are
# reproductions, not a regression net, and should go green as the defects are fixed.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_insert_async_honors_on_insert_error_hook(db_async: TypeDAL):
    """
    pydal's `adapter.insert()` routes a failing INSERT through `table._on_insert_error` and
    returns the hook's value (adapters/base.py:541-549). `db.insert_async()` does not, so the
    same table diverges between sync and async on a constraint violation - while the sibling
    `update_async()` twenty lines up already does honour `_on_update_error` (core.py:694-699).
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
    (helpers/classes.py:357), so a record you already hold can always be written back.
    `update_record_async()` rebuilds that update through `QueryBuilder.update_async()` without
    the flag, so `adapter._update()` re-applies the table's common filter (base.py:566-568 via
    `use_common_filters`, helpers/methods.py:49-54) and a row the filter excludes - a
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
    `THREAD_LOCAL._pydal_last_insert_` (pydal adapters/postgres.py:128-133), and coroutines
    share one thread, so for the async path it is effectively a global: any other insert
    running between `_insert()` and here overwrites it.

    Proven by executing a `DEFAULT VALUES` insert - no fields, therefore no RETURNING
    (postgres.py:149-162) - while the thread-local says the opposite. Reading the attribute
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
    `SqliteAsyncConnection.connection()` yields the single connection it wraps to every caller
    (async_execution.py:95-103) and commits on clean exit / rolls back on exception. Two
    coroutines inside it simultaneously are therefore in the *same* transaction, and whichever
    exits first decides for both: a clean writer's row gets discarded by an unrelated failure,
    or a failed writer's row gets committed by an unrelated success.

    `SqliteAsyncConnection`'s docstring promises every `_async` call is its own committed
    transaction; that only holds while calls never overlap. Postgres passes this test, since
    psycopg_pool hands out distinct connections.

    Do NOT rewrite this with an `asyncio.Barrier`: it deadlocks, and not because of a bug.
    SQLite cannot fix this by handing each caller its own connection the way psycopg_pool does
    - two aiosqlite connections to pydal's `sqlite:memory` (shared-cache, `uri: True`) answer
    the second concurrent writer with `OperationalError: database table is locked`, which no
    busy-timeout retries. So the fix has to *serialize* callers, and a barrier demands the one
    thing the fix exists to prevent: two coroutines inside `connection()` at the same time.

    Instead each writer signals that it is inside and waits a bounded time for the other. That
    forces the overlap where one is possible (the unfixed, shared-connection code) and simply
    times out where it is not (serialized), so the assertion below is about the transactional
    outcome either way, on both backends.
    """
    db = db_async

    @db.define()
    class AsyncThingIsolation(TypedTable):
        name: TypedField[str]

    tablename = str(AsyncThingIsolation)
    pool = await db._get_async_pool()
    keeper_inside = asyncio.Event()
    failer_inside = asyncio.Event()

    async def wait_briefly(event: asyncio.Event) -> None:
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(event.wait(), timeout=0.25)

    async def committing_writer():
        async with pool.connection() as conn, conn.cursor() as cur:
            await cur.execute(f"INSERT INTO {tablename} (name) VALUES ('keep')")
            keeper_inside.set()
            await wait_briefly(failer_inside)
            # clean exit -> this row must survive

    async def failing_writer():
        with contextlib.suppress(RuntimeError):
            async with pool.connection() as conn, conn.cursor() as cur:
                await cur.execute(f"INSERT INTO {tablename} (name) VALUES ('discard')")
                failer_inside.set()
                await wait_briefly(keeper_inside)
                raise RuntimeError("boom")  # -> this row must be rolled back

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
    `sqlite_delete_async` re-implements `SQLite.delete()`'s cascade (adapters/sqlite.py:93-104):
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
    UPDATE through `table._on_update_error`, mirroring `adapter.update()` (base.py:585-589).
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
    `TypedTable.insert_async()` keeps pydal's `Table.insert()` hook dance (objects.py:960-968):
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
    `QueryBuilder.delete_async()` replicates `Set.delete()`'s hooks (objects.py:3010-3017),
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

    with pytest.raises(Exception, match=r"(?i)unique"):
        row = table._fields_and_values_for_update({"name": "b"})
        await db.update_async(table, table.id == first, row.op_values())


@pytest.mark.asyncio
async def test_insert_async_with_custom_primarykey(db_async: TypeDAL):
    """
    Tables with a `_primarykey` instead of pydal's standard `_id` report the new row as a
    `{name: value}` dict rather than a `Reference` (adapters/base.py:550-563).
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
