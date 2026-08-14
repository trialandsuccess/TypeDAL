"""
Test-first spec for the async execution path (see docs/rfc-async-execution.md).

`collect_async()` does not exist yet — these tests are RED by design: they pin down the
expected shape (`await Sometable.where(...).collect_async()` returns a `TypedRows`, field-for-
field equal to the synchronous `.collect()`) before the implementation exists, per the RFC's
constraint that the public API doesn't change and this is the only new surface being added.

Covers the two concrete divergence points the feasibility spike found (see RFC):
  - jsonb -> dict (pydal's Postgres parser expects the driver to have already decoded it)
  - decimal(10,2) -> Decimal
and the actual point of the exercise: the event loop is not blocked while the query runs.
"""
import asyncio
import time
from decimal import Decimal

import pytest

from src.typedal import TypeDAL, TypedField, TypedTable
from src.typedal.fields import DecimalField, JSONField


@pytest.mark.asyncio
async def test_collect_async_matches_sync_collect(dal_psql: TypeDAL):
    """The core parity claim: async-executed rows must equal sync-executed rows, field for field."""
    db = dal_psql

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
async def test_collect_async_preserves_types(dal_psql: TypeDAL):
    """The two divergence points the spike actually found: decimal and jsonb."""
    db = dal_psql

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

    assert isinstance(row.meta, dict)  # not a raw jsonb string
    assert row.meta == {"a": 1, "b": [1, 2, 3]}


@pytest.mark.asyncio
async def test_collect_async_does_not_block_event_loop(dal_psql: TypeDAL):
    """
    The actual point of building this: a query in flight must not stall other coroutines.
    A ticker sleeping every 5ms should keep ticking at ~5ms while queries run concurrently;
    a blocking implementation would show gaps close to the total query time instead.
    """
    db = dal_psql

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
