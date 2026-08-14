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
