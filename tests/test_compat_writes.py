"""
Pins what delete() and bulk_insert() do before performance steps 2 and 3 change how they talk to the database.
"""

import datetime as dt
import itertools
import sqlite3
import typing as t
from pathlib import Path

import psycopg2.errors
import psycopg2.extensions
import pytest
from pydal.helpers.classes import ExecutionHandler

from src.typedal import TypeDAL, TypedField, TypedTable, relationship

from .compat_support import compat_uri, drop_all, is_postgres, make_db

__all__ = ["compat_uri"]

_counter = itertools.count(1)


class WriteTag(TypedTable):
    name: str


class WriteItem(TypedTable):
    name: str
    qty = TypedField(int, default=5)
    serial = TypedField(int, default=lambda: next(_counter))
    upper = TypedField(str, compute=lambda row: row.name.upper())
    touched = TypedField(str, notnull=False, update="updated")
    created = TypedField(dt.datetime, notnull=False, default=dt.datetime(2020, 1, 1))
    tag: WriteTag | None
    tenant: int | None
    tags = relationship(list["WriteTag"], lambda self, other: other.id == self.tag)


class WriteKeyed(TypedTable):
    k: str
    v: int | None


class Recorder(ExecutionHandler):
    """Records every statement that passes through PyDAL's execution handlers."""

    def before_execute(self, command: str) -> None:
        getattr(self.adapter.db, "statements", []).append(command)


class RecordingDAL(TypeDAL):
    execution_handlers = [*TypeDAL.execution_handlers, Recorder]


def seed(db: TypeDAL) -> None:
    db.define(WriteTag)
    db.define(WriteItem)
    WriteTag.insert(name="t1")
    for i in range(10):
        WriteItem.insert(name=f"i{i}", tag=1 if i < 5 else None, tenant=i % 2)
    db.commit()


@pytest.fixture
def wdb(compat_uri: str, tmp_path: Path) -> t.Iterator[TypeDAL]:
    db = make_db(compat_uri, tmp_path, enable_typedal_caching=False)
    try:
        seed(db)
        yield db
    finally:
        WriteItem._permissions = None
        drop_all(db)


@pytest.fixture
def rdb(compat_uri: str, tmp_path: Path) -> t.Iterator[t.Any]:
    db = make_db(compat_uri, tmp_path, cls=RecordingDAL, enable_typedal_caching=True)
    try:
        seed(db)
        db.statements = []
        yield db
    finally:
        WriteItem._permissions = None
        drop_all(db)


def names(db: TypeDAL) -> list[str]:
    return sorted(r.name for r in db(db.write_item).select(db.write_item.name))


# delete


def test_delete_returns_the_deleted_ids(wdb: TypeDAL) -> None:
    ids = WriteItem.where(WriteItem.name.belongs(["i1", "i3", "i8"])).delete()

    assert sorted(ids) == [2, 4, 9]
    assert all(type(i) is int for i in ids)
    assert names(wdb) == ["i0", "i2", "i4", "i5", "i6", "i7", "i9"]


def test_delete_nothing_returns_an_empty_list(wdb: TypeDAL) -> None:
    assert WriteItem.where(name="nope").delete() == []
    assert len(names(wdb)) == 10


def test_delete_everything(wdb: TypeDAL) -> None:
    assert sorted(WriteItem.where(WriteItem.id > 0).delete()) == list(range(1, 11))
    assert names(wdb) == []


def test_delete_runs_hooks_with_the_set(wdb: TypeDAL) -> None:
    log: list[t.Any] = []
    WriteItem.before_delete(lambda s: log.append(("before", sorted(r.name for r in s.select()))))
    WriteItem.after_delete(lambda s: log.append(("after", s.count())))

    WriteItem.where(WriteItem.name.belongs(["i1", "i2"])).delete()

    assert log == [("before", ["i1", "i2"]), ("after", 0)]


def test_before_delete_can_cancel(wdb: TypeDAL) -> None:
    WriteItem.before_delete(lambda s: True)

    assert WriteItem.where(name="i1").delete() == []
    assert len(names(wdb)) == 10


def test_delete_honours_the_common_filter(wdb: TypeDAL) -> None:
    wdb.write_item._common_filter = lambda query: wdb.write_item.tenant == 1
    try:
        ids = WriteItem.where(WriteItem.id > 0).delete()
    finally:
        wdb.write_item._common_filter = None

    assert sorted(ids) == [2, 4, 6, 8, 10]
    assert names(wdb) == ["i0", "i2", "i4", "i6", "i8"]


def test_delete_needs_permission(wdb: TypeDAL) -> None:
    WriteItem._permissions = {"delete": False}
    with pytest.raises(PermissionError):
        WriteItem.where(name="i1").delete()
    assert len(names(wdb)) == 10


def test_delete_with_a_joined_where_lambda_is_refused(wdb: TypeDAL) -> None:
    with pytest.raises(ValueError):
        WriteItem.join("tags").where(lambda item, tags: tags.name == "t1").delete()
    assert len(names(wdb)) == 10


def test_delete_sql_and_rows_delete(wdb: TypeDAL) -> None:
    assert WriteItem.where(name="i1")._delete().upper().startswith("DELETE FROM")
    assert WriteItem.where(WriteItem.tag == 1).collect().delete() is True
    assert names(wdb) == ["i5", "i6", "i7", "i8", "i9"]


def test_delete_selects_ids_through_the_execution_handlers(rdb: t.Any) -> None:
    WriteItem.where(WriteItem.id > 0).cache().collect()  # a cache entry that depends on every row
    rdb.statements.clear()
    WriteItem.where(name="i1").delete()
    builder = list(rdb.statements)

    WriteItem.where(WriteItem.id > 0).cache().collect()
    rdb.statements.clear()
    rdb(rdb.write_item.name == "i2").delete()
    raw = list(rdb.statements)

    def id_selects(statements: list[str]) -> int:
        # TypeDAL's own select("id"); SQLite's delete adds a `SELECT "write_item"."id"` of its own
        return sum(s.startswith('SELECT id FROM "write_item" ') for s in statements)

    # QueryBuilder.delete selects the ids, and so does the cache hook (_remove_cache) for both delete paths:
    assert id_selects(builder) == 2
    assert id_selects(raw) == 1
    assert any(s.startswith('DELETE FROM "write_item"') for s in builder)
    assert any(s.startswith('DELETE FROM "write_item"') for s in raw)
    assert names(rdb) == ["i0", "i3", "i4", "i5", "i6", "i7", "i8", "i9"]


def test_delete_record(wdb: TypeDAL) -> None:
    item = WriteItem.where(name="i4").first()
    assert item is not None
    assert item.delete_record() == 1
    assert not item
    assert names(wdb) == ["i0", "i1", "i2", "i3", "i5", "i6", "i7", "i8", "i9"]


# bulk_insert


def test_bulk_insert_returns_the_new_instances(wdb: TypeDAL) -> None:
    rows = WriteItem.bulk_insert([{"name": "a"}, {"name": "b", "qty": 1, "tag": 1}, {"name": "c", "touched": "x"}])

    got = sorted((r.id, r.name, r.qty, r.upper, r.touched, r.created, r.tag) for r in rows)
    assert got == [
        # PyDAL uses `update=` as the insert default when a field has no `default`:
        (11, "a", 5, "A", "updated", dt.datetime(2020, 1, 1), None),
        (12, "b", 1, "B", "updated", dt.datetime(2020, 1, 1), 1),
        (13, "c", 5, "C", "x", dt.datetime(2020, 1, 1), None),
    ]
    assert all(isinstance(r, WriteItem) for r in rows)
    assert len(names(wdb)) == 13


def test_bulk_insert_calls_callable_defaults_per_row(wdb: TypeDAL) -> None:
    rows = WriteItem.bulk_insert([{"name": "a"}, {"name": "b"}, {"name": "c", "serial": 0}])
    serials = [r.serial for r in sorted(rows, key=lambda r: r.id)]

    assert serials[2] == 0
    assert serials[1] == serials[0] + 1


def test_bulk_insert_empty(wdb: TypeDAL) -> None:
    rows = WriteItem.bulk_insert([])
    assert len(rows) == 0
    assert len(names(wdb)) == 10


def test_bulk_insert_runs_hooks_per_row(wdb: TypeDAL) -> None:
    log: list[t.Any] = []
    WriteItem.before_insert(lambda row: log.append(("before", row["name"], row.get("upper"))))
    WriteItem.after_insert(lambda row, rid: log.append(("after1", row["name"], rid)))
    WriteItem.after_insert(lambda row, rid: log.append(("after2", row["name"], int(rid))))

    WriteItem.bulk_insert([{"name": "a"}, {"name": "b"}])

    # every before hook first, then each after hook over all rows in order:
    assert log == [
        ("before", "a", "A"),
        ("before", "b", "B"),
        ("after1", "a", 11),
        ("after1", "b", 12),
        ("after2", "a", 11),
        ("after2", "b", 12),
    ]


@pytest.mark.xfail(
    strict=True, raises=TypeError, reason="pre-existing: pydal returns 0 and Model.bulk_insert calls belongs(0)"
)
def test_bulk_insert_before_hook_cancels_the_whole_batch(wdb: TypeDAL) -> None:
    WriteItem.before_insert(lambda row: row["name"] == "b")

    try:
        rows = WriteItem.bulk_insert([{"name": "a"}, {"name": "b"}, {"name": "c"}])
    finally:
        assert len(names(wdb)) == 10  # nothing was written, also when bulk_insert raises

    assert len(rows) == 0


def test_bulk_insert_rows_with_different_fields(wdb: TypeDAL) -> None:
    items: list[dict[str, t.Any]] = [{"name": "a", "tenant": 3}, {"name": "b", "qty": 9}, {"name": "c", "tag": 1}]
    rows = sorted(WriteItem.bulk_insert(items), key=lambda r: r.id)

    assert [(r.name, r.qty, r.tenant, r.tag) for r in rows] == [
        ("a", 5, 3, None),
        ("b", 9, None, None),
        ("c", 5, None, 1),
    ]


def test_bulk_insert_many_rows(wdb: TypeDAL) -> None:
    rows = WriteItem.bulk_insert([{"name": f"m{i}", "qty": i} for i in range(1234)])

    assert len(rows) == 1234
    assert sorted(r.qty for r in rows) == list(range(1234))
    assert sorted(r.id for r in rows) == list(range(11, 11 + 1234))


def test_bulk_insert_needs_permission(wdb: TypeDAL) -> None:
    WriteItem._permissions = {"insert": False}
    with pytest.raises(PermissionError):
        WriteItem.bulk_insert([{"name": "a"}])
    assert len(names(wdb)) == 10


def test_bulk_insert_failure_under_autocommit(wdb: TypeDAL) -> None:
    if not is_postgres(wdb):
        pytest.skip("psycopg2 autocommit")
    connection = wdb._adapter.connection
    connection.autocommit = True
    try:
        with pytest.raises(psycopg2.errors.NotNullViolation) as exc:
            WriteItem.bulk_insert([{"name": "a"}, {"name": None}, {"name": "c"}])
        assert exc.value.diag.column_name == "name"
    finally:
        connection.autocommit = False

    # rows after the failing one are never written; "a" survives row by row, not with a failing batch (accepted):
    new = set(names(wdb)) - {f"i{i}" for i in range(10)}
    assert new in ({"a"}, set())


def test_bulk_insert_on_a_keyed_model_writes_the_rows(wdb: TypeDAL) -> None:
    wdb.define(WriteKeyed, primarykey=["k"])
    with pytest.raises(TypeError):  # pre-existing: the ids can't be selected back
        WriteKeyed.bulk_insert([{"k": "a", "id": 1, "v": 1}, {"k": "b", "id": 2}])
    assert sorted((r.k, r.v) for r in wdb(wdb.write_keyed).select()) == [("a", 1), ("b", None)]


def test_bulk_insert_failure_leaves_the_transaction_to_the_caller(wdb: TypeDAL) -> None:
    WriteTag.insert(name="uncommitted")  # an earlier write in the same transaction
    with pytest.raises((sqlite3.IntegrityError, psycopg2.errors.NotNullViolation)) as exc:
        WriteItem.bulk_insert([{"name": "a"}, {"name": None}])
    if is_postgres(wdb):
        assert exc.value.diag.column_name == "name"
    else:
        assert "write_item.name" in str(exc.value)

    # bulk_insert neither committed nor rolled back the caller's transaction:
    if is_postgres(wdb):
        status = wdb._adapter.connection.get_transaction_status()
        assert status == psycopg2.extensions.TRANSACTION_STATUS_INERROR
    else:
        assert wdb(wdb.write_tag.name == "uncommitted").count() == 1
    wdb.rollback()

    assert len(names(wdb)) == 10
    assert wdb(wdb.write_tag.name == "uncommitted").count() == 0


def test_bulk_insert_sees_values_set_by_a_trigger(wdb: TypeDAL) -> None:
    if not is_postgres(wdb):
        pytest.skip("trigger syntax is PostgreSQL's")
    wdb.executesql(
        "CREATE FUNCTION write_item_trg() RETURNS trigger AS $$ "
        "BEGIN NEW.touched := 'trigger:' || NEW.name; RETURN NEW; END; $$ LANGUAGE plpgsql;"
    )
    wdb.executesql(
        "CREATE TRIGGER write_item_trg BEFORE INSERT ON write_item FOR EACH ROW EXECUTE FUNCTION write_item_trg();"
    )
    try:
        rows = WriteItem.bulk_insert([{"name": "a"}, {"name": "b"}])
        assert sorted(r.touched for r in rows) == ["trigger:a", "trigger:b"]
    finally:
        wdb.rollback()


def test_insert_sees_changes_made_by_an_after_insert_trigger(wdb: TypeDAL) -> None:
    if not is_postgres(wdb):
        pytest.skip("trigger syntax is PostgreSQL's")
    wdb.executesql(
        "CREATE FUNCTION write_item_after() RETURNS trigger AS $$ "
        "BEGIN UPDATE write_item SET touched = 'after:' || NEW.name WHERE id = NEW.id; RETURN NULL; END; $$ "
        "LANGUAGE plpgsql;"
    )
    wdb.executesql(
        "CREATE TRIGGER write_item_after AFTER INSERT ON write_item FOR EACH ROW EXECUTE FUNCTION write_item_after();"
    )
    try:
        assert WriteItem.insert(name="a").touched == "after:a"
        inst, errors = WriteItem.validate_and_insert(name="b")
        assert errors is None and inst is not None and inst.touched == "after:b"
        rows = WriteItem.bulk_insert([{"name": "c"}, {"name": "d"}])
        assert sorted(r.touched for r in rows) == ["after:c", "after:d"]
    finally:
        wdb.rollback()


def test_bulk_insert_reaches_statement_level_triggers(wdb: TypeDAL) -> None:
    if not is_postgres(wdb):
        pytest.skip("trigger syntax is PostgreSQL's")
    wdb.executesql("CREATE TABLE write_item_log (n integer);")
    wdb.executesql(
        "CREATE FUNCTION write_item_stmt() RETURNS trigger AS $$ "
        "BEGIN INSERT INTO write_item_log SELECT count(*) FROM new_rows; RETURN NULL; END; $$ LANGUAGE plpgsql;"
    )
    wdb.executesql(
        "CREATE TRIGGER write_item_stmt AFTER INSERT ON write_item REFERENCING NEW TABLE AS new_rows "
        "FOR EACH STATEMENT EXECUTE FUNCTION write_item_stmt();"
    )
    try:
        WriteItem.bulk_insert([{"name": f"s{i}"} for i in range(3)])
        # every row reaches the trigger; how many statements carry them is not pinned (accepted for step 3):
        assert wdb.executesql("SELECT sum(n) FROM write_item_log;")[0][0] == 3
    finally:
        wdb.rollback()
