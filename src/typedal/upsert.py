"""Instance-local PyDAL adapter and dialect extensions for upsert."""

from __future__ import annotations

import types
import typing as t

from pydal.adapters.base import BaseAdapter, SQLAdapter
from pydal.dialects.base import SQLDialect
from pydal.dialects.postgre import PostgreDialect
from pydal.dialects.sqlite import SQLiteDialect
from pydal.helpers._internals import Dispatcher

from .types import Field, Row, Table

upsert_dialects = Dispatcher("upsert dialect")


class UpsertDialect(t.Protocol):
    """Additional dialect methods installed on an existing PyDAL dialect."""

    upsert_supported: bool
    upsert_returning: bool

    def insert(self, table: str, fields: str, values: str) -> str: ...

    def upsert(
        self,
        table: str,
        fields: str,
        values: str,
        conflict: str,
        update: str,
        returning: str | None = None,
    ) -> str: ...


class UpsertAdapter(t.Protocol):
    """Typed interface for the adapter's instance-local upsert extension."""

    dialect: UpsertDialect

    def _upsert(
        self,
        table: Table,
        key_fields: list[Field],
        insert_fields: list[tuple[Field, t.Any]],
        update_fields: list[Field],
    ) -> str: ...

    def upsert(
        self,
        table: Table,
        key_fields: list[Field],
        insert_fields: list[tuple[Field, t.Any]],
        update_fields: list[Field],
    ) -> Row: ...


class SQLiteDriver(t.Protocol):
    """SQLite driver capability used when installing the dialect extension."""

    sqlite_version_info: tuple[int, int, int]


@upsert_dialects.register_for(SQLDialect)
class SQLUpsertDialect:
    """Unsupported dialects use update_or_insert instead."""

    native = False
    returning = False

    def __init__(self, dialect: SQLDialect):
        self.adapter = dialect.adapter

    def upsert(
        self,
        table: str,
        fields: str,
        values: str,
        conflict: str,
        update: str,
        returning: str | None = None,
    ) -> str:
        """Render native upsert SQL where the dialect supports it."""
        raise NotImplementedError("This dialect does not support native upsert")


class OnConflictUpsertDialect(SQLUpsertDialect):
    """SQL generation shared by PostgreSQL and SQLite."""

    native = True
    returning = True

    def upsert(
        self,
        table: str,
        fields: str,
        values: str,
        conflict: str,
        update: str,
        returning: str | None = None,
    ) -> str:
        """Render INSERT using the existing dialect's quoting and INSERT syntax."""
        dialect = t.cast(UpsertDialect, self)
        sql = dialect.insert(table, fields, values).rstrip().removesuffix(";")
        action = f"DO UPDATE SET {update}" if update else "DO NOTHING"
        sql += f" ON CONFLICT ({conflict}) {action}"
        if returning:
            sql += f" RETURNING {returning}"
        return sql + ";"


@upsert_dialects.register_for(PostgreDialect)
class PostgreUpsertDialect(OnConflictUpsertDialect):
    """PostgreSQL supports native conflict handling and RETURNING."""


@upsert_dialects.register_for(SQLiteDialect)
class SQLiteUpsertDialect(OnConflictUpsertDialect):
    """SQLite capabilities depend on the connected driver's SQLite version."""

    def __init__(self, dialect: SQLDialect):
        super().__init__(dialect)
        version = t.cast(SQLiteDriver, self.adapter.driver).sqlite_version_info
        self.native = version >= (3, 24, 0)
        self.returning = version >= (3, 35, 0)


def _adapter_upsert_sql(
    adapter: SQLAdapter,
    table: Table,
    key_fields: list[Field],
    insert_fields: list[tuple[Field, t.Any]],
    update_fields: list[Field],
) -> str:
    """Adapter._upsert: expand typed values and delegate SQL syntax to its dialect."""
    columns = ", ".join(field._rname for field, _ in insert_fields)
    values = ", ".join(adapter.expand(value, field.type) for field, value in insert_fields)
    conflict = ", ".join(field._rname for field in key_fields)
    update = ", ".join(f"{field._rname} = EXCLUDED.{field._rname}" for field in update_fields)
    dialect = t.cast(UpsertDialect, adapter.dialect)
    returning = ", ".join(field._rname for field in table) if dialect.upsert_returning else None
    return dialect.upsert(table._rname, columns, values, conflict, update, returning=returning)


def _adapter_upsert(
    adapter: SQLAdapter,
    table: Table,
    key_fields: list[Field],
    insert_fields: list[tuple[Field, t.Any]],
    update_fields: list[Field],
) -> Row:
    """Adapter.upsert: execute on the current connection and decode the resulting row."""
    upsert_adapter = t.cast(UpsertAdapter, adapter)
    adapter.execute(upsert_adapter._upsert(table, key_fields, insert_fields, update_fields))
    record = None
    if upsert_adapter.dialect.upsert_returning:
        fields = list(table)
        colnames = [f"{table._tablename}.{field.name}" for field in fields]
        record = adapter.parse(adapter.fetchall(), fields, colnames).first()
    if record is None:
        key_names = {field.name for field in key_fields}
        # Reuse converted values instead of evaluating filter_in or callable values twice.
        lookup = [(field, value) for field, value in insert_fields if field.name in key_names]
        field, value = lookup[0]
        query = field == value
        for field, value in lookup[1:]:
            query &= field == value
        record = table._db(query, ignore_common_filters=True).select(table.ALL, limitby=(0, 1)).first()
    if record is None:
        raise RuntimeError("The upserted row could not be retrieved; it may have been deleted concurrently")
    return t.cast(Row, record)


def install_upsert(adapter: BaseAdapter) -> None:
    """Extend this adapter and dialect without modifying PyDAL's global registrations."""
    if hasattr(adapter, "upsert") or not isinstance(adapter.dialect, SQLDialect):
        return
    extension = upsert_dialects.get_for(adapter.dialect)
    render_upsert = type(extension).upsert
    # Bind the extension function to the initialized dialect, preserving its INSERT behavior.
    setattr(adapter.dialect, "upsert", types.MethodType(render_upsert, adapter.dialect))
    setattr(adapter.dialect, "upsert_supported", extension.native)
    setattr(adapter.dialect, "upsert_returning", extension.returning)
    setattr(adapter, "_upsert", types.MethodType(_adapter_upsert_sql, adapter))
    setattr(adapter, "upsert", types.MethodType(_adapter_upsert, adapter))
