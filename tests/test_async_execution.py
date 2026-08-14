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
import contextlib
import tempfile
import time
import typing as t
from decimal import Decimal

import pytest
import pytest_asyncio

from src.typedal import TypeDAL, TypedField, TypedTable
from src.typedal.async_execution import ASYNC_POOL_FACTORIES
from src.typedal.fields import DecimalField, JSONField


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
        AsyncThingValidateUpdate.id == int(existing_id), qty=9,
    )
    await db.commit_async()
    assert errors is None
    assert row is not None
    assert row.qty == 9

    _row, errors = await AsyncThingValidateUpdate.validate_and_update_async(
        AsyncThingValidateUpdate.id == int(existing_id), qty="not-a-number",
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
        AsyncThingValidateUpsert.name == "widget", name="widget", qty=1,
    )
    await db.commit_async()
    assert errors is None
    assert inserted.qty == 1
    assert AsyncThingValidateUpsert.count() == 1

    updated, errors = await AsyncThingValidateUpsert.validate_and_update_or_insert_async(
        AsyncThingValidateUpsert.name == "widget", name="widget", qty=2,
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
async def test_get_async_pool_is_opened_once_under_concurrency(
    db_async: TypeDAL,
    monkeypatch: pytest.MonkeyPatch,
):
    """
    `_get_async_pool()` checks `self._async_pool is None`, awaits the factory, then assigns
    (core.py:602-612). Two coroutines whose first DB use overlaps both pass the check and both
    open one: a second psycopg pool, or on SQLite a second aiosqlite connection. Only one is
    stored; the other is dropped without `close()`, leaking the connection (and, for aiosqlite,
    its background thread).

    The stand-in factory suspends before doing the real work. Both real factories contain
    awaits, but *whether* a given one actually yields to the loop is a driver detail rather
    than a guarantee - `aiosqlite.connect()` does, `psycopg_pool`'s `open()` currently does
    not - and this is a test of `_get_async_pool()`'s check-then-assign, not of which drivers
    happen to make it observable today.
    """
    db = db_async

    # start from "never opened", whatever earlier tests on this session-scoped DAL did:
    await db.close_async()

    dbengine = db._adapter.dbengine
    real_factory = ASYNC_POOL_FACTORIES[dbengine]
    opened = []

    async def counting_factory(dal: TypeDAL):
        await asyncio.sleep(0)  # any await inside a factory is enough to open the window
        pool = await real_factory(dal)
        opened.append(pool)
        return pool

    monkeypatch.setitem(ASYNC_POOL_FACTORIES, dbengine, counting_factory)

    try:
        first, second = await asyncio.gather(db._get_async_pool(), db._get_async_pool())

        assert first is second, "concurrent first use handed out two different pools"
        assert len(opened) == 1, f"opened {len(opened)}, so {len(opened) - 1} was leaked unclosed"
    finally:
        # don't let this test's own leak poison the rest of the session:
        for pool in opened:
            if pool is not db._async_pool:
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
async def test_insert_async_lastrowid_does_not_read_shared_last_insert(
    db_async: TypeDAL,
    monkeypatch: pytest.MonkeyPatch,
):
    """
    `postgres_lastrowid_async()` decides whether the INSERT it just ran carried a RETURNING
    clause by reading `adapter._last_insert` (async_execution.py:176) - a property over
    `THREAD_LOCAL._pydal_last_insert_` (pydal adapters/postgres.py:128-133). Coroutines share
    one thread, so that thread-local provides no isolation whatsoever here: for the async path
    it is effectively a global.

    `insert_async()` sets it via `adapter._insert()` (core.py:734) and reads it several awaits
    later (core.py:745); any other insert landing in that window overwrites it. The window is
    made deterministic here rather than raced: the statement built is a `DEFAULT VALUES` insert
    (no fields -> no RETURNING, pydal postgres.py:149-162), while a concurrent normal insert
    leaves the flag truthy - so lastrowid tries to `fetchone()` a result that does not exist.
    """
    db = db_async
    if db._adapter.dbengine != "postgres":
        pytest.skip("only the postgres lastrowid strategy consults _last_insert")

    @db.define()
    class AsyncThingLastInsert(TypedTable):
        name = TypedField(str, notnull=False)

    table = AsyncThingLastInsert._ensure_table_defined()
    adapter = db._adapter
    real_get_pool = db._get_async_pool

    async def racing_get_pool():
        pool = await real_get_pool()
        # stand-in for a concurrent insert_async() finishing its own adapter._insert():
        adapter._last_insert = (table._id, 1)
        return pool

    monkeypatch.setattr(db, "_get_async_pool", racing_get_pool)

    # no fields -> INSERT INTO ... DEFAULT VALUES, which has no RETURNING clause
    result = await db.insert_async(table, [])

    assert int(result) > 0


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
    """
    db = db_async

    @db.define()
    class AsyncThingIsolation(TypedTable):
        name: TypedField[str]

    tablename = str(AsyncThingIsolation)
    pool = await db._get_async_pool()
    both_inside = asyncio.Barrier(2)

    async def committing_writer():
        async with pool.connection() as conn, conn.cursor() as cur:
            await cur.execute(f"INSERT INTO {tablename} (name) VALUES ('keep')")  # noqa: S608
            await both_inside.wait()
            # clean exit -> this row must survive

    async def failing_writer():
        with contextlib.suppress(RuntimeError):
            async with pool.connection() as conn, conn.cursor() as cur:
                await cur.execute(f"INSERT INTO {tablename} (name) VALUES ('discard')")  # noqa: S608
                await both_inside.wait()
                raise RuntimeError("boom")  # -> this row must be rolled back

    await asyncio.gather(committing_writer(), failing_writer())

    rows = await AsyncThingIsolation.collect_async()
    assert sorted(row.name for row in rows) == ["keep"]
