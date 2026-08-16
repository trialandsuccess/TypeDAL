"""
Spec for TypeDAL's async surface.

The engine is thread offload (`src/typedal/asynchronous.py`): pydal's own sync code, executed on
a worker thread that owns its connection. So the tests come in three groups:

1. parity - an `*_async` method must return exactly what its sync twin returns, on every backend;
2. transactions - flat calls autocommit, `db.session()` is how you get a transaction, and a
   session belongs to the task that opened it and to no other;
3. concurrency - the falsification test: two OS threads plus one event loop, interleaved writes,
   one shared `TypeDAL`. That is the shape py4web actually runs, and a design that polices the
   sync/async boundary with process-wide state fails it.
"""

import asyncio
import contextlib
import itertools
import tempfile
import threading
import time
import typing as t
from decimal import Decimal
from pathlib import Path

import pytest
import pytest_asyncio

from src.typedal import TypeDAL, TypedField, TypedTable
from src.typedal.asynchronous import ConnectionWorker
from src.typedal.fields import DecimalField, JSONField


class AsyncThingCached(TypedTable):
    """
    Model for the caching tests, at module level because the cache stores rows with `dill`.

    `dill` only pickles a class by reference when it can import it back by name; a class defined
    inside a test function is pickled by value instead, which drags the whole `TypeDAL` (and its
    thread locals) into the pickle and fails. Every other model here can stay local.
    """

    qty: TypedField[int]


@contextlib.asynccontextmanager
async def _postgres_db(dal_psql: TypeDAL) -> t.AsyncIterator[TypeDAL]:
    yield dal_psql


@contextlib.asynccontextmanager
async def _sqlite_db(_: TypeDAL | None = None) -> t.AsyncIterator[TypeDAL]:
    with tempfile.TemporaryDirectory() as d:
        db = TypeDAL("sqlite:memory", enable_typedal_caching=False, folder=d)
        try:
            yield db
        finally:
            db.close()


@contextlib.asynccontextmanager
async def _sqlite_file_db(_: TypeDAL | None = None) -> t.AsyncIterator[TypeDAL]:
    """
    File-backed SQLite, which is not a redundant copy of `sqlite:memory`.

    `sqlite:memory` reaches a second connection only through shared-cache mode, whose table locks
    refuse a concurrent writer outright instead of waiting; a file has a path that every worker
    thread can open for real, with the usual busy timeout. The two behave differently under
    exactly the concurrency this module is about.
    """
    with tempfile.TemporaryDirectory() as d:
        db = TypeDAL(f"sqlite://{Path(d) / 'async.db'}", enable_typedal_caching=False, folder=d)
        try:
            yield db
        finally:
            db.close()


_DB_FACTORIES: dict[str, t.Callable[[TypeDAL], t.AsyncContextManager[TypeDAL]]] = {
    "postgres": _postgres_db,
    "sqlite": _sqlite_db,
    "sqlite-file": _sqlite_file_db,
}

# the backends where a second connection can read while another one holds an open write.
# shared-cache `sqlite:memory` cannot: its table locks turn the reader away outright. That is
# sqlite's limit and it applies to plain threads just as much, so those tests skip it rather
# than pretend the engine can route around it.
_CONCURRENT_DB_FACTORIES = ["postgres", "sqlite-file"]


@pytest_asyncio.fixture(params=list(_DB_FACTORIES))
async def db_async(request: pytest.FixtureRequest, dal_psql: TypeDAL) -> t.AsyncIterator[TypeDAL]:
    """A `TypeDAL` per backend, with its worker threads stopped afterwards."""
    async with _DB_FACTORIES[request.param](dal_psql) as db:
        yield db


@pytest_asyncio.fixture(params=_CONCURRENT_DB_FACTORIES)
async def db_concurrent(request: pytest.FixtureRequest, dal_psql: TypeDAL) -> t.AsyncIterator[TypeDAL]:
    """Same, restricted to the backends where a second connection can read during a write."""
    async with _DB_FACTORIES[request.param](dal_psql) as db:
        yield db


@pytest_asyncio.fixture
async def db_cached() -> t.AsyncIterator[TypeDAL]:
    """
    A database with TypeDAL's own caching layer left *on*, which every other fixture here disables.

    File-backed, because the point of these tests is what the cache does to a real open
    transaction, and `sqlite:memory` cannot hold one across two connections.
    """
    with tempfile.TemporaryDirectory() as d:
        db = TypeDAL(f"sqlite://{Path(d) / 'async-cached.db'}", enable_typedal_caching=True, folder=d)
        try:
            yield db
        finally:
            db.close()


##########
# parity #
##########


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
    """The two divergence points the original spike found: decimal and jsonb."""
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
    assert await AsyncThingCount.count_async() == 3


@pytest.mark.asyncio
async def test_insert_async_matches_sync_insert(db_async: TypeDAL):
    """insert_async must return a usable id, and the row must be committed and visible."""
    db = db_async

    @db.define()
    class AsyncThingInsert(TypedTable):
        name: TypedField[str]
        qty: TypedField[int]

    new_id = await AsyncThingInsert.insert_async(name="widget", qty=5)

    assert int(new_id) > 0

    # read back over the *main thread's* connection: a flat async call commits, so this sees it
    # without any further ceremony.
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

    assert len(updated_ids) == 2
    assert len(AsyncThingUpdate.where(AsyncThingUpdate.qty == 99).collect()) == 2


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

    assert len(deleted_ids) == 2
    assert AsyncThingDelete.where(AsyncThingDelete.qty > 0).count() == 0


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
    Relationships and joins must load through the async path too.

    This is where the offload design pays for itself: the relationship code is not reimplemented
    for async, it simply runs - lazy follow-up queries included - on the worker thread.
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
async def test_all_async_and_collect_async_match_sync(db_async: TypeDAL):
    """The model-level all_async/collect_async must return the same rows as their sync twins."""
    db = db_async

    @db.define()
    class AsyncThingAll(TypedTable):
        qty: TypedField[int]

    AsyncThingAll.insert(qty=1)
    AsyncThingAll.insert(qty=2)
    db.commit()

    assert len(await AsyncThingAll.all_async()) == len(AsyncThingAll.all()) == 2
    assert len(await AsyncThingAll.collect_async()) == len(AsyncThingAll.collect()) == 2


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

    assert (await AsyncThingFirst.where(AsyncThingFirst.qty > 0).first_or_fail_async()).qty == 5

    # TypedTable-level shortcuts (no explicit .where(...)):
    async_row_3 = await AsyncThingFirst.first_async()
    assert async_row_3 is not None
    assert async_row_3.qty == 5
    assert (await AsyncThingFirst.first_or_fail_async()).qty == 5


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

    assert len(await AsyncThingPaginate.paginate_async(limit=2, page=1)) == 2


@pytest.mark.asyncio
async def test_chunk_async_matches_sync_chunk(db_async: TypeDAL):
    """chunk_async must yield the same chunks as sync chunk(), from both entry points."""
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

    assert [len(chunk) async for chunk in AsyncThingChunk.chunk_async(2)] == [2, 2, 1]


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
    assert sorted(await AsyncThingColumn.column_async(AsyncThingColumn.qty)) == [1, 2]


@pytest.mark.asyncio
async def test_execute_async_matches_sync_execute(db_async: TypeDAL):
    """execute_async must return the same raw pydal Rows as the sync execute()."""
    db = db_async

    @db.define()
    class AsyncThingExecute(TypedTable):
        qty: TypedField[int]

    AsyncThingExecute.insert(qty=1)
    db.commit()

    sync_rows = AsyncThingExecute.where(AsyncThingExecute.qty > 0).execute()
    async_rows = await AsyncThingExecute.where(AsyncThingExecute.qty > 0).execute_async()

    assert len(async_rows) == len(sync_rows) == 1
    assert async_rows.first().qty == 1


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

    assert len(await AsyncThingIntoSource.collect_into_async(AsyncThingIntoTargetAsyncBare)) == 1


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

    inserted = await AsyncThingUpsert.update_or_insert_async({"name": "widget"}, name="widget", qty=1)
    assert inserted.qty == 1
    assert AsyncThingUpsert.count() == 1

    updated = await AsyncThingUpsert.update_or_insert_async({"name": "widget"}, name="widget", qty=2)
    assert updated.qty == 2
    assert AsyncThingUpsert.count() == 1


@pytest.mark.asyncio
async def test_validate_and_insert_async_matches_sync(db_async: TypeDAL):
    """validate_and_insert_async must match sync: row on success, errors on failure."""
    db = db_async

    @db.define()
    class AsyncThingValidateInsert(TypedTable):
        qty: TypedField[int]

    row, errors = await AsyncThingValidateInsert.validate_and_insert_async(qty=5)
    assert errors is None
    assert row is not None
    assert row.qty == 5

    _row, errors = await AsyncThingValidateInsert.validate_and_insert_async(qty="not-a-number")
    assert errors is not None


@pytest.mark.asyncio
async def test_validate_and_update_async_matches_sync(db_async: TypeDAL):
    """validate_and_update_async must match sync: row on success, errors on failure."""
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
    assert errors is None
    assert inserted.qty == 1
    assert AsyncThingValidateUpsert.count() == 1

    updated, errors = await AsyncThingValidateUpsert.validate_and_update_or_insert_async(
        AsyncThingValidateUpsert.name == "widget",
        name="widget",
        qty=2,
    )
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
    assert updated_row.qty == 7

    fresh = AsyncThingRecord.where(AsyncThingRecord.id == int(row_id)).first()
    assert fresh.qty == 7

    assert await fresh.delete_record_async() == 1
    assert AsyncThingRecord.count() == 0


@pytest.mark.asyncio
async def test_async_calls_do_not_block_the_event_loop(db_async: TypeDAL):
    """
    The whole point: a query in flight must not stall other coroutines.

    A ticker sleeping every 5ms should keep ticking at ~5ms while queries run; a blocking
    implementation shows gaps close to the total query time instead.
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

    gaps = [b - a for a, b in itertools.pairwise(ticks)]
    assert max(gaps) < 0.05, f"event loop was blocked: max gap between ticks was {max(gaps) * 1000:.1f}ms"


################
# transactions #
################


@pytest.mark.asyncio
async def test_flat_async_calls_autocommit(db_async: TypeDAL):
    """
    Rule one: outside a session, every `*_async` call commits before it returns.

    Anything else silently loses writes, because the worker that ran the statement goes back to
    the pool and the next borrower is some other task entirely.
    """
    db = db_async

    @db.define()
    class AsyncThingAutocommit(TypedTable):
        qty: TypedField[int]

    await AsyncThingAutocommit.insert_async(qty=1)

    # rollback on the main thread's connection cannot undo it: it was already committed elsewhere.
    db.rollback()
    assert AsyncThingAutocommit.count() == 1

    # and commit_async/rollback_async outside a session have nothing left to settle either:
    await db.rollback_async()
    await db.commit_async()
    assert await AsyncThingAutocommit.count_async() == 1


@pytest.mark.asyncio
async def test_a_failed_flat_call_rolls_back_and_frees_the_worker(db_async: TypeDAL):
    """
    A statement that raises must roll its worker's transaction back before the worker is released.

    Without that the next borrower inherits a broken transaction - on Postgres literally so: every
    statement on that connection fails with "current transaction is aborted" until someone ends it.
    """
    db = db_async

    @db.define()
    class AsyncThingFailure(TypedTable):
        qty: TypedField[int]

    first = await AsyncThingFailure.insert_async(qty=1)

    # each backend raises its own driver error, so this is deliberately broad:
    with pytest.raises(Exception):
        # duplicate primary key: a real error from the database, mid-transaction
        await AsyncThingFailure.insert_async(id=int(first), qty=2)

    # the pool is usable again, and the failed statement left nothing behind:
    await AsyncThingFailure.insert_async(qty=3)
    db.rollback()
    assert sorted(AsyncThingFailure.column(AsyncThingFailure.qty)) == [1, 3]


@pytest.mark.asyncio
async def test_async_use_of_an_unbound_model_is_a_loud_error():
    """A model that was never passed to `db.define` has no database to offload to."""

    class AsyncThingUnbound(TypedTable):
        qty: TypedField[int]

    with pytest.raises(EnvironmentError):
        await AsyncThingUnbound.insert_async(qty=1)


@pytest.mark.asyncio
async def test_session_commits_at_the_end_of_the_block(db_concurrent: TypeDAL):
    """A session is how you get a transaction: nothing is committed until the block ends."""
    db = db_concurrent

    @db.define()
    class AsyncThingSessionCommit(TypedTable):
        qty: TypedField[int]

    async with db.session():
        await AsyncThingSessionCommit.insert_async(qty=1)
        await AsyncThingSessionCommit.insert_async(qty=2)

        # inside the session, its own connection sees both rows...
        assert await AsyncThingSessionCommit.count_async() == 2
        # ...while the main thread's connection sees none of them yet.
        db.rollback()
        assert AsyncThingSessionCommit.count() == 0

    assert AsyncThingSessionCommit.count() == 2


@pytest.mark.asyncio
async def test_session_rolls_back_when_the_block_raises(db_async: TypeDAL):
    """An exception out of the block rolls the whole transaction back, both statements included."""
    db = db_async

    @db.define()
    class AsyncThingSessionRollback(TypedTable):
        qty: TypedField[int]

    with pytest.raises(RuntimeError):
        async with db.session():
            await AsyncThingSessionRollback.insert_async(qty=1)
            raise RuntimeError("boom")

    db.rollback()
    assert AsyncThingSessionRollback.count() == 0


@pytest.mark.asyncio
async def test_session_explicit_commit_and_rollback(db_async: TypeDAL):
    """`session.commit()` / `session.rollback()` settle mid-block and start a fresh transaction."""
    db = db_async

    @db.define()
    class AsyncThingSessionExplicit(TypedTable):
        qty: TypedField[int]

    async with db.session() as session:
        await AsyncThingSessionExplicit.insert_async(qty=1)
        await session.commit()

        await AsyncThingSessionExplicit.insert_async(qty=2)
        await session.rollback()

        # committing again with nothing pending is a no-op, not an error:
        await session.commit()

    db.rollback()
    assert AsyncThingSessionExplicit.column(AsyncThingSessionExplicit.qty) == [1]


@pytest.mark.asyncio
async def test_db_commit_async_and_rollback_async_settle_the_session(db_async: TypeDAL):
    """`db.commit_async()`/`db.rollback_async()` settle whatever session the current task is in."""
    db = db_async

    @db.define()
    class AsyncThingDbSettle(TypedTable):
        qty: TypedField[int]

    async with db.session():
        await AsyncThingDbSettle.insert_async(qty=1)
        await db.rollback_async()
        assert await AsyncThingDbSettle.count_async() == 0

        await AsyncThingDbSettle.insert_async(qty=2)
        await db.commit_async()

    db.rollback()
    assert AsyncThingDbSettle.column(AsyncThingDbSettle.qty) == [2]


@pytest.mark.asyncio
async def test_run_sync_shares_the_session_transaction(db_async: TypeDAL):
    """
    `run_sync` is the feature the connection-swapping design could not offer.

    Plain sync ORM code - relationships, hooks, cascades - runs on the session's worker, so it
    sees the session's uncommitted rows and its writes belong to the same transaction.
    """
    db = db_async

    @db.define()
    class AsyncThingRunSyncOther(TypedTable):
        name: TypedField[str]

    @db.define()
    class AsyncThingRunSync(TypedTable):
        name: TypedField[str]
        other: AsyncThingRunSyncOther

    def build() -> str:
        # ordinary sync code, including a relationship join, on the session's connection
        other = AsyncThingRunSyncOther.insert(name="parent")
        AsyncThingRunSync.insert(name="child", other=other)
        return AsyncThingRunSync.join("other").first().other.name

    with pytest.raises(RuntimeError):
        async with db.session() as session:
            assert await session.run_sync(build) == "parent"
            assert await AsyncThingRunSync.count_async() == 1
            raise RuntimeError("boom")

    db.rollback()
    assert AsyncThingRunSync.count() == 0
    assert AsyncThingRunSyncOther.count() == 0


@pytest.mark.asyncio
async def test_db_run_sync_outside_a_session_autocommits(db_async: TypeDAL):
    """`db.run_sync` without a session is a flat call: offloaded, and committed on return."""
    db = db_async

    @db.define()
    class AsyncThingRunSyncFlat(TypedTable):
        qty: TypedField[int]

    def insert_two() -> int:
        AsyncThingRunSyncFlat.insert(qty=1)
        AsyncThingRunSyncFlat.insert(qty=2)
        return AsyncThingRunSyncFlat.count()

    assert await db.run_sync(insert_two) == 2

    db.rollback()
    assert AsyncThingRunSyncFlat.count() == 2


@pytest.mark.asyncio
async def test_a_task_spawned_inside_a_session_does_not_join_it(dal_psql: TypeDAL):
    """
    The trap the previous attempt fell into.

    `create_task`/`gather` copy the context, so the session binding travels into the child task -
    but two tasks interleaving statements on one connection is exactly the bug a session exists
    to prevent. The binding is stamped with its owning task and ignored anywhere else, so the
    child autocommits on a worker of its own.

    Postgres only: the claim is about two write transactions being open at the same time, and
    sqlite has exactly one writer, so there is nothing to observe there.
    """
    db = dal_psql

    @db.define()
    class AsyncThingTaskScope(TypedTable):
        source: TypedField[str]

    async def child():
        await AsyncThingTaskScope.insert_async(source="child")

    with pytest.raises(RuntimeError):
        async with db.session():
            await AsyncThingTaskScope.insert_async(source="parent")
            await asyncio.create_task(child())
            raise RuntimeError("boom")

    db.rollback()
    # the parent's row was rolled back with the session, the child's committed on its own:
    assert AsyncThingTaskScope.column(AsyncThingTaskScope.source) == ["child"]


@pytest.mark.asyncio
async def test_nested_sessions_are_one_transaction(db_concurrent: TypeDAL):
    """A nested `db.session()` in the same task joins the outer one instead of opening a second."""
    db = db_concurrent

    @db.define()
    class AsyncThingNested(TypedTable):
        qty: TypedField[int]

    async with db.session() as outer:
        await AsyncThingNested.insert_async(qty=1)

        async with db.session() as inner:
            assert inner is outer
            await AsyncThingNested.insert_async(qty=2)

        # leaving the inner block must not have committed anything:
        db.rollback()
        assert AsyncThingNested.count() == 0

    assert AsyncThingNested.count() == 2


@pytest.mark.asyncio
async def test_sessions_in_sibling_tasks_are_independent(dal_psql: TypeDAL):
    """
    Two concurrent tasks each get their own session, worker, connection and transaction.

    Postgres only, for the same reason as above: two simultaneously open write transactions.
    """
    db = dal_psql

    @db.define()
    class AsyncThingSiblings(TypedTable):
        source: TypedField[str]

    started = asyncio.Event()

    async def keeper():
        async with db.session():
            await AsyncThingSiblings.insert_async(source="keeper")
            started.set()
            await asyncio.sleep(0.05)

    async def loser():
        await started.wait()
        async with db.session():
            await AsyncThingSiblings.insert_async(source="loser")
            raise RuntimeError("boom")

    results = await asyncio.gather(keeper(), loser(), return_exceptions=True)

    assert isinstance(results[1], RuntimeError)
    db.rollback()
    assert AsyncThingSiblings.column(AsyncThingSiblings.source) == ["keeper"]


@pytest.mark.asyncio
async def test_worker_pool_is_bounded_and_reused(db_async: TypeDAL):
    """
    A worker is a connection, so the pool is bounded - and a released worker is reused, not
    replaced, which is what keeps the connection count flat under load.
    """
    db = db_async
    pool = db._async_workers

    @db.define()
    class AsyncThingPool(TypedTable):
        qty: TypedField[int]

    for _ in range(5):
        await AsyncThingPool.insert_async(qty=1)

    assert pool._created == 1
    assert len(pool._idle) == 1

    async with db.session():
        await AsyncThingPool.count_async()
        assert not pool._idle  # held for the whole session

    assert len(pool._idle) == 1  # and given back at the end


@pytest.mark.asyncio
async def test_a_saturated_pool_queues_instead_of_growing(db_async: TypeDAL):
    """
    With every worker busy, the next caller waits and is handed the first one released.

    It must be a handover, not a new worker: the bound on workers is the bound on connections.
    """
    db = db_async
    pool = db._async_workers
    pool._max_workers = 1

    @db.define()
    class AsyncThingQueue(TypedTable):
        qty: TypedField[int]

    order: list[str] = []
    holding = asyncio.Event()

    async def holder():
        async with db.session() as session:
            await session.run_sync(time.sleep, 0.05)
            holding.set()
            await session.run_sync(time.sleep, 0.05)
        order.append("holder")

    async def waiter():
        await holding.wait()
        assert not pool._idle  # nothing to hand out yet: this call has to queue
        await AsyncThingQueue.insert_async(qty=1)
        order.append("waiter")

    await asyncio.gather(holder(), waiter())

    assert order == ["holder", "waiter"]
    assert pool._created == 1  # queued, rather than opening a second connection
    assert len(pool._idle) == 1
    db.rollback()
    assert AsyncThingQueue.count() == 1


@pytest.mark.asyncio
async def test_a_cancelled_waiter_does_not_leak_its_worker(db_async: TypeDAL):
    """
    Cancellation while queued for a worker must not strand it.

    `acquire()` hands the worker over through a plain future; if the task awaiting it is cancelled
    in that same instant, the worker has to find its way back to the pool anyway.
    """
    db = db_async
    db._async_workers._max_workers = 1
    pool = db._async_workers

    @db.define()
    class AsyncThingCancel(TypedTable):
        qty: TypedField[int]

    holding = asyncio.Event()

    async def hold():
        async with db.session() as session:
            await session.run_sync(time.sleep, 0.05)
            holding.set()
            await session.run_sync(time.sleep, 0.1)

    async def queued():
        await holding.wait()
        await AsyncThingCancel.insert_async(qty=1)

    holder = asyncio.create_task(hold())
    waiter = asyncio.create_task(queued())
    await holding.wait()
    await asyncio.sleep(0)

    waiter.cancel()
    with pytest.raises(asyncio.CancelledError):
        await waiter

    await holder

    assert pool._created == 1
    assert len(pool._idle) == 1
    # the pool still works afterwards:
    await AsyncThingCancel.insert_async(qty=2)
    assert await AsyncThingCancel.count_async() == 1


@pytest.mark.asyncio
async def test_close_async_stops_the_workers(db_async: TypeDAL):
    """`close_async()` closes each worker's connection on its own thread; the pool refills after."""
    db = db_async

    @db.define()
    class AsyncThingCloseAsync(TypedTable):
        qty: TypedField[int]

    await AsyncThingCloseAsync.insert_async(qty=1)
    assert db._async_workers._created == 1

    await db.close_async()
    assert db._async_workers._created == 0

    # the database itself is untouched - only the worker threads are gone:
    assert await AsyncThingCloseAsync.count_async() == 1


###############
# concurrency #
###############


@pytest.mark.asyncio
async def test_threads_and_event_loop_share_one_typedal(db_concurrent: TypeDAL):
    """
    The acceptance test: two OS threads and one event loop, writing through one `TypeDAL`.

    This is the shape py4web actually runs pydal in (thread per request), and it is the case the
    previous attempt could not survive: its guard state was per-instance and per-process while
    pydal's connections are per-thread, so it both blocked unrelated sync callers and let
    genuinely interleaved work through. Here nothing is guarded, because nothing is shared -
    every writer, the two threads and each worker thread alike, has its own pydal connection.
    """
    db = db_concurrent

    @db.define()
    class AsyncThingInterleaved(TypedTable):
        source: TypedField[str]
        n: TypedField[int]

    db.commit()

    rounds = 10
    failures: list[BaseException] = []

    def sync_writer(name: str) -> None:
        try:
            for i in range(rounds):
                AsyncThingInterleaved.insert(source=name, n=i)
                db.commit()
                time.sleep(0.001)
        except BaseException as e:  # pragma: no cover - only reached when the test fails
            failures.append(e)
        finally:
            # hand this thread's connection back to pydal instead of stranding it on a dead thread
            db._adapter.close(action="commit", really=False)

    async def async_writer() -> None:
        for i in range(rounds):
            await AsyncThingInterleaved.insert_async(source="loop", n=i)
            await asyncio.sleep(0.001)

    threads = [threading.Thread(target=sync_writer, args=(f"thread-{i}",)) for i in range(2)]
    for thread in threads:
        thread.start()

    await async_writer()

    for thread in threads:
        await asyncio.to_thread(thread.join)

    assert not failures

    db.rollback()
    counts = {
        source: AsyncThingInterleaved.where(AsyncThingInterleaved.source == source).count()
        for source in ("thread-0", "thread-1", "loop")
    }
    assert counts == {"thread-0": rounds, "thread-1": rounds, "loop": rounds}


@pytest.mark.asyncio
async def test_an_open_async_transaction_does_not_block_a_sync_thread(dal_psql: TypeDAL):
    """
    The sharpest version of the same claim, and the one the previous attempt actually fails.

    Both sides hold a write transaction open at the same time: an OS thread inserting without
    committing, and a session doing the same on the loop. Under the design this replaces - a second,
    async-driver connection inside one `TypeDAL`, with a runtime guard between the two - this raises
    in *both* directions: the sync thread is refused because the async side has pending writes,
    though it has a connection and a transaction of its own and could not care less, and both
    writers lose rows. Here neither notices the other.

    Postgres only: sqlite has a single writer, so two open write transactions block each other by
    definition, and that is sqlite's answer rather than the engine's.
    """
    db = dal_psql

    @db.define()
    class AsyncThingLongSession(TypedTable):
        source: TypedField[str]
        n: TypedField[int]

    db.commit()

    rounds = 10
    failures: list[BaseException] = []

    def sync_writer() -> None:
        try:
            # deliberately not committing per row: this transaction stays open for the whole run
            for i in range(rounds):
                AsyncThingLongSession.insert(source="thread", n=i)
                time.sleep(0.005)
            db.commit()
        except BaseException as e:  # pragma: no cover - only reached when the test fails
            failures.append(e)
        finally:
            db._adapter.close(action="commit", really=False)

    async def session_writer() -> None:
        async with db.session():
            for i in range(rounds):
                await AsyncThingLongSession.insert_async(source="session", n=i)
                await asyncio.sleep(0.005)

    thread = threading.Thread(target=sync_writer)
    thread.start()
    await session_writer()
    await asyncio.to_thread(thread.join)

    assert not failures

    db.rollback()
    counts = {
        source: AsyncThingLongSession.where(AsyncThingLongSession.source == source).count()
        for source in ("thread", "session")
    }
    assert counts == {"thread": rounds, "session": rounds}


########################
# settlement failures  #
########################


@pytest.mark.asyncio
async def test_a_failed_flat_commit_rolls_back_before_the_worker_is_released(db_async: TypeDAL):
    """
    A statement can succeed and the *commit* still fail: SQLITE_BUSY, a deferred constraint,
    a connection that died between the two.

    `run_and_commit` only rolls back when `call()` raises, so a failing `db.commit()` skips the
    rollback entirely and `run_async`'s `finally` hands the worker back to the pool with the
    transaction still open. The next borrower - a different task, doing something unrelated -
    inherits those rows: it can read them, and its own commit will commit them.
    """
    db = db_async

    @db.define()
    class AsyncThingCommitFailure(TypedTable):
        qty: TypedField[int]

    real_commit = db.commit
    attempts = itertools.count()

    def failing_commit() -> None:
        if next(attempts) == 0:
            raise RuntimeError("commit failed")
        real_commit()

    db.commit = failing_commit
    try:
        with pytest.raises(RuntimeError):
            await AsyncThingCommitFailure.insert_async(qty=1)
    finally:
        db.commit = real_commit

    # the insert never committed, so it must not be observable - not by the next borrower of that
    # worker, and not after that borrower commits its own (empty) transaction either.
    assert await AsyncThingCommitFailure.count_async() == 0

    db.rollback()
    assert AsyncThingCommitFailure.count() == 0


def _failing_once_commit(db: TypeDAL) -> t.Callable[[], None]:
    """A `db.commit` that raises the first time and behaves after that."""
    real_commit = db.commit
    attempts = itertools.count()

    def failing_commit() -> None:
        if next(attempts) == 0:
            raise RuntimeError("commit failed")
        real_commit()

    return failing_commit


@pytest.mark.asyncio
async def test_a_failed_session_commit_keeps_the_worker_and_lets_the_caller_roll_back(db_async: TypeDAL):
    """
    The same hole in `AsyncSession._settle`, where it costs more.

    `_settle` clears `self._worker` *before* running the commit, so a commit that raises leaves the
    session without its handle: the worker goes back to the pool mid-transaction, and the caller's
    `await session.rollback()` finds `self._worker is None` and returns silently. They are told the
    commit failed and then have no way at all to settle the transaction they still own.

    An explicit commit inside the block, checked at the moment it fails: that is the only point
    where the invariant is visible, since leaving the block settles the transaction one way or
    another (the test below) and frees the worker for real.
    """
    db = db_async

    @db.define()
    class AsyncThingSessionCommitFailure(TypedTable):
        qty: TypedField[int]

    real_commit = db.commit
    session = db.session()

    db.commit = _failing_once_commit(db)
    try:
        with pytest.raises(RuntimeError):
            async with session:
                await AsyncThingSessionCommitFailure.insert_async(qty=1)
                try:
                    await session.commit()
                except RuntimeError:
                    # the commit did not go through, so the transaction is still open and still
                    # this session's: it keeps its worker, and nobody else may be handed it.
                    assert session._worker is not None
                    assert db._async_workers._idle == []
                    raise
    finally:
        db.commit = real_commit

    assert await AsyncThingSessionCommitFailure.count_async() == 0


@pytest.mark.asyncio
async def test_a_failed_commit_at_scope_exit_still_settles_the_transaction(db_async: TypeDAL):
    """
    The other half: when the *implicit* commit at the end of the block fails.

    There is no caller left to settle it - the block is over and the scope raises - so the session
    has to fall back to a rollback itself. Whatever it does, what it must not do is what it does
    today: release the worker with the transaction still open, and then have `__aexit__`'s own
    rollback quietly do nothing because the handle is already gone.
    """
    db = db_async

    @db.define()
    class AsyncThingExitCommitFailure(TypedTable):
        qty: TypedField[int]

    real_commit = db.commit

    db.commit = _failing_once_commit(db)
    try:
        with pytest.raises(RuntimeError):
            async with db.session():
                await AsyncThingExitCommitFailure.insert_async(qty=1)
    finally:
        db.commit = real_commit

    assert await AsyncThingExitCommitFailure.count_async() == 0

    db.rollback()
    assert AsyncThingExitCommitFailure.count() == 0


###########
# caching #
###########


@pytest.mark.asyncio
async def test_a_cached_collect_does_not_commit_the_session_transaction(db_cached: TypeDAL):
    """
    Caching is a side effect of a read, and it must not end the caller's transaction.

    `_insert_cache_entry` (`src/typedal/caching.py`) finishes with `db.commit()`. Inside a session
    that commit is not the cache's own - it is the caller's, and it commits every write made in
    the block so far. The session then rolls back to nothing, and the writes it promised to undo
    are already durable.
    """
    db = db_cached
    db.define(AsyncThingCached)

    class Boom(Exception):
        pass

    with pytest.raises(Boom):
        async with db.session():
            await AsyncThingCached.insert_async(qty=1)

            rows = await AsyncThingCached.where(AsyncThingCached.qty > 0).cache(ttl=60).collect_async()
            assert len(rows) == 1  # the cached read sees the session's own uncommitted write

            raise Boom

    assert await AsyncThingCached.count_async() == 0


@pytest.mark.asyncio
async def test_a_cached_collect_does_not_commit_a_run_sync_unit(db_cached: TypeDAL):
    """
    Same defect through `run_sync`, which is the documented way to keep a unit of work atomic.

    The callback is one transaction by contract; a `.cache()` anywhere inside it splits that
    transaction in two, and the first half survives a failure of the second.
    """
    db = db_cached
    db.define(AsyncThingCached)

    def unit() -> None:
        AsyncThingCached.insert(qty=1)
        AsyncThingCached.where(AsyncThingCached.qty > 0).cache(ttl=60).collect()
        raise RuntimeError("second half failed")

    with pytest.raises(RuntimeError):
        await db.run_sync(unit)

    assert await AsyncThingCached.count_async() == 0


###################
# worker shutdown #
###################


@pytest.mark.asyncio
async def test_worker_shutdown_surfaces_a_failed_connection_close(db_async: TypeDAL):
    """
    `ConnectionWorker.shutdown` submits `_close_connection` and never looks at the future.

    A connection that fails to close - a rollback the server refuses, a socket already gone -
    therefore disappears without a trace, on the one code path whose entire job is to release
    that connection. Failing to close must be loud; whether that is a raise or a log is the call
    to make, but silence is not one of the options.
    """
    worker = ConnectionWorker(db_async, "typedal-async-shutdown-failure")
    await worker.run(lambda: None)

    def failing_close() -> None:
        raise RuntimeError("close failed")

    worker._close_connection = failing_close  # ty: ignore[invalid-assignment]

    with pytest.raises(RuntimeError):
        worker.shutdown()


@pytest.mark.asyncio
async def test_shutting_down_a_worker_that_never_ran_a_statement_is_safe(db_async: TypeDAL):
    """
    The companion to the test above: a worker created but never used has no connection to close.

    Green today only because `shutdown` swallows everything. It must stay green once failures are
    surfaced - `_close_connection` closing a connection that was never opened is not an error.
    """
    worker = ConnectionWorker(db_async, "typedal-async-unused")
    worker.shutdown()
