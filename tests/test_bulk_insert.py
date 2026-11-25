"""Model.bulk_insert sends batches of rows per INSERT where that gives the same rows as pydal's row-by-row loop."""

import datetime as dt
import sqlite3
import decimal
import typing as t
from pathlib import Path

import pytest
from pydal.adapters import SQLite, adapters
from pydal import Field, SQLCustomType
from pydal.helpers.classes import ExecutionHandler, TimingHandler
from pydal.objects import Table as PydalTable

from src.typedal import TypeDAL, TypedField, TypedTable
from src.typedal.bulk_insert import BATCH_SIZE, bulk_insert_ids
from src.typedal.caching import cache_models

from .compat_support import compat_uri, drop_all, is_postgres, make_db, norm

__all__ = ["compat_uri"]


class BulkOwner(TypedTable):
    name: str


class BulkThing(TypedTable):
    s: str | None
    i: int | None
    f: float | None
    b: bool | None
    dec: decimal.Decimal | None
    d: dt.date | None
    tm: dt.time | None
    dtm: dt.datetime | None
    j: object | None
    ls: list[str] | None
    li: list[int] | None
    bl: bytes | None
    owner: BulkOwner | None
    upper = TypedField(str, notnull=False, compute=lambda row: (row.get("s") or "").upper())
    stamp = TypedField(str, notnull=False, default="default")


class BulkBare(TypedTable):
    s: str | None


@pytest.fixture(autouse=True)
def recording(monkeypatch: pytest.MonkeyPatch) -> None:
    """Record statements on `db.statements` through TypeDAL's own timing handler, so no extra handler is added."""
    original = TimingHandler.after_execute

    def after_execute(self: t.Any, command: str) -> None:
        getattr(self.adapter.db, "statements", []).append(command)
        original(self, command)

    monkeypatch.setattr(TimingHandler, "after_execute", after_execute)


@pytest.fixture
def bdb(compat_uri: str, tmp_path: Path) -> t.Iterator[t.Any]:
    db = make_db(compat_uri, tmp_path, enable_typedal_caching=True)
    try:
        db.define(BulkOwner)
        db.define(BulkThing)
        db.define(BulkBare)
        BulkOwner.insert(name="o")
        db.commit()
        db.statements = []
        yield db
    finally:
        drop_all(db)


ITEMS: list[dict[str, t.Any]] = [
    dict(
        s='it\'s "quoted" \\ back; -- not a comment',
        i=-(2**31),
        f=1.5e-300,
        b=True,
        dec=decimal.Decimal("12345678.90"),
        d=dt.date(1999, 12, 31),
        tm=dt.time(23, 59, 59),
        dtm=dt.datetime(2020, 2, 29, 12, 0, 0, 123456),
        j={"a": [1, None, "ü"], "b": {"c": True}},
        ls=["x", "y|z", ""],
        li=[1, -2, 3],
        bl=b"\x00\xff'\"",
        owner=1,
        stamp="given",
    ),
    dict(s="ü€😀", i=0, f=0.0, b=False, dec=decimal.Decimal("0"), j=[], ls=[], li=[]),
    dict(s=None, i=None, f=None, b=None, j=None, owner=None),
    dict(s=""),
]


def inserts(db: t.Any) -> list[str]:
    return [s for s in db.statements if s.startswith('INSERT INTO "bulk_thing"')]


def test_rows_match_pydals_row_by_row_insert(bdb: t.Any) -> None:
    items = ITEMS + [ITEMS[0]] * 3  # each item fills other columns; the last three share one statement
    fast = BulkThing.bulk_insert(items)
    assert len(inserts(bdb)) == len(ITEMS) + 1

    bdb.statements.clear()
    slow_ids = bdb.bulk_thing.bulk_insert(items)  # pydal's own loop
    assert len(inserts(bdb)) == len(items)

    def values(rows: t.Iterable[t.Any]) -> list[t.Any]:
        return [norm({k: v for k, v in r.as_dict().items() if k != "id"}) for r in sorted(rows, key=lambda r: r.id)]

    slow = bdb(bdb.bulk_thing.id.belongs(slow_ids)).select()
    assert values(fast) == values(slow)
    assert len(fast) == len(items)


def test_batches_of_batch_size_and_one_cache_probe(bdb: t.Any) -> None:
    rows = BulkThing.bulk_insert([{"s": f"r{i}", "i": i} for i in range(2 * BATCH_SIZE + 234)])

    assert len(rows) == 2 * BATCH_SIZE + 234
    assert sorted(r.i for r in rows) == list(range(2 * BATCH_SIZE + 234))
    assert len(inserts(bdb)) == 3
    assert all(" RETURNING " in s for s in inserts(bdb))
    assert len([s for s in bdb.statements if "typedal_cache_dependency" in s]) == 1


def test_different_columns_and_empty_rows_split_batches(bdb: t.Any) -> None:
    items: list[dict[str, t.Any]] = [{"s": "a"}, {"s": "b"}, {"i": 1}, {}, {"s": "c"}, {"s": "d"}]
    rows = sorted(BulkThing.bulk_insert(items), key=lambda r: r.id)

    # (s), (s) | (i) | DEFAULT VALUES... except that `upper` and `stamp` always get values | (s), (s)
    assert [(r.s, r.i, r.upper, r.stamp) for r in rows] == [
        ("a", None, "A", "default"),
        ("b", None, "B", "default"),
        (None, 1, "", "default"),
        (None, None, "", "default"),
        ("c", None, "C", "default"),
        ("d", None, "D", "default"),
    ]
    assert len(inserts(bdb)) == 4


def test_rows_without_values_use_default_values(bdb: t.Any) -> None:
    rows = sorted(BulkBare.bulk_insert([{}, {"s": "a"}, {"s": "b"}, {}]), key=lambda r: r.id)

    assert [r.s for r in rows] == [None, "a", "b", None]
    statements = [s for s in bdb.statements if s.startswith('INSERT INTO "bulk_bare"')]
    assert len(statements) == 3
    assert sum("DEFAULT VALUES" in s for s in statements) == 2


def test_empty_bulk_insert(bdb: t.Any) -> None:
    assert len(BulkThing.bulk_insert([])) == 0
    assert inserts(bdb) == []


def test_a_user_after_insert_hook_keeps_the_row_by_row_loop(bdb: t.Any) -> None:
    seen: list[t.Any] = []
    BulkThing.after_insert(lambda row, rid: seen.append((row["s"], int(rid))))
    try:
        rows = BulkThing.bulk_insert([{"s": "a"}, {"s": "b"}])
    finally:
        bdb.bulk_thing._after_insert.pop()

    assert len(inserts(bdb)) == 2
    assert sorted(seen) == sorted((r.s, r.id) for r in rows)


def test_insert_error_handler_keeps_the_row_by_row_loop(bdb: t.Any) -> None:
    bdb.bulk_thing._on_insert_error = lambda table, fields, error: 0
    try:
        BulkThing.bulk_insert([{"s": "a"}, {"s": "b"}])
    finally:
        del bdb.bulk_thing._on_insert_error
    assert len(inserts(bdb)) == 2


def test_autocommit_keeps_the_row_by_row_loop(bdb: t.Any) -> None:
    if not is_postgres(bdb):
        pytest.skip("psycopg2 autocommit")
    bdb.commit()
    bdb._adapter.connection.autocommit = True
    try:
        BulkThing.bulk_insert([{"s": "a"}, {"s": "b"}])
    finally:
        bdb._adapter.connection.autocommit = False
    assert len(inserts(bdb)) == 2


def test_a_custom_adapter_keeps_the_row_by_row_loop(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(adapters._registry_, "sqlite", type("CustomSQLite", (SQLite,), {}))
    db = make_db(f"sqlite://{tmp_path / 'custom.db'}", tmp_path, enable_typedal_caching=False)
    try:
        db.define(BulkOwner)
        db.define(BulkThing)
        db.statements = []
        BulkThing.bulk_insert([{"s": "a"}, {"s": "b"}])
        assert len(inserts(db)) == 2
    finally:
        db.close()


def test_without_returning_keeps_the_row_by_row_loop(bdb: t.Any, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(bdb._adapter.dialect, "update_returning_supported", False)
    BulkThing.bulk_insert([{"s": "a"}, {"s": "b"}])
    assert len(inserts(bdb)) == 2


def test_after_insert_hooks_added_by_a_before_insert_hook_get_every_row(bdb: t.Any) -> None:
    seen: list[t.Any] = []

    def register(row: t.Any) -> None:
        if not seen:
            seen.append("registered")
            BulkThing.after_insert(lambda row, rid: seen.append((row["s"], int(rid))))

    BulkThing.before_insert(register)
    try:
        rows = BulkThing.bulk_insert([{"s": "a"}, {"s": "b"}])
    finally:
        bdb.bulk_thing._before_insert.pop()
        bdb.bulk_thing._after_insert.pop()

    assert seen[0] == "registered"
    assert sorted(seen[1:]) == sorted((r.s, r.id) for r in rows)


def test_customised_cache_tables_get_one_invalidation_per_row(bdb: t.Any) -> None:
    BulkThing.insert(s="x")
    assert [r.s for r in BulkThing.where(BulkThing.id > 0).cache().collect()] == ["x"]
    cache, _ = cache_models(bdb)
    calls: list[int] = []
    cache._table._before_delete.append(lambda s: calls.append(1) or len(calls) == 1)  # vetoes only the first delete
    try:
        BulkThing.bulk_insert([{"s": "a"}, {"s": "b"}])
    finally:
        cache._table._before_delete.pop()

    assert len(calls) == 2  # one invalidation per row, so the second one clears the entry
    assert sorted(r.s for r in BulkThing.where(BulkThing.id > 0).cache().collect()) == ["a", "b", "x"]


class Registering(ExecutionHandler):
    """Registers an after-insert hook while the first multi-row INSERT runs."""

    def after_execute(self, command: str) -> None:
        db = self.adapter.db
        if command.startswith('INSERT INTO "bulk_thing"') and getattr(db, "late", None) == []:
            db.late.append("registered")
            BulkThing.after_insert(lambda row, rid: db.late.append((row["s"], type(rid).__name__, int(rid))))


class RegisteringDAL(TypeDAL):
    execution_handlers = [*TypeDAL.execution_handlers, Registering]


def test_after_insert_hooks_added_while_inserting_get_every_row(compat_uri: str, tmp_path: Path) -> None:
    db = make_db(compat_uri, tmp_path, cls=RegisteringDAL, enable_typedal_caching=True)
    try:
        db.define(BulkOwner)
        db.define(BulkThing)
        db.late = []
        rows = BulkThing.bulk_insert([{"s": "a"}, {"s": "b"}])
        assert db.late[0] == "registered"
        assert sorted(db.late[1:]) == sorted((r.s, "Reference", r.id) for r in rows)
    finally:
        BulkThing._after_insert[:] = [h for h in BulkThing._after_insert if type(h).__name__ != "function"]
        drop_all(db)


def test_sqlite_legacy_autocommit_keeps_the_row_by_row_loop(tmp_path: Path) -> None:
    db = make_db(
        f"sqlite://{tmp_path / 'auto.db'}",
        tmp_path,
        enable_typedal_caching=False,
        driver_args={"isolation_level": None},
    )
    try:
        db.define(BulkOwner)
        db.define(BulkThing)
        db.statements = []
        BulkThing.bulk_insert([{"s": "a"}, {"s": "b"}])
        assert len(inserts(db)) == 2
    finally:
        db.close()


def test_custom_field_types_keep_the_row_by_row_loop(bdb: t.Any) -> None:
    custom = SQLCustomType(type="string", native="text", encoder=lambda x: f"'{x}'", decoder=lambda x: x)
    bdb.define_table("bulk_custom", Field("c", custom))
    try:
        assert bulk_insert_ids(bdb.bulk_custom, [{"c": "a"}, {"c": "b"}]) is None
    finally:
        bdb.bulk_custom.drop()


class UpperTable(PydalTable):
    def bulk_insert(self, items: list[dict[str, t.Any]]) -> t.Any:
        return super().bulk_insert([{**item, "s": item["s"].upper()} for item in items])


class BulkCustomTable(TypedTable):
    s: str


def test_a_table_class_with_its_own_bulk_insert_is_used(bdb: t.Any) -> None:
    bdb.define(BulkCustomTable, table_class=UpperTable)
    rows = BulkCustomTable.bulk_insert([{"s": "b"}, {"s": "c"}])
    assert sorted(r.s for r in rows) == ["B", "C"]


def test_callable_values_from_a_before_insert_hook_run_per_row(bdb: t.Any) -> None:
    BulkThing.before_insert(lambda row: row.set_value("i", lambda: bdb(bdb.bulk_thing).count(), None))
    try:
        rows = BulkThing.bulk_insert([{"s": "a"}, {"s": "b"}])
    finally:
        bdb.bulk_thing._before_insert.pop()
    assert sorted(r.i for r in rows) == [0, 1]  # each counted the rows inserted before it


def test_autocommit_switched_on_by_a_before_insert_hook(tmp_path: Path) -> None:
    db = make_db(f"sqlite://{tmp_path / 'late-auto.db'}", tmp_path, enable_typedal_caching=False)
    try:
        db.define(BulkOwner)
        db.define(BulkThing)
        db.define(BulkCustomTable)
        db.commit()
        connection = db._adapter.connection
        BulkCustomTable.before_insert(lambda row: setattr(connection, "isolation_level", None))
        with pytest.raises(sqlite3.IntegrityError):
            BulkCustomTable.bulk_insert([{"s": "a"}, {"s": None}])
        connection.isolation_level = ""
        db.rollback()
        assert [r.s for r in db(db.bulk_custom_table).select()] == ["a"]  # committed on its own, as in pydal's loop
    finally:
        db.close()


class Committing(ExecutionHandler):
    """Commits right after every INSERT."""

    def after_execute(self, command: str) -> None:
        if command.startswith("INSERT"):
            self.adapter.connection.commit()


class CommittingDAL(TypeDAL):
    execution_handlers = [*TypeDAL.execution_handlers, Committing]


def test_sqlite_execution_handlers_keep_the_row_by_row_loop(tmp_path: Path) -> None:
    db = make_db(f"sqlite://{tmp_path / 'handler.db'}", tmp_path, cls=CommittingDAL, enable_typedal_caching=False)
    try:
        db.define(BulkOwner)
        db.define(BulkThing)
        db.statements = []
        BulkThing.bulk_insert([{"s": "a"}, {"s": "b"}])
        assert len(inserts(db)) == 2
    finally:
        db.close()


def test_statements_stay_below_the_sqlite_length_limit(tmp_path: Path) -> None:
    db = make_db(f"sqlite://{tmp_path / 'limit.db'}", tmp_path, enable_typedal_caching=False)
    try:
        db.define(BulkOwner)
        db.define(BulkThing)
        db._adapter.connection.setlimit(sqlite3.SQLITE_LIMIT_SQL_LENGTH, 1024)
        db.statements = []
        rows = BulkThing.bulk_insert([{"s": "x" * 120} for _ in range(5)])  # ~270 bytes per row with `upper`
        assert len(rows) == 5
        assert 1 < len(inserts(db)) < 5
        assert all(len(s.encode()) <= 1024 for s in inserts(db))
    finally:
        db.close()
