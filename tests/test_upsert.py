"""Integration tests for table-level upsert against real database connections."""

import datetime as dt
from collections.abc import Iterator
from pathlib import Path
import typing as t
import warnings

import pytest
from pydal import DAL, Field
from pydal.helpers.classes import ExecutionHandler
from testcontainers.community.mysql import MySqlContainer

from src.typedal import (
    AffectedSet,
    TypeDAL,
    TypedField,
    TypedTable,
    UpsertAmbiguityError,
    UpsertHookError,
    UpsertHooksWarning,
    UpsertKeyError,
)
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
    if upsert_db._adapter.dbengine == "postgres":
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
    with pytest.raises(UpsertKeyError) as error:
        UpsertUser.upsert({"email": "old@example.com"}, email="secret@example.com", name="hunter2")
    assert "UpsertUser.update_or_insert({'email': ...}, email=..., name=...)" in str(error.value)
    # values may come from request data and must not leak into logs:
    assert "secret@example.com" not in str(error.value)
    assert "hunter2" not in str(error.value)
    original = UpsertUser.insert(email="old@example.com", name="original")
    changed = UpsertUser.update_or_insert({"email": "old@example.com"}, email="new@example.com", name="fixed")
    assert changed.id == original.id
    assert changed.email == "new@example.com"


@pytest.mark.parametrize(
    "key,values",
    [({}, {}), ({"missing": 1}, {}), ({"email": "a"}, {"missing": 1}), ({"email": None}, {}), ({"id": 1}, {})],
)
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


def test_unmarked_before_hook_warns_without_running() -> None:
    events: list[str] = []

    def unmarked_hook(_row: OpRow) -> None:
        events.append("before_insert")

    UpsertUser.before_insert(unmarked_hook)
    with pytest.warns(UpsertHooksWarning, match="unmarked_hook"):
        UpsertUser.upsert({"email": "a@example.com"}, name="Alice")
    assert events == []


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

    UpsertUser.before_insert(before_insert, upsert="ignore")
    UpsertUser.after_insert(after_insert)
    UpsertUser.before_update(before_update, upsert="ignore")
    UpsertUser.after_update(after_update)
    return events


def test_upsert_insertion_runs_only_insert_hooks(hook_events: list[str]) -> None:
    inserted = UpsertUser.upsert({"email": "a@example.com"}, name="Alice")
    assert inserted.name == "Alice"
    assert hook_events == ["after_insert"]


def test_upsert_update_runs_only_update_hooks(hook_events: list[str]) -> None:
    original = UpsertUser.insert(email="a@example.com", name="Alice")
    hook_events.clear()
    updated = UpsertUser.upsert({"email": "a@example.com"}, name="Bob")
    assert updated.id == original.id
    assert hook_events == ["after_update"]


def test_key_only_existing_row_does_not_run_hooks(hook_events: list[str]) -> None:
    original = UpsertUser.insert(email="a@example.com", name="Alice")
    hook_events.clear()
    existing = UpsertUser.upsert({"email": "a@example.com"})
    assert existing.id == original.id
    assert hook_events == []


def test_key_only_insertion_runs_insert_hooks(hook_events: list[str]) -> None:
    inserted = UpsertUser.upsert({"email": "a@example.com"})
    assert inserted.name == "default"
    assert hook_events == ["after_insert"]


def test_changed_predicate_after_update_receives_affected_ids(upsert_db: RecordingDAL) -> None:
    @upsert_db.define()
    class HookedTable(TypedTable):
        value: str

    affected: list[int] = []

    def after_update(rows: Set, row: OpRow) -> None:
        affected.extend(rows.select().column(HookedTable.id))
        assert row.value == "new"
        assert all(record.value == "new" for record in rows.select())

    HookedTable.after_update(after_update)
    first = HookedTable.insert(value="old")
    second = HookedTable.insert(value="old")
    HookedTable.insert(value="other")
    assert set(HookedTable.where(value="old").update(value="new")) == {first.id, second.id}
    assert set(affected) == {first.id, second.id}


@pytest.mark.parametrize("branch", ["insert", "update"])
def test_error_policy_blocks_before_sql(upsert_db: RecordingDAL, branch: str) -> None:
    def forbidden_hook(*_args: t.Any) -> None:
        pytest.fail("Before-hook must not run during upsert")

    if branch == "insert":
        UpsertUser.before_insert(forbidden_hook, upsert="error")
    else:
        UpsertUser.before_update(forbidden_hook, upsert="error")
    with pytest.raises(UpsertHookError, match=f"UpsertUser.before_{branch} hook 'forbidden_hook'.*update_or_insert"):
        UpsertUser.upsert({"email": "a@example.com"})
    assert upsert_db.statements == []


def test_bound_method_policy_uses_callable_equality() -> None:
    class HookService:
        def before_insert(self, _row: OpRow) -> None:
            pytest.fail("Before-hook must not run during upsert")

    service = HookService()
    UpsertUser.before_insert(service.before_insert, upsert="error")
    UpsertUser.before_insert(service.before_insert, upsert="ignore")
    with warnings.catch_warnings(record=True) as emitted:
        warnings.simplefilter("always", UpsertHooksWarning)
        UpsertUser.upsert({"email": "a@example.com"})
    assert emitted == []


@pytest.mark.asyncio
async def test_async_warning_points_to_calling_file() -> None:
    def unmarked_hook(_row: OpRow) -> None:
        pytest.fail("Before-hook must not run during upsert")

    UpsertUser.before_insert(unmarked_hook)
    with pytest.warns(UpsertHooksWarning) as emitted:
        await UpsertUser.upsert_async({"email": "a@example.com"})
    # pytest.warns records every warning; drivers may add their own when the worker connects.
    hook_warnings = [w for w in emitted if issubclass(w.category, UpsertHooksWarning)]
    assert len(hook_warnings) == 1, [(w.category.__name__, str(w.message), w.filename) for w in emitted]
    assert hook_warnings[0].filename == __file__


def test_ignore_policy_is_silent_and_preserves_normal_hooks(hook_events: list[str]) -> None:
    with warnings.catch_warnings(record=True) as emitted:
        warnings.simplefilter("always", UpsertHooksWarning)
        UpsertUser.upsert({"email": "a@example.com"}, name="Alice")
    assert not any(isinstance(warning.message, UpsertHooksWarning) for warning in emitted)
    assert hook_events == ["after_insert"]
    hook_events.clear()
    UpsertUser.update_or_insert({"email": "a@example.com"}, name="Bob")
    assert hook_events == ["before_update", "after_update"]


@pytest.mark.asyncio
async def test_async_upsert_after_hooks(hook_events: list[str]) -> None:
    inserted = await UpsertUser.upsert_async({"email": "a@example.com"}, name="Alice")
    updated = await UpsertUser.upsert_async({"email": "a@example.com"}, name="Bob")
    assert updated.id == inserted.id
    assert hook_events == ["after_insert", "after_update"]


def test_missing_unique_key_or_ambiguous_python_lookup(upsert_db: RecordingDAL) -> None:
    @upsert_db.define
    class NonUniqueUpsert(TypedTable):
        code: str
        value: str

    upsert_db.commit()
    if upsert_db._adapter.dbengine == "postgres":
        with pytest.raises(UpsertKeyError, match="unique"):
            NonUniqueUpsert.upsert({"code": "same"}, value="new")
    else:
        first = NonUniqueUpsert.upsert({"code": "same"}, value="first")
        updated = NonUniqueUpsert.upsert({"code": "same"}, value="second")
        assert updated.id == first.id
        NonUniqueUpsert.insert(code="same", value="other")
        with pytest.raises(UpsertAmbiguityError, match="multiple rows"):
            NonUniqueUpsert.upsert({"code": "same"}, value="ambiguous")


@pytest.mark.parametrize("path", ["raw", "raw_where", "record", "table", "validated"])
def test_other_update_paths_receive_affected_ids(upsert_db: RecordingDAL, path: str) -> None:
    original = UpsertUser.insert(email="a@example.com", name="old")
    affected: list[int] = []

    def after_update(rows: Set, row: OpRow) -> None:
        affected.extend(rows.select().column(UpsertUser.id))
        assert row.name == "new"
        assert rows.select().first().name == "new"

    UpsertUser.after_update(after_update)
    if path == "raw":
        assert upsert_db(UpsertUser.name == "old").update(name="new") == 1
    elif path == "raw_where":
        assert upsert_db(UpsertUser.id > 0).where(UpsertUser.name == "old").update(name="new") == 1
    elif path == "record":
        original.update_record(name="new")
    elif path == "validated":
        response = upsert_db(UpsertUser.name == "old").validate_and_update(name="new")
        assert response["updated"] == 1
        assert not response["errors"]
    else:
        UpsertUser.update_or_insert({"name": "old"}, name="new")
    assert affected == [original.id]


def test_upsert_updates_only_explicit_values(upsert_db: RecordingDAL) -> None:
    @upsert_db.define
    class ComputedUpsert(TypedTable):
        code = TypedField(str, unique=True)
        name: str
        stamp = TypedField(str, default="insert", update="update")
        computed = TypedField(str, compute=lambda row: row.name.upper())

    inserted = ComputedUpsert.upsert({"code": "same"}, name="alice")
    updated = ComputedUpsert.upsert({"code": "same"}, name="bob")
    assert inserted.computed == updated.computed == "ALICE"
    assert updated.stamp == "insert"
    explicit = ComputedUpsert.upsert({"code": "same"}, name="bob", stamp="explicit", computed="manual")
    assert explicit.stamp == "explicit"
    assert explicit.computed == "manual"


@pytest.mark.parametrize("operation", ["upsert", "update"])
def test_common_filter_after_hooks_see_row_outside_filter(upsert_db: RecordingDAL, operation: str) -> None:
    @upsert_db.define(common_filter=lambda _query: upsert_db.filtered_upsert.active == True)  # noqa: E712
    class FilteredUpsert(TypedTable):
        code = TypedField(str, unique=True)
        active = TypedField(bool, default=True)

    inserted = FilteredUpsert.upsert({"code": "same"})
    affected: list[int] = []

    def after_update(rows: Set, row: OpRow) -> None:
        affected.extend(rows.select().column(FilteredUpsert.id))
        assert row.active is False
        assert rows.select().first().active is False

    FilteredUpsert.after_update(after_update)
    if operation == "upsert":
        updated = FilteredUpsert.upsert({"code": "same"}, active=False)
        assert updated.id == inserted.id
    else:
        assert FilteredUpsert.where(active=True).update(active=False) == [inserted.id]
    assert affected == [inserted.id]
    assert FilteredUpsert.count() == 0


def test_hook_policy_belongs_to_model_registration(upsert_db: RecordingDAL) -> None:
    @upsert_db.define
    class OtherUpsertUser(TypedTable):
        email = TypedField(str, unique=True)

    def shared_hook(_row: OpRow) -> None:
        pytest.fail("Before-hook must not run during upsert")

    UpsertUser.before_insert(shared_hook, upsert="ignore")
    OtherUpsertUser.before_insert(shared_hook, upsert="error")
    UpsertUser.upsert({"email": "a@example.com"})
    with pytest.raises(UpsertHookError):
        OtherUpsertUser.upsert({"email": "a@example.com"})
    OtherUpsertUser.before_insert(shared_hook, upsert="ignore")
    OtherUpsertUser.upsert({"email": "a@example.com"})


def test_conflict_on_another_unique_column_is_integrity_error(upsert_db: RecordingDAL) -> None:
    @upsert_db.define
    class OtherUniqueUpsert(TypedTable):
        email = TypedField(str, unique=True)
        username = TypedField(str, unique=True)

    OtherUniqueUpsert.insert(email="old@example.com", username="taken")
    upsert_db.commit()
    with pytest.raises(upsert_db._adapter.driver.IntegrityError):
        OtherUniqueUpsert.upsert({"email": "new@example.com"}, username="taken")


def test_branch_metadata_does_not_shadow_inserted_field(upsert_db: RecordingDAL) -> None:
    @upsert_db.define
    class BranchUpsert(TypedTable):
        code = TypedField(str, unique=True)
        inserted: bool

    events: list[str] = []
    BranchUpsert.after_insert(lambda _row, _row_id: events.append("insert"))
    BranchUpsert.after_update(lambda _rows, _row: events.append("update"))
    first = BranchUpsert.upsert({"code": "same"}, inserted=False)
    second = BranchUpsert.upsert({"code": "same"}, inserted=True)
    assert first.inserted is False
    assert second.inserted is True
    assert second.id == first.id
    assert events == ["insert", "update"]


def test_invalid_hook_policy_leaves_registration_unchanged() -> None:
    def before_insert(_row: OpRow) -> None:
        pytest.fail("Invalid hook registration must not be installed")

    with pytest.raises(ValueError, match="Invalid upsert hook policy"):
        UpsertUser.before_insert(before_insert, upsert=t.cast(t.Any, "invalid"))
    UpsertUser.insert(email="a@example.com")
    UpsertUser.upsert({"email": "a@example.com"}, name="updated")


def test_update_veto_and_naive_bypass(upsert_db: RecordingDAL) -> None:
    inserted = UpsertUser.insert(email="a@example.com", name="old")
    events: list[str] = []

    def veto(_rows: Set, _row: OpRow) -> bool:
        events.append("before")
        return True

    UpsertUser.before_update(veto, upsert="ignore")
    UpsertUser.after_update(lambda _rows, _row: events.append("after"))
    assert UpsertUser.where(id=inserted.id).update(name="blocked") == []
    assert UpsertUser(inserted.id).name == "old"
    assert events == ["before"]
    events.clear()
    assert upsert_db(UpsertUser.id == inserted.id).update_naive(name="naive") == 1
    assert UpsertUser(inserted.id).name == "naive"
    assert events == []


def test_plain_update_without_after_hooks_uses_only_rowcount(upsert_db: RecordingDAL) -> None:
    original = UpsertUser.insert(email="a@example.com", name="old")
    upsert_db.statements.clear()
    assert upsert_db(UpsertUser.id == original.id).update(name="new") == 1
    assert len(upsert_db.statements) == 1
    assert upsert_db.statements[0].startswith("UPDATE")
    assert "RETURNING" not in upsert_db.statements[0]
    assert UpsertUser(original.id).name == "new"


def test_after_hook_uses_known_ids_without_selecting(upsert_db: RecordingDAL) -> None:
    original = UpsertUser.insert(email="a@example.com", name="old")
    ids: list[int] = []

    def after_update(rows: AffectedSet, row: OpRow) -> None:
        ids.extend(rows.affected_ids)
        assert row.name == "new"

    UpsertUser.after_update(after_update)
    upsert_db.statements.clear()
    assert upsert_db(UpsertUser.id == original.id).update(name="new") == 1
    assert ids == [original.id]
    expected_count = 2 if upsert_db._adapter.dbengine == "mysql" else 1
    assert len(upsert_db.statements) == expected_count


def test_upsert_same_values_still_runs_update_after_hook() -> None:
    original = UpsertUser.insert(email="a@example.com", name="same")
    ids: list[int] = []

    def after_update(rows: AffectedSet, row: OpRow) -> None:
        ids.extend(rows.affected_ids)
        assert row.name == "same"

    UpsertUser.after_update(after_update)
    updated = UpsertUser.upsert({"email": "a@example.com"}, name="same")
    assert updated.id == original.id
    assert ids == [original.id]


@pytest.mark.parametrize("naive", [False, True])
def test_empty_update_raises_before_sql(upsert_db: RecordingDAL, naive: bool) -> None:
    rows = upsert_db(UpsertUser.id > 0)
    with pytest.raises(ValueError, match="No fields to update"):
        if naive:
            rows.update_naive()
        else:
            rows.update()
    assert upsert_db.statements == []


def test_empty_query_builder_update_raises_before_sql(upsert_db: RecordingDAL) -> None:
    with pytest.raises(ValueError, match="No fields to update"):
        UpsertUser.where(UpsertUser.id > 0).update()
    assert upsert_db.statements == []


def test_update_without_matches_has_no_after_hook(upsert_db: RecordingDAL) -> None:
    events: list[str] = []
    UpsertUser.after_update(lambda _rows, _row: events.append("after"))
    rows = upsert_db(UpsertUser.id > 0)
    assert rows.where(None) is rows
    assert rows.update(name="new") == 0
    assert events == []


@pytest.mark.parametrize("upsert_db", ["postgres"], indirect=True)
def test_native_adapter_key_only_conflict_returns_unchanged_row(upsert_db: RecordingDAL) -> None:
    @upsert_db.define
    class NativeMembership(TypedTable):
        user_code: str
        group_code: str
        name = TypedField(str, default="default")

    NativeMembership.create_index("native_membership_unique", "user_code", "group_code", unique=True)
    existing = NativeMembership.insert(user_code="u", group_code="g", name="existing")
    table = NativeMembership._ensure_table_defined()
    operation = table._fields_and_values_for_insert({"user_code": "u", "group_code": "g"})
    result = upsert_db._adapter.upsert(table, [table.user_code, table.group_code], operation.op_values(), [])
    assert result.outcome == "unchanged"
    assert result.row.id == existing.id
    assert result.row.name == "existing"


@pytest.mark.parametrize("upsert_db", ["sqlite", "mysql"], indirect=True)
def test_unsupported_native_dialect_rejects_direct_use(upsert_db: RecordingDAL) -> None:
    table = UpsertUser._ensure_table_defined()
    operation = table._fields_and_values_for_insert({"email": "a@example.com"})
    with pytest.raises(NotImplementedError, match="native upsert"):
        upsert_db._adapter._upsert(table, [table.email], operation.op_values(), [])
    if upsert_db._adapter.dbengine == "mysql":
        with pytest.raises(NotImplementedError, match="UPDATE RETURNING"):
            upsert_db._adapter.dialect.update_returning("UPDATE unused SET value=1;", "id")
    assert upsert_db.statements == []


def test_install_upsert_is_idempotent(upsert_db: RecordingDAL) -> None:
    from src.typedal.upsert import install_upsert

    original = upsert_db._adapter.upsert
    install_upsert(upsert_db._adapter)
    assert upsert_db._adapter.upsert is original
    assert UpsertUser.upsert({"email": "a@example.com"}).name == "default"


def test_extensions_leave_pydal_null_adapter_unchanged(tmp_path: Path) -> None:
    from src.typedal.updates import install_update
    from src.typedal.upsert import install_upsert

    db = DAL(None, folder=str(tmp_path))
    try:
        install_upsert(db._adapter)
        install_update(db._adapter)
        assert not hasattr(db._adapter, "upsert")
        assert not hasattr(db._adapter, "update_with_ids")
    finally:
        db.close()


@pytest.mark.parametrize("branch", ["insert", "update"])
@pytest.mark.parametrize("upsert_db", ["sqlite"], indirect=True)
def test_python_path_reports_trigger_deleted_row(upsert_db: RecordingDAL, branch: str) -> None:
    original = UpsertUser.insert(email="a@example.com", name="old")
    events: list[str] = []
    UpsertUser.after_insert(lambda _row, _row_id: events.append("insert"))
    UpsertUser.after_update(lambda _rows, _row: events.append("update"))
    if branch == "insert":
        upsert_db.executesql(
            'CREATE TRIGGER remove_inserted AFTER INSERT ON "upsert_user" BEGIN '
            'DELETE FROM "upsert_user" WHERE id = NEW.id; END;'
        )
        with pytest.raises(RuntimeError, match="could not be retrieved"):
            UpsertUser.upsert({"email": "other@example.com"}, name="new")
    else:
        upsert_db.executesql(
            'CREATE TRIGGER remove_updated BEFORE UPDATE ON "upsert_user" BEGIN '
            'DELETE FROM "upsert_user" WHERE id = OLD.id; SELECT RAISE(IGNORE); END;'
        )
        with pytest.raises(RuntimeError, match="deleted concurrently"):
            UpsertUser.upsert({"email": original.email}, name="new")
    assert events == []


@pytest.mark.parametrize("upsert_db", ["postgres"], indirect=True)
def test_native_path_reports_suppressed_insert(upsert_db: RecordingDAL) -> None:
    upsert_db.executesql(
        "CREATE FUNCTION suppress_upsert_insert() RETURNS trigger AS $$ BEGIN RETURN NULL; END; $$ LANGUAGE plpgsql;"
    )
    try:
        upsert_db.executesql(
            'CREATE TRIGGER suppress_insert BEFORE INSERT ON "upsert_user" '
            "FOR EACH ROW EXECUTE FUNCTION suppress_upsert_insert();"
        )
        with pytest.raises(RuntimeError, match="could not be retrieved"):
            UpsertUser.upsert({"email": "a@example.com"}, name="new")
    finally:
        upsert_db.executesql('DROP TRIGGER suppress_insert ON "upsert_user";')
        upsert_db.executesql("DROP FUNCTION suppress_upsert_insert();")


def test_upsert_on_plain_table_does_not_warn() -> None:
    # PyDAL's own upload hooks are registered on every table; they must not count as unmarked before-hooks
    with warnings.catch_warnings():
        warnings.simplefilter("error", UpsertHooksWarning)
        UpsertUser.upsert({"email": "a@example.com"}, name="Alice")
        UpsertUser.upsert({"email": "a@example.com"}, name="Bob")


def test_upload_fields_are_stored_and_replaced_files_removed(upsert_db: RecordingDAL, tmp_path: Path) -> None:
    from src.typedal.fields import UploadField

    @upsert_db.define()
    class UpsertDocument(TypedTable):
        code = TypedField(str, unique=True)
        attachment = UploadField(uploadfolder=str(tmp_path), autodelete=True)

    with warnings.catch_warnings():
        warnings.simplefilter("error", UpsertHooksWarning)
        first = UpsertDocument.upsert({"code": "a"}, attachment={"data": b"one", "filename": "one.txt"})
        first_file = tmp_path / first.attachment
        assert first_file.read_bytes() == b"one"

        second = UpsertDocument.upsert({"code": "a"}, attachment={"data": b"two", "filename": "two.txt"})

    assert second.id == first.id
    assert second.attachment != first.attachment
    assert (tmp_path / second.attachment).read_bytes() == b"two"
    # same as a normal update with autodelete: the replaced file is removed
    assert not first_file.exists()


@pytest.mark.parametrize("values", [{"id": 5}, {"name": "x", "id": 5}])
def test_id_is_rejected_as_upsert_value(upsert_db: RecordingDAL, values: dict[str, t.Any]) -> None:
    original = UpsertUser.insert(email="a@example.com", name="Alice")
    upsert_db.statements.clear()
    with pytest.raises(UpsertKeyError, match="must not contain id"):
        UpsertUser.upsert({"email": "a@example.com"}, **values)
    assert upsert_db.statements == []
    assert UpsertUser(original.id).name == "Alice"


@pytest.mark.parametrize("value", [["a@example.com"], {"a": 1}, object()])
def test_non_scalar_key_values_are_rejected(upsert_db: RecordingDAL, value: t.Any) -> None:
    with pytest.raises(UpsertKeyError, match="plain scalars"):
        UpsertUser.upsert({"email": value})
    assert upsert_db.statements == []


@pytest.mark.parametrize(
    "key,values,match",
    [({}, {}, "nonempty"), ({"missing": 1}, {}, "Unknown"), ({"email": "a"}, {"missing": 1}, "Unknown")],
)
def test_invalid_arguments_raise_upsert_key_error(key: dict[str, t.Any], values: dict[str, t.Any], match: str) -> None:
    with pytest.raises(UpsertKeyError, match=match):
        UpsertUser.upsert(key, **values)


def test_decimal_values_are_coerced_not_inlined(upsert_db: RecordingDAL) -> None:
    from decimal import Decimal

    @upsert_db.define()
    class UpsertPrice(TypedTable):
        code = TypedField(str, unique=True)
        amount = TypedField(Decimal, type="decimal(10,2)")

    UpsertPrice.upsert({"code": "keep"}, amount=Decimal("1.00"))
    for payload in ["1); DELETE FROM upsert_price; --", "1, code = 'pwned'", "NaN"]:
        with pytest.raises(ValueError, match="Invalid decimal"):
            UpsertPrice.upsert({"code": "keep"}, amount=payload)
        with pytest.raises(ValueError, match="Invalid decimal"):
            UpsertPrice.insert(code="other", amount=payload)
        with pytest.raises(ValueError, match="Invalid decimal"):
            UpsertPrice.where(UpsertPrice.amount == payload).collect()
    upsert_db.rollback()

    UpsertPrice.upsert({"code": "keep"}, amount=Decimal("1.00"))
    updated = UpsertPrice.upsert({"code": "keep"}, amount=" 2.50 ")
    assert updated.amount == Decimal("2.50")
    assert [row.code for row in UpsertPrice.collect()] == ["keep"]


def test_tenant_filtered_upsert_cannot_overwrite_other_tenant(upsert_db: RecordingDAL) -> None:
    @upsert_db.define()
    class UpsertTenantRow(TypedTable):
        code = TypedField(str, unique=True)
        secret: str
        request_tenant = TypedField(str, default="tenant-a")

    tenant = upsert_db.upsert_tenant_row.request_tenant
    first = UpsertTenantRow.upsert({"code": "shared"}, secret="a-secret")
    upsert_db.commit()

    tenant.default = "tenant-b"
    try:
        upsert_db.statements.clear()
        with pytest.raises(Exception) as error:
            UpsertTenantRow.upsert({"code": "shared"}, secret="b-overwrite")
        # the lookup is tenant-filtered, so the write is an INSERT that hits the unique constraint:
        assert "ON CONFLICT" not in " ".join(upsert_db.statements)
        assert not isinstance(error.value, AssertionError)
        upsert_db.rollback()
    finally:
        tenant.default = "tenant-a"

    row = upsert_db(upsert_db.upsert_tenant_row.id == first.id).select().first()
    assert (row.secret, row.request_tenant) == ("a-secret", "tenant-a")


def test_timestamps_and_slug_mixins_refuse_upsert(upsert_db: RecordingDAL) -> None:
    from src.typedal.mixins import SlugMixin, TimestampsMixin

    @upsert_db.define()
    class UpsertArticle(TypedTable, SlugMixin, TimestampsMixin, slug_field="title"):
        code = TypedField(str, unique=True)
        title: str

    upsert_db.statements.clear()
    with pytest.raises(UpsertHookError, match="before_insert hook .*generate_slug"):
        UpsertArticle.upsert({"code": "a"}, title="Hello")
    assert upsert_db.statements == []

    # update_or_insert runs the hooks, so it keeps working:
    article = UpsertArticle.update_or_insert({"code": "a"}, code="a", title="Hello")
    assert article.slug == "hello"


def test_timestamps_mixin_alone_refuses_upsert(upsert_db: RecordingDAL) -> None:
    from src.typedal.mixins import TimestampsMixin

    @upsert_db.define()
    class UpsertStamped(TypedTable, TimestampsMixin):
        code = TypedField(str, unique=True)

    with pytest.raises(UpsertHookError, match="before_update hook 'set_updated_at'"):
        UpsertStamped.upsert({"code": "a"})


@pytest.mark.parametrize("branch", ["insert", "update"])
def test_once_hooks_accept_upsert_policy(upsert_db: RecordingDAL, branch: str) -> None:
    def once_hook(*_args: t.Any) -> None:
        pytest.fail("Before-hook must not run during upsert")

    register = UpsertUser.before_insert_once if branch == "insert" else UpsertUser.before_update_once
    register(once_hook, upsert="error")
    with pytest.raises(UpsertHookError, match="once_hook"):
        UpsertUser.upsert({"email": "a@example.com"})

    events: list[str] = []
    register(lambda *_args: events.append("once"), upsert="ignore")
    with warnings.catch_warnings():
        warnings.simplefilter("error", UpsertHooksWarning)
        # the 'error' hook is still registered, so remove it first:
        hooks = UpsertUser._before_insert if branch == "insert" else UpsertUser._before_update
        hooks[:] = [hook for hook in hooks if getattr(hook, "__name__", "") != "once_hook"]
        UpsertUser.upsert({"email": "a@example.com"})
    assert events == []


def test_affected_set_can_be_narrowed_inside_hook(upsert_db: RecordingDAL) -> None:
    first = UpsertUser.insert(email="a@example.com", name="old")
    second = UpsertUser.insert(email="b@example.com", name="old")
    seen: dict[str, t.Any] = {}

    def after_update(rows: AffectedSet, _row: OpRow) -> None:
        narrowed = rows.where(UpsertUser.email == "a@example.com")
        called = rows(UpsertUser.email == "b@example.com")
        seen["types"] = (type(narrowed).__name__, type(called).__name__)
        seen["narrowed"] = narrowed.select(UpsertUser.id).column(UpsertUser.id)
        seen["called"] = called.select(UpsertUser.id).column(UpsertUser.id)
        seen["none"] = rows.where(None) is rows

    UpsertUser.after_update(after_update)
    assert upsert_db(UpsertUser.name == "old").update(name="new") == 2
    assert seen == {
        "types": ("UpdateSet", "UpdateSet"),
        "narrowed": [first.id],
        "called": [second.id],
        "none": True,
    }


@pytest.mark.parametrize("primarykey", [["k"], ["k", "n"]])
def test_keyed_tables_support_after_update_hooks(upsert_db: RecordingDAL, primarykey: list[str]) -> None:
    from pydal import Field

    table = upsert_db.define_table(
        f"upsert_keyed_{len(primarykey)}",
        Field("k", "string", length=32),
        Field("n", "integer"),
        Field("v", "integer"),
        primarykey=primarykey,
    )
    table.insert(k="a", n=1, v=0)
    table.insert(k="a", n=2, v=0) if len(primarykey) > 1 else table.insert(k="b", n=2, v=0)
    seen: list[t.Any] = []

    def after_update(rows: AffectedSet, _row: OpRow) -> None:
        seen.extend(rows.affected_ids)
        assert rows.count() == len(rows.affected_ids)

    table._after_update.append(after_update)
    assert upsert_db(table.n == 1).update(v=5) == 1
    expected = "a" if len(primarykey) == 1 else ("a", 1)
    assert seen == [expected]
    assert upsert_db(table.v == 5).count() == 1


@pytest.mark.parametrize("upsert_db", ["sqlite"], indirect=True)
def test_update_without_returning_is_restricted_to_selected_rows(tmp_path: Path) -> None:
    # simulate MySQL / SQLite < 3.35 on a file database, so a second connection can write in between
    uri = f"sqlite://{tmp_path / 'race.sqlite'}"
    db = RecordingDAL(uri, enable_typedal_caching=False, folder=str(tmp_path))
    other = DAL(uri, folder=str(tmp_path))
    db.statements = []

    class RaceRow(TypedTable):
        name: str

    try:
        db.define(RaceRow)
        db.commit()
        other.define_table("race_row", Field("name"), migrate=False)
        db._adapter.dialect.update_returning_supported = False
        first = RaceRow.insert(name="old")
        db.commit()
        ids: list[int] = []
        RaceRow.after_update(lambda rows, _row: ids.extend(rows.affected_ids))

        original_execute = db._adapter.execute

        def execute(sql: str, *args: t.Any, **kwargs: t.Any) -> t.Any:
            if sql.startswith("UPDATE"):
                # a concurrent writer adds a matching row between the SELECT and the UPDATE
                other.race_row.insert(name="old")
                other.commit()
            return original_execute(sql, *args, **kwargs)

        db._adapter.execute = execute
        assert db(RaceRow.name == "old").update(name="new") == 1
        db._adapter.execute = original_execute
        assert ids == [first.id]
        assert RaceRow.where(name="old").count() == 1  # the late row was not touched
    finally:
        db.rollback()
        db.close()
        other.close()


class CachedUpsert(TypedTable):
    """Module level: cached rows are pickled, which needs an importable class."""

    code = TypedField(str, unique=True)
    status: str


@pytest.mark.parametrize("upsert_db", ["sqlite", "postgres", "mysql"], indirect=True)
def test_upsert_invalidates_cache(upsert_db: RecordingDAL, tmp_path: Path) -> None:
    from src.typedal.caching import _TypedalCache, _TypedalCacheDependency

    # the shared fixture disables caching; this one needs it. The cache models are module-global, so
    # remember which database they belong to and hand them back afterwards.
    previous = _TypedalCache._db
    uri = str(upsert_db._uri)
    db = TypeDAL(uri, enable_typedal_caching=True, folder=str(tmp_path / "cached"))
    try:
        db.define(CachedUpsert)
        first = CachedUpsert.upsert({"code": "a"}, status="draft")
        drafts = CachedUpsert.where(status="draft").cache()
        everything = CachedUpsert.where(CachedUpsert.id > 0).cache()
        assert [row.id for row in drafts.collect()] == [first.id]
        assert len(everything.collect()) == 1
        assert drafts.collect().metadata["cache"]["status"] == "cached"
        assert everything.collect().metadata["cache"]["status"] == "cached"

        # update branch that moves the row out of the cached predicate:
        updated = CachedUpsert.upsert({"code": "a"}, status="published")
        assert updated.id == first.id
        refreshed = drafts.collect()
        assert refreshed.metadata["cache"]["status"] == "fresh"
        assert not refreshed

        # insert branch:
        CachedUpsert.upsert({"code": "b"}, status="draft")
        assert everything.collect().metadata["cache"]["status"] == "fresh"
        assert len(everything.collect()) == 2
    finally:
        db.rollback()
        # only our own table: the typedal_cache tables are shared with the rest of the suite on Postgres,
        # and dropping them would wait on other connections' locks
        if "cached_upsert" in db.tables:
            db.cached_upsert.drop()
        db.commit()
        db.close()
        CachedUpsert.unbind()
        if previous is not None and previous._adapter is not None:
            previous.try_define(_TypedalCache)
            previous.try_define(_TypedalCacheDependency)


@pytest.mark.parametrize("upsert_db", ["sqlite"], indirect=True)
def test_paths_without_update_returning(upsert_db: RecordingDAL) -> None:
    # what MySQL and SQLite < 3.35 do, checked on SQLite so it runs without a MySQL container
    upsert_db._adapter.dialect.update_returning_supported = False
    ids: list[int] = []
    UpsertUser.after_update(lambda rows, _row: ids.extend(rows.affected_ids))

    assert upsert_db(UpsertUser.email == "missing@example.com").update(name="x") == 0
    original = UpsertUser.upsert({"email": "a@example.com"}, name="old")
    updated = UpsertUser.upsert({"email": "a@example.com"}, name="new")
    assert (updated.id, updated.name) == (original.id, "new")
    assert ids == [original.id]


def test_upload_without_pydal_autodelete_hook_keeps_old_file(upsert_db: RecordingDAL, tmp_path: Path) -> None:
    from pydal.helpers.methods import delete_uploaded_files

    from src.typedal.fields import UploadField

    @upsert_db.define()
    class UpsertKeepFile(TypedTable):
        code = TypedField(str, unique=True)
        attachment = UploadField(uploadfolder=str(tmp_path), autodelete=True)

    UpsertKeepFile._before_update.remove(delete_uploaded_files)
    first = UpsertKeepFile.upsert({"code": "a"}, attachment={"data": b"one", "filename": "one.txt"})
    UpsertKeepFile.upsert({"code": "a"}, attachment={"data": b"two", "filename": "two.txt"})
    assert (tmp_path / first.attachment).exists()
