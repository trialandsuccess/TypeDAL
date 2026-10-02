"""
Integration tests for table-level upsert against real database connections.

Tests that exercise backend-specific SQL (native ON CONFLICT on Postgres, the select-then-write path on SQLite and
MySQL, UPDATE ... RETURNING or not) use `upsert_db`, which runs them on every backend. Everything decided in Python
before any SQL runs (validation, hook policies, warnings) uses `sqlite_db`.
"""

import datetime as dt
import typing as t
import warnings
from collections.abc import Iterator
from pathlib import Path

import pytest
from pydal import DAL, Field
from pydal.helpers.classes import ExecutionHandler
from pydal.helpers.methods import delete_uploaded_files
from testcontainers.community.mysql import MySqlContainer

from src.typedal import TypeDAL, TypedField, TypedTable
from src.typedal.exceptions import UpsertAmbiguityError, UpsertHookError, UpsertKeyError
from src.typedal.fields import UploadField
from src.typedal.mixins import SlugMixin, TimestampsMixin
from src.typedal.types import AffectedSet, OpRow, Reference, Set
from src.typedal.updates import install_update
from src.typedal.upsert import install_upsert
from src.typedal.warnings import UpsertHooksWarning


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


def _recording_db(request: pytest.FixtureRequest, backend: str, tmp_path: Path) -> Iterator[RecordingDAL]:
    """A database with UpsertUser defined, dropped again afterwards."""
    uri = "sqlite:memory"
    if backend == "postgres":
        uri = request.getfixturevalue("dal_psql_uri")
    elif backend == "mysql":
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


@pytest.fixture(params=["sqlite", "postgres", "mysql"])
def upsert_db(request: pytest.FixtureRequest, tmp_path: Path) -> Iterator[RecordingDAL]:
    yield from _recording_db(request, request.param, tmp_path)


@pytest.fixture
def sqlite_db(request: pytest.FixtureRequest, tmp_path: Path) -> Iterator[RecordingDAL]:
    yield from _recording_db(request, "sqlite", tmp_path)


@pytest.fixture
def hook_events() -> list[str]:
    """Register all four insert and update hooks on UpsertUser (before-hooks with upsert='ignore')."""
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


# --- every backend: the SQL paths ---


def test_insert_update_and_key_only(upsert_db: RecordingDAL) -> None:
    # PyDAL's own upload hooks are registered on every table; they must not count as unmarked before-hooks:
    with warnings.catch_warnings():
        warnings.simplefilter("error", UpsertHooksWarning)
        defaults = UpsertUser.upsert({"email": "defaults@example.com"})
        upsert_db.statements.clear()
        inserted = UpsertUser.upsert({"email": "a@example.com"}, name="Alice 'quoted'")
        updated = UpsertUser.upsert({"email": "a@example.com"}, name="Bob")

    assert (defaults.name, defaults.created_at) == ("default", dt.datetime(2026, 1, 1))
    assert isinstance(inserted, UpsertUser)
    assert isinstance(updated, UpsertUser)
    assert inserted.name == "Alice 'quoted'"
    assert updated.id == inserted.id
    assert updated.name == "Bob"
    assert updated.created_at == dt.datetime(2026, 1, 1)
    if upsert_db._adapter.dbengine == "postgres":
        assert len(upsert_db.statements) == 2
        assert all(sql.startswith("INSERT INTO") and "ON CONFLICT" in sql for sql in upsert_db.statements)

    # a key-only call returns the existing row as-is:
    upsert_db.statements.clear()
    existing = UpsertUser.upsert({"email": "a@example.com"})
    assert (existing.id, existing.name) == (inserted.id, "Bob")
    assert not any(sql.startswith("UPDATE") or "DO UPDATE" in sql for sql in upsert_db.statements)


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


def test_upsert_participates_in_transaction(upsert_db: RecordingDAL) -> None:
    UpsertUser.upsert({"email": "a@example.com"}, name="Alice")
    upsert_db.rollback()
    assert UpsertUser.count() == 0


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


def test_hooks_run_only_for_the_branch_taken(upsert_db: RecordingDAL, hook_events: list[str]) -> None:
    # registered with upsert='ignore', so the before-hooks are skipped silently:
    with warnings.catch_warnings():
        warnings.simplefilter("error", UpsertHooksWarning)
        inserted = UpsertUser.upsert({"email": "a@example.com"})
        assert inserted.name == "default"
        assert hook_events == ["after_insert"]

        hook_events.clear()
        assert UpsertUser.upsert({"email": "a@example.com"}).id == inserted.id
        assert hook_events == []

        assert UpsertUser.upsert({"email": "a@example.com"}, name="Bob").id == inserted.id
        assert hook_events == ["after_update"]

        hook_events.clear()
        UpsertUser.upsert({"email": "b@example.com"}, name="Carol")
        assert hook_events == ["after_insert"]

    # the before-hooks still run for normal writes:
    hook_events.clear()
    UpsertUser.update_or_insert({"email": "a@example.com"}, name="Dave")
    assert hook_events == ["before_update", "after_update"]


@pytest.mark.asyncio
async def test_async_upsert_after_hooks(upsert_db: RecordingDAL, hook_events: list[str]) -> None:
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


@pytest.mark.parametrize("path", ["builder", "raw", "raw_where", "record", "table", "validated"])
def test_update_paths_pass_affected_rows_to_after_hooks(upsert_db: RecordingDAL, path: str) -> None:
    # every update changes the column it filters on, so the original query no longer finds the row afterwards:
    original = UpsertUser.insert(email="a@example.com", name="old")
    UpsertUser.insert(email="b@example.com", name="other")
    affected: list[int] = []

    def after_update(rows: Set, row: OpRow) -> None:
        affected.extend(rows.select().column(UpsertUser.id))
        assert row.name == "new"
        assert rows.select().first().name == "new"

    UpsertUser.after_update(after_update)
    if path == "builder":
        assert UpsertUser.where(name="old").update(name="new") == [original.id]
    elif path == "raw":
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


def test_after_update_hook_gets_the_known_ids(upsert_db: RecordingDAL) -> None:
    original = UpsertUser.insert(email="a@example.com", name="old")
    ids: list[int] = []

    def after_update(rows: AffectedSet, row: OpRow) -> None:
        ids.extend(rows.affected_ids)
        assert row.name == "new"

    UpsertUser.after_update(after_update)

    # nothing updated, no hook:
    no_match = upsert_db(UpsertUser.email == "missing@example.com")
    assert no_match.where(None) is no_match
    assert no_match.update(name="new") == 0
    assert ids == []

    # the ids come from the write itself (MySQL, without RETURNING, selects them first):
    upsert_db.statements.clear()
    assert upsert_db(UpsertUser.id == original.id).update(name="new") == 1
    assert ids == [original.id]
    assert len(upsert_db.statements) == (2 if upsert_db._adapter.dbengine == "mysql" else 1)

    # an upsert that changes nothing is still an update:
    ids.clear()
    assert UpsertUser.upsert({"email": "a@example.com"}, name="new").id == original.id
    assert ids == [original.id]


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
    # Postgres reports the branch as an extra `inserted` column
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


@pytest.mark.parametrize("primarykey", [["k"], ["k", "n"]])
def test_keyed_tables_support_after_update_hooks(upsert_db: RecordingDAL, primarykey: list[str]) -> None:
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


@pytest.mark.parametrize("autodelete_hook", [True, False])
def test_upload_fields_are_stored(upsert_db: RecordingDAL, tmp_path: Path, autodelete_hook: bool) -> None:
    @upsert_db.define()
    class UpsertDocument(TypedTable):
        code = TypedField(str, unique=True)
        attachment = UploadField(uploadfolder=str(tmp_path), autodelete=True)

    if not autodelete_hook:
        UpsertDocument._before_update.remove(delete_uploaded_files)

    with warnings.catch_warnings():
        warnings.simplefilter("error", UpsertHooksWarning)
        first = UpsertDocument.upsert({"code": "a"}, attachment={"data": b"one", "filename": "one.txt"})
        first_file = tmp_path / first.attachment
        assert first_file.read_bytes() == b"one"

        second = UpsertDocument.upsert({"code": "a"}, attachment={"data": b"two", "filename": "two.txt"})

    assert second.id == first.id
    assert second.attachment != first.attachment
    assert (tmp_path / second.attachment).read_bytes() == b"two"
    # like a normal update: PyDAL's autodelete hook removes the replaced file, without it the file stays
    assert first_file.exists() is not autodelete_hook


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


def test_upsert_normalizes_keys_once(upsert_db: RecordingDAL) -> None:
    """Insert, update and key-only lookup share keys converted exactly once per call."""
    conversions: list[str] = []

    def normalize(value: str) -> str:
        conversions.append(value)
        return value.lower()

    @upsert_db.define
    class NormalizedUpsert(TypedTable):
        email = TypedField(str, unique=True, filter_in=normalize)
        name = TypedField(str, default="default")

    first = NormalizedUpsert.upsert({"email": "A@EXAMPLE.COM"}, name="old")
    updated = NormalizedUpsert.upsert({"email": "A@EXAMPLE.COM"}, name="new")
    unchanged = NormalizedUpsert.upsert({"email": "A@EXAMPLE.COM"})
    assert first.email == "a@example.com"
    assert updated.id == unchanged.id == first.id
    assert updated.name == unchanged.name == "new"
    assert NormalizedUpsert.count() == 1
    assert conversions == ["A@EXAMPLE.COM"] * 3


# --- SQLite only: decided in Python, before any SQL ---


@pytest.mark.parametrize(
    "key,values,match",
    [
        ({}, {}, "nonempty"),
        ({"missing": 1}, {}, "Unknown"),
        ({"email": "a@example.com"}, {"missing": 1}, "Unknown"),
        ({"email": None}, {}, "must not be None"),
        ({"id": 1}, {}, "key must not contain id"),
        ({"email": "a@example.com"}, {"id": 5}, "values must not contain id"),
        ({"email": "a@example.com"}, {"name": "x", "id": 5}, "values must not contain id"),
        ({"email": ["a@example.com"]}, {}, "plain scalars"),
        ({"email": {"a": 1}}, {}, "plain scalars"),
        ({"email": object()}, {}, "plain scalars"),
    ],
)
def test_invalid_arguments_are_rejected_before_sql(
    sqlite_db: RecordingDAL, key: dict[str, t.Any], values: dict[str, t.Any], match: str
) -> None:
    original = UpsertUser.insert(email="a@example.com", name="Alice")
    sqlite_db.statements.clear()
    with pytest.raises(UpsertKeyError, match=match):
        UpsertUser.upsert(key, **values)
    assert sqlite_db.statements == []
    assert UpsertUser(original.id).name == "Alice"


def test_overlap_error_contains_concrete_alternative(sqlite_db: RecordingDAL) -> None:
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


def test_key_only_with_required_fields(sqlite_db: RecordingDAL) -> None:
    @sqlite_db.define
    class RequiredUpsert(TypedTable):
        code = TypedField(str, unique=True)
        name = TypedField(str, required=True)

    # inserting needs the required value...
    with pytest.raises(RuntimeError, match="missing required field: name"):
        RequiredUpsert.upsert({"code": "new"})
    assert RequiredUpsert.count() == 0

    # ...returning an existing row doesn't:
    inserted = RequiredUpsert.insert(code="a", name="Alice")
    existing = RequiredUpsert.upsert({"code": "a"})
    assert (existing.id, existing.name) == (inserted.id, "Alice")


def test_upsert_is_table_class_only(sqlite_db: RecordingDAL) -> None:
    row = UpsertUser.upsert({"email": "a@example.com"})
    with pytest.raises(AttributeError):
        UpsertUser.where(id=0).upsert({"email": "a@example.com"})
    with pytest.raises(AttributeError):
        row.upsert({"email": "a@example.com"})


@pytest.mark.parametrize("denied", ["insert", "update"])
def test_upsert_requires_both_write_permissions(sqlite_db: RecordingDAL, denied: str) -> None:
    @sqlite_db.define(permissions={denied: False})
    class RestrictedUpsertUser(TypedTable):
        email = TypedField(str, unique=True)

    sqlite_db.statements.clear()
    with pytest.raises(PermissionError, match=f"{denied} is not allowed"):
        RestrictedUpsertUser.upsert({"email": "a@example.com"})
    assert sqlite_db.statements == []


def test_unmarked_before_hook_warns_without_running(sqlite_db: RecordingDAL) -> None:
    events: list[str] = []

    def unmarked_hook(_row: OpRow) -> None:
        events.append("before_insert")

    UpsertUser.before_insert(unmarked_hook)
    with pytest.warns(UpsertHooksWarning, match="unmarked_hook"):
        UpsertUser.upsert({"email": "a@example.com"}, name="Alice")
    assert events == []


@pytest.mark.asyncio
async def test_async_warning_points_to_calling_file(sqlite_db: RecordingDAL) -> None:
    def unmarked_hook(_row: OpRow) -> None:
        pytest.fail("Before-hook must not run during upsert")

    UpsertUser.before_insert(unmarked_hook)
    with pytest.warns(UpsertHooksWarning) as emitted:
        await UpsertUser.upsert_async({"email": "a@example.com"})
    # pytest.warns records every warning; drivers may add their own when the worker connects.
    hook_warnings = [w for w in emitted if issubclass(w.category, UpsertHooksWarning)]
    assert len(hook_warnings) == 1, [(w.category.__name__, str(w.message), w.filename) for w in emitted]
    assert hook_warnings[0].filename == __file__


@pytest.mark.parametrize("policy", ["error", "ignore"])
@pytest.mark.parametrize("register", ["before_insert", "before_update", "before_insert_once", "before_update_once"])
def test_before_hook_policies(sqlite_db: RecordingDAL, register: str, policy: t.Literal["error", "ignore"]) -> None:
    def policy_hook(*_args: t.Any) -> None:
        pytest.fail("Before-hook must not run during upsert")

    getattr(UpsertUser, register)(policy_hook, upsert=policy)
    if policy == "error":
        branch = "insert" if "insert" in register else "update"
        with pytest.raises(UpsertHookError, match=f"UpsertUser.before_{branch} hook 'policy_hook'.*update_or_insert"):
            UpsertUser.upsert({"email": "a@example.com"})
        assert sqlite_db.statements == []
    else:
        with warnings.catch_warnings():
            warnings.simplefilter("error", UpsertHooksWarning)
            UpsertUser.upsert({"email": "a@example.com"})


def test_bound_method_policy_uses_callable_equality(sqlite_db: RecordingDAL) -> None:
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


def test_hook_policy_belongs_to_model_registration(sqlite_db: RecordingDAL) -> None:
    @sqlite_db.define
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


def test_invalid_hook_policy_leaves_registration_unchanged(sqlite_db: RecordingDAL) -> None:
    def before_insert(_row: OpRow) -> None:
        pytest.fail("Invalid hook registration must not be installed")

    with pytest.raises(ValueError, match="Invalid upsert hook policy"):
        UpsertUser.before_insert(before_insert, upsert=t.cast(t.Any, "invalid"))
    UpsertUser.insert(email="a@example.com")
    UpsertUser.upsert({"email": "a@example.com"}, name="updated")


def test_timestamps_and_slug_mixins_refuse_upsert(sqlite_db: RecordingDAL) -> None:
    @sqlite_db.define()
    class UpsertArticle(TypedTable, SlugMixin, TimestampsMixin, slug_field="title"):
        code = TypedField(str, unique=True)
        title: str

    sqlite_db.statements.clear()
    with pytest.raises(UpsertHookError, match="before_insert hook .*generate_slug"):
        UpsertArticle.upsert({"code": "a"}, title="Hello")
    assert sqlite_db.statements == []

    # update_or_insert runs the hooks, so it keeps working:
    article = UpsertArticle.update_or_insert({"code": "a"}, code="a", title="Hello")
    assert article.slug == "hello"


def test_timestamps_mixin_alone_refuses_upsert(sqlite_db: RecordingDAL) -> None:
    @sqlite_db.define()
    class UpsertStamped(TypedTable, TimestampsMixin):
        code = TypedField(str, unique=True)

    with pytest.raises(UpsertHookError, match="before_update hook 'set_updated_at'"):
        UpsertStamped.upsert({"code": "a"})


@pytest.mark.parametrize("write", ["update", "update_naive", "builder"])
def test_empty_update_raises_before_sql(sqlite_db: RecordingDAL, write: str) -> None:
    rows = sqlite_db(UpsertUser.id > 0)
    with pytest.raises(ValueError, match="No fields to update"):
        if write == "builder":
            UpsertUser.where(UpsertUser.id > 0).update()
        else:
            getattr(rows, write)()
    assert sqlite_db.statements == []


def test_update_veto_and_naive_bypass(sqlite_db: RecordingDAL) -> None:
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
    assert sqlite_db(UpsertUser.id == inserted.id).update_naive(name="naive") == 1
    assert UpsertUser(inserted.id).name == "naive"
    assert events == []


def test_plain_update_without_after_hooks_uses_only_rowcount(sqlite_db: RecordingDAL) -> None:
    original = UpsertUser.insert(email="a@example.com", name="old")
    sqlite_db.statements.clear()
    assert sqlite_db(UpsertUser.id == original.id).update(name="new") == 1
    assert len(sqlite_db.statements) == 1
    assert sqlite_db.statements[0].startswith("UPDATE")
    assert "RETURNING" not in sqlite_db.statements[0]
    assert UpsertUser(original.id).name == "new"


def test_affected_set_can_be_narrowed_inside_hook(sqlite_db: RecordingDAL) -> None:
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
    assert sqlite_db(UpsertUser.name == "old").update(name="new") == 2
    assert seen == {
        "types": ("UpdateSet", "UpdateSet"),
        "narrowed": [first.id],
        "called": [second.id],
        "none": True,
    }


# --- paths that normal use rarely or never reaches on the backends at hand ---


@pytest.mark.parametrize("upsert_db", ["postgres"], indirect=True)
def test_native_adapter_key_only_conflict_returns_unchanged_row(upsert_db: RecordingDAL) -> None:
    # upsert() looks a key-only call up first; called directly, ON CONFLICT DO NOTHING returns no row
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


def test_adapter_extensions(sqlite_db: RecordingDAL, tmp_path: Path) -> None:
    # installed once per adapter:
    original = sqlite_db._adapter.upsert
    install_upsert(sqlite_db._adapter)
    assert sqlite_db._adapter.upsert is original

    # dialects without native upsert refuse direct use:
    table = UpsertUser._ensure_table_defined()
    operation = table._fields_and_values_for_insert({"email": "a@example.com"})
    with pytest.raises(NotImplementedError, match="native upsert"):
        sqlite_db._adapter._upsert(table, [table.email], operation.op_values(), [])
    assert sqlite_db.statements == []

    # PyDAL's null adapter has no SQL dialect to extend:
    null_db = DAL(None, folder=str(tmp_path))
    try:
        install_upsert(null_db._adapter)
        install_update(null_db._adapter)
        assert not hasattr(null_db._adapter, "upsert")
        assert not hasattr(null_db._adapter, "update_with_ids")
    finally:
        null_db.close()


@pytest.mark.parametrize("branch", ["insert", "update"])
def test_python_path_reports_trigger_deleted_row(sqlite_db: RecordingDAL, branch: str) -> None:
    original = UpsertUser.insert(email="a@example.com", name="old")
    events: list[str] = []
    UpsertUser.after_insert(lambda _row, _row_id: events.append("insert"))
    UpsertUser.after_update(lambda _rows, _row: events.append("update"))
    if branch == "insert":
        sqlite_db.executesql(
            'CREATE TRIGGER remove_inserted AFTER INSERT ON "upsert_user" BEGIN '
            'DELETE FROM "upsert_user" WHERE id = NEW.id; END;'
        )
        with pytest.raises(RuntimeError, match="deleted concurrently"):
            UpsertUser.upsert({"email": "other@example.com"}, name="new")
    else:
        sqlite_db.executesql(
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
        with pytest.raises(RuntimeError, match="deleted concurrently"):
            UpsertUser.upsert({"email": "a@example.com"}, name="new")
    finally:
        upsert_db.executesql('DROP TRIGGER suppress_insert ON "upsert_user";')
        upsert_db.executesql("DROP FUNCTION suppress_upsert_insert();")


def test_fallback_update_without_returning(sqlite_db: RecordingDAL) -> None:
    # what MySQL and SQLite < 3.35 do, checked on SQLite for the cases MySQL doesn't reach
    sqlite_db._adapter.dialect.update_returning_supported = False
    ids: list[int] = []
    UpsertUser.after_update(lambda rows, _row: ids.extend(rows.affected_ids))

    assert sqlite_db(UpsertUser.email == "missing@example.com").update(name="x") == 0
    original = UpsertUser.upsert({"email": "a@example.com"}, name="old")
    updated = UpsertUser.upsert({"email": "a@example.com"}, name="new")
    assert (updated.id, updated.name) == (original.id, "new")
    assert ids == [original.id]

    # an unrestricted adapter update reports every updated row's key:
    second = UpsertUser.insert(email="b@example.com")
    table = UpsertUser._ensure_table_defined()
    operation = table._fields_and_values_for_update({"name": "updated"})
    result = sqlite_db._adapter.update_with_ids(table, None, operation.op_values())
    assert result.count == 2
    assert set(result.ids) == {original.id, second.id}

    # restricting the update to the selected ids keeps a common filter bypass:
    @sqlite_db.define(common_filter=lambda _query: sqlite_db.bypass_update.active == True)  # noqa: E712
    class BypassUpdate(TypedTable):
        active: bool
        name: str

    row_id = BypassUpdate._ensure_table_defined().insert(active=False, name="hidden")
    seen: list[str] = []
    BypassUpdate.after_update(lambda rows, _row: seen.extend(rows.select().column("name")))
    rows = sqlite_db(BypassUpdate.id == row_id, ignore_common_filters=True)
    assert rows.update_ids(name="updated") == [row_id]
    assert seen == ["updated"]
    assert rows.select().first().name == "updated"


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


@pytest.mark.parametrize("handle_error", [False, True])
@pytest.mark.parametrize("need_ids", [False, True])
def test_update_returning_preserves_error_callback(sqlite_db: RecordingDAL, handle_error: bool, need_ids: bool) -> None:
    """Constraint failures call the configured handler or propagate without after-hooks."""
    original = UpsertUser.insert(email="a@example.com")
    UpsertUser.insert(email="taken@example.com")
    table = UpsertUser._ensure_table_defined()
    events: list[str] = []
    UpsertUser.after_update(lambda _rows, _row: events.append("after"))
    rows = sqlite_db(UpsertUser.id == original.id)

    def on_error(error_table: t.Any, query: t.Any, fields: t.Any, error: Exception) -> int:
        assert error_table is table
        assert query is rows.query
        assert fields == [(table.email, "taken@example.com")]
        assert isinstance(error, sqlite_db._adapter.driver.IntegrityError)
        events.append("error")
        return 0

    if handle_error:
        table._on_update_error = on_error
    write = rows.update_ids if need_ids else rows.update
    if handle_error:
        assert write(email="taken@example.com") == ([] if need_ids else 0)
        assert events == ["error"]
    else:
        with pytest.raises(sqlite_db._adapter.driver.IntegrityError):
            write(email="taken@example.com")
        assert events == []
    assert UpsertUser(original.id).email == "a@example.com"


class CachedUpsert(TypedTable):
    """Module level: cached rows are pickled, which needs an importable class."""

    code = TypedField(str, unique=True)
    status: str


def test_upsert_invalidates_cache(tmp_path: Path) -> None:
    # the shared fixtures disable caching; this one needs it
    db = TypeDAL("sqlite:memory", enable_typedal_caching=True, folder=str(tmp_path))
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
        db.close()
        CachedUpsert.unbind()
