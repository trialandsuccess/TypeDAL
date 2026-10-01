"""Integration tests for table-level upsert against real database connections."""

import datetime as dt
from collections.abc import Iterator
from pathlib import Path
import typing as t

import pytest
from pydal.helpers.classes import ExecutionHandler
from testcontainers.community.mysql import MySqlContainer

from src.typedal import TypeDAL, TypedField, TypedTable, UpsertFallbackWarning, UpsertHooksWarning
from src.typedal.types import OpRow, Reference, Set


class RecordingHandler(ExecutionHandler):
    """Observe actual statements through PyDAL's execution-handler API."""

    def before_execute(self, command: str) -> None:
        db = t.cast(RecordingDAL, self.adapter.db)
        if hasattr(db, "statements"):
            db.statements.append(command)


class RecordingDAL(TypeDAL):
    """A real DAL connection with statement recording enabled."""

    statements: list[str]
    execution_handlers = [*TypeDAL.execution_handlers, RecordingHandler]


class UpsertUser(TypedTable):
    email = TypedField(str, unique=True, rname="email_address")
    name = TypedField(str, default="default")
    created_at = TypedField(dt.datetime, default=lambda: dt.datetime(2026, 1, 1))


@pytest.fixture(scope="module")
def mysql_uri() -> Iterator[str]:
    with MySqlContainer("mysql:8.4", dialect="pymysql") as container:
        yield "mysql:pymysql://" + container.get_connection_url().split("://", 1)[1]


@pytest.fixture(params=["sqlite", "postgres", "mysql"], autouse=True)
def upsert_db(request: pytest.FixtureRequest, tmp_path: Path) -> Iterator[RecordingDAL]:
    uri = "sqlite:memory"
    if request.param == "postgres":
        uri = request.getfixturevalue("dal_psql_uri")
    elif request.param == "mysql":
        uri = request.getfixturevalue("mysql_uri")
    db = RecordingDAL(uri, enable_typedal_caching=False, folder=str(tmp_path))
    db.statements = []

    db.define(UpsertUser)
    db.commit()
    db.statements.clear()

    try:
        yield db
    finally:
        db.rollback()
        for name in reversed(db.tables):
            db[name].drop()
        db.commit()
        db.close()
        UpsertUser.unbind()


def test_insert_and_update_return_typed_rows(upsert_db: RecordingDAL) -> None:
    inserted = UpsertUser.upsert({"email": "a@example.com"}, name="Alice 'quoted'")
    updated = UpsertUser.upsert({"email": "a@example.com"}, name="Bob")
    assert isinstance(inserted, UpsertUser)
    assert isinstance(updated, UpsertUser)
    assert inserted.name == "Alice 'quoted'"
    assert updated.id == inserted.id
    assert updated.name == "Bob"
    assert updated.created_at == dt.datetime(2026, 1, 1)
    if upsert_db._adapter.dbengine != "mysql":
        assert len(upsert_db.statements) == 2
        assert all(sql.startswith("INSERT INTO") and "ON CONFLICT" in sql for sql in upsert_db.statements)


def test_key_only_preserves_existing_row(upsert_db: RecordingDAL) -> None:
    inserted = UpsertUser.upsert({"email": "a@example.com"}, name="Alice")
    upsert_db.statements.clear()
    existing = UpsertUser.upsert({"email": "a@example.com"})
    assert existing.id == inserted.id
    assert existing.name == "Alice"
    assert not any(sql.startswith("UPDATE") or "DO UPDATE" in sql for sql in upsert_db.statements)


def test_key_only_inserts_with_defaults() -> None:
    inserted = UpsertUser.upsert({"email": "a@example.com"})
    assert inserted.name == "default"
    assert inserted.created_at == dt.datetime(2026, 1, 1)


def test_composite_unique_key(upsert_db: RecordingDAL) -> None:
    @upsert_db.define
    class UpsertMembership(TypedTable):
        user_code: TypedField[str]
        group_code: TypedField[str]
        role: TypedField[str]

    UpsertMembership.create_index("upsert_membership_unique", "user_code", "group_code", unique=True)
    first = UpsertMembership.upsert({"user_code": "u", "group_code": "g"}, role="member")
    updated = UpsertMembership.upsert({"user_code": "u", "group_code": "g"}, role="admin")
    other = UpsertMembership.upsert({"user_code": "u", "group_code": "other"}, role="member")
    assert first.id == updated.id
    assert updated.role == "admin"
    assert other.id != first.id


def test_overlap_error_contains_concrete_alternative() -> None:
    with pytest.raises(ValueError) as error:
        UpsertUser.upsert({"email": "old@example.com"}, email="new@example.com", name="fixed")
    assert (
        "UpsertUser.update_or_insert({'email': 'old@example.com'}, email='new@example.com', name='fixed')"
        in str(error.value)
    )
    original = UpsertUser.insert(email="old@example.com", name="original")
    changed = UpsertUser.update_or_insert({"email": "old@example.com"}, email="new@example.com", name="fixed")
    assert changed.id == original.id
    assert changed.email == "new@example.com"


@pytest.mark.parametrize("key,values", [({}, {}), ({"missing": 1}, {}), ({"email": "a"}, {"missing": 1})])
def test_invalid_arguments_do_not_execute_sql(
    upsert_db: RecordingDAL,
    key: dict[str, t.Any],
    values: dict[str, t.Any],
) -> None:
    with pytest.raises(ValueError):
        UpsertUser.upsert(key, **values)
    assert upsert_db.statements == []


def test_upsert_is_table_class_only() -> None:
    row = UpsertUser.upsert({"email": "a@example.com"})
    with pytest.raises(AttributeError):
        UpsertUser.where(id=0).upsert({"email": "a@example.com"})
    with pytest.raises(AttributeError):
        row.upsert({"email": "a@example.com"})


def test_upsert_participates_in_transaction(upsert_db: RecordingDAL) -> None:
    UpsertUser.upsert({"email": "a@example.com"}, name="Alice")
    upsert_db.rollback()
    assert UpsertUser.count() == 0


def test_native_hook_warning_or_mysql_fallback(upsert_db: RecordingDAL) -> None:
    warning = UpsertFallbackWarning if upsert_db._adapter.dbengine == "mysql" else UpsertHooksWarning
    with pytest.warns(warning):
        UpsertUser.upsert({"email": "a@example.com"}, name="Alice")


@pytest.mark.asyncio
async def test_async_upsert_and_session_rollback(upsert_db: RecordingDAL) -> None:
    with pytest.raises(RuntimeError, match="rollback requested"):
        async with upsert_db.session():
            first = await UpsertUser.upsert_async({"email": "a@example.com"}, name="Alice")
            updated = await UpsertUser.upsert_async({"email": "a@example.com"}, name="Bob")
            existing = await UpsertUser.upsert_async({"email": "a@example.com"})
            assert isinstance(updated, UpsertUser)
            assert first.id == updated.id == existing.id
            assert existing.name == "Bob"
            raise RuntimeError("rollback requested")
    assert await UpsertUser.count_async() == 0


def test_key_only_returns_existing_with_required_fields(upsert_db: RecordingDAL) -> None:
    @upsert_db.define
    class RequiredUpsertUser(TypedTable):
        email = TypedField(str, unique=True)
        name = TypedField(str, required=True)

    inserted = RequiredUpsertUser.insert(email="a@example.com", name="Alice")
    existing = RequiredUpsertUser.upsert({"email": "a@example.com"})
    assert existing.id == inserted.id
    assert existing.name == "Alice"


@pytest.mark.parametrize("denied", ["insert", "update"])
def test_upsert_requires_both_write_permissions(upsert_db: RecordingDAL, denied: str) -> None:
    @upsert_db.define(permissions={denied: False})
    class RestrictedUpsertUser(TypedTable):
        email = TypedField(str, unique=True)

    upsert_db.statements.clear()
    with pytest.raises(PermissionError, match=f"{denied} is not allowed"):
        RestrictedUpsertUser.upsert({"email": "a@example.com"})
    assert upsert_db.statements == []


@pytest.fixture
def hook_events() -> list[str]:
    events: list[str] = []

    def before_insert(row: OpRow) -> None:
        events.append("before_insert")
        assert UpsertUser.where(email=row.email).count() == 0

    def after_insert(row: OpRow, row_id: Reference) -> None:
        events.append("after_insert")
        assert UpsertUser(int(row_id)).name == row.name

    def before_update(rows: Set, row: OpRow) -> None:
        events.append("before_update")
        assert rows.count() == 1
        assert rows.select().first().name != row.name

    def after_update(rows: Set, row: OpRow) -> None:
        events.append("after_update")
        assert rows.select().first().name == row.name

    UpsertUser.before_insert(before_insert)
    UpsertUser.after_insert(after_insert)
    UpsertUser.before_update(before_update)
    UpsertUser.after_update(after_update)
    return events


def test_upsert_insertion_runs_only_insert_hooks(hook_events: list[str]) -> None:
    inserted = UpsertUser.upsert({"email": "a@example.com"}, name="Alice")
    assert inserted.name == "Alice"
    assert hook_events == ["before_insert", "after_insert"]


def test_upsert_update_runs_only_update_hooks(hook_events: list[str]) -> None:
    original = UpsertUser.insert(email="a@example.com", name="Alice")
    hook_events.clear()
    updated = UpsertUser.upsert({"email": "a@example.com"}, name="Bob")
    assert updated.id == original.id
    assert hook_events == ["before_update", "after_update"]


def test_key_only_existing_row_does_not_run_hooks(hook_events: list[str]) -> None:
    original = UpsertUser.insert(email="a@example.com", name="Alice")
    hook_events.clear()
    existing = UpsertUser.upsert({"email": "a@example.com"})
    assert existing.id == original.id
    assert hook_events == []


def test_key_only_insertion_runs_insert_hooks(hook_events: list[str]) -> None:
    inserted = UpsertUser.upsert({"email": "a@example.com"})
    assert inserted.name == "default"
    assert hook_events == ["before_insert", "after_insert"]


def test_after_insert(upsert_db: RecordingDAL):

    @upsert_db.define()
    class HookedTable(TypedTable):
        value: str

    HookedTable.before_update(lambda req_set, oprow: print(req_set, oprow))
    HookedTable.after_update(lambda req_set, oprow: print(req_set, oprow))

    HookedTable.insert(value=1)
    HookedTable.insert(value=1)
    HookedTable.insert(value=2)

    xyz = HookedTable.where(value=1).update(value=3)

    print(xyz)
