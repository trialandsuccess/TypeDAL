"""Affected-ID update execution shared by all TypeDAL Set update paths."""

from __future__ import annotations

import functools
import operator
import types
import typing as t
from dataclasses import dataclass

from pydal import DAL
from pydal.adapters.base import BaseAdapter, SQLAdapter
from pydal.dialects.base import SQLDialect
from pydal.dialects.postgre import PostgreDialect
from pydal.dialects.sqlite import SQLiteDialect
from pydal.helpers._internals import Dispatcher

from .types import Field, OpRow, PrimaryKey, Query, Set, Table, UpsertKeyValue

update_dialects = Dispatcher("update returning dialect")


@dataclass
class UpdateResult:
    """Rowcount and IDs, collected only when requested or needed by after-hooks."""

    count: int
    ids: list[PrimaryKey]


class UpdateAdapter(t.Protocol):
    """Typed interface for the adapter's instance-local `update_with_ids` extension."""

    def update_with_ids(self, table: Table, query: Query | None, fields: list[tuple[Field, t.Any]]) -> UpdateResult:
        """Update the matching rows and return the rowcount and their primary keys."""
        ...


class UpdateDialect(t.Protocol):
    """Typed interface for the dialect's instance-local UPDATE ... RETURNING extension."""

    update_returning_supported: bool

    def update_returning(self, sql: str, field: str) -> str:
        """Append a RETURNING clause for `field` to an UPDATE statement."""
        ...


@update_dialects.register_for(SQLDialect)
class SQLUpdateDialect:
    """Dialect without UPDATE ... RETURNING; IDs are selected before updating."""

    supported = False

    def __init__(self, dialect: SQLDialect):
        """Keep the adapter of the dialect this extension is installed on."""
        self.adapter = dialect.adapter

    def update_returning(self, sql: str, field: str) -> str:
        """Not available on this dialect."""
        raise NotImplementedError("This dialect does not support UPDATE RETURNING")


@update_dialects.register_for(PostgreDialect)
class PostgreUpdateDialect(SQLUpdateDialect):
    """PostgreSQL returns the updated rows' keys in the UPDATE itself."""

    supported = True

    def update_returning(self, sql: str, field: str) -> str:
        """Append `RETURNING field` to the UPDATE statement."""
        return sql.rstrip().removesuffix(";") + f" RETURNING {field};"


@update_dialects.register_for(SQLiteDialect)
class SQLiteUpdateDialect(PostgreUpdateDialect):
    """SQLite has the same RETURNING syntax, from version 3.35 on."""

    def __init__(self, dialect: SQLDialect):
        """Enable RETURNING only when the linked SQLite library supports it."""
        super().__init__(dialect)
        self.supported = getattr(self.adapter.driver, "sqlite_version_info", (0,)) >= (3, 35, 0)


def key_fields(table: Table) -> list[Field]:
    """The primary key columns: `id` for normal tables, `primarykey=[...]` for keyed tables."""
    primarykey = getattr(table, "_primarykey", None)
    if primarykey:
        return [table[name] for name in primarykey]
    return [table._id]


def _key_value(fields: list[Field], values: t.Sequence[UpsertKeyValue]) -> PrimaryKey:
    """A scalar for single-column keys (so integer ids stay plain ints), a tuple for composite keys."""
    return values[0] if len(fields) == 1 else tuple(values)


def ids_query(table: Table, ids: list[PrimaryKey]) -> Query:
    """Match exactly the rows identified by `ids` (as produced by `_key_value`)."""
    fields = key_fields(table)
    if len(fields) == 1 or not ids:
        return fields[0].belongs(ids)
    # composite keys (more than one key field) are always tuples, see _key_value
    composite = [t.cast(tuple[UpsertKeyValue, ...], key) for key in ids]
    matches = [functools.reduce(operator.and_, map(operator.eq, fields, key)) for key in composite]
    return t.cast(Query, functools.reduce(operator.or_, matches))


def _update_with_ids(
    adapter: SQLAdapter, table: Table, query: Query | None, fields: list[tuple[Field, t.Any]]
) -> UpdateResult:
    keys = key_fields(table)
    dialect = t.cast(UpdateDialect, adapter.dialect)
    if dialect.update_returning_supported:
        returning = ", ".join(field._rname for field in keys)
        sql = dialect.update_returning(adapter._update(table, query, fields), returning)
        try:
            adapter.execute(sql)
        except Exception as error:
            if hasattr(table, "_on_update_error"):
                on_error = t.cast(t.Callable[..., int], table._on_update_error)
                return UpdateResult(on_error(table, query, fields, error), [])
            raise
        ids = [_key_value(keys, row) for row in adapter.fetchall()]
        return UpdateResult(len(ids), ids)

    # Without RETURNING: lock the matching rows where the backend can (MySQL), then restrict the
    # UPDATE to exactly those keys, so rows changed in between are neither updated nor reported.
    # (PyDAL's SQLite for_update opens a new transaction and emits invalid FOR UPDATE syntax, so not there.)
    # Primary keys must stay unchanged here: returned IDs and after-hooks use the pre-update keys.
    for_update = bool(adapter.can_select_for_update) and adapter.dbengine not in {"sqlite", "spatialite"}
    rows = adapter.db(query).select(*keys, for_update=for_update)
    ids = [_key_value(keys, [row[field] for field in keys]) for row in rows]
    if not ids:
        return UpdateResult(0, [])
    restricted = ids_query(table, ids) if query is None else query & ids_query(table, ids)
    if query is not None:
        restricted.ignore_common_filters = query.ignore_common_filters
    count = adapter.update(table, restricted, fields)
    return UpdateResult(count, ids if count else [])


def install_update(adapter: BaseAdapter) -> None:
    """Extend this adapter and its dialect with `update_with_ids`, without touching PyDAL's global registrations."""
    if not isinstance(adapter.dialect, SQLDialect):
        return
    extension = update_dialects.get_for(adapter.dialect)
    setattr(adapter.dialect, "update_returning_supported", extension.supported)
    setattr(adapter.dialect, "update_returning", types.MethodType(type(extension).update_returning, adapter.dialect))
    setattr(adapter, "update_with_ids", types.MethodType(_update_with_ids, adapter))


def affected_set(table: Table, ids: list[PrimaryKey]) -> AffectedSet:
    """After-hooks must still see rows that no longer match a common filter."""
    return AffectedSet(table._db, ids_query(table, ids), ids)


class UpdateSet(Set):
    """A real PyDAL Set with affected-ID after-update callback semantics."""

    def where(self, query: Query | None, ignore_common_filters: bool = False) -> UpdateSet:
        """Narrow this set; the result is a plain UpdateSet."""
        if query is None:
            return self
        rows = super().where(query, ignore_common_filters=ignore_common_filters)
        # always a plain UpdateSet: a narrowed AffectedSet no longer matches its affected_ids
        return UpdateSet(self.db, rows.query)

    def _write(self, table: Table, row: OpRow, run_callbacks: bool, need_ids: bool = False) -> UpdateResult:
        if run_callbacks and any(hook(self, row) for hook in table._before_update):
            return UpdateResult(0, [])
        if not need_ids and not (run_callbacks and table._after_update):
            count = self.db._adapter.update(table, self.query, row.op_values())
            return UpdateResult(count, [])
        adapter = t.cast(UpdateAdapter, self.db._adapter)
        result = adapter.update_with_ids(table, self.query, row.op_values())
        if result.ids and run_callbacks:
            rows = affected_set(table, result.ids)
            for hook in table._after_update:
                hook(rows, row)
        return result

    def update_ids(self, **fields: t.Any) -> list[PrimaryKey]:
        """Update the matching rows, running hooks, and return the primary keys of the updated rows."""
        table = self.db._adapter.get_table(self.query)
        row = table._fields_and_values_for_update(fields)
        if not row.op_values():
            raise ValueError("No fields to update")
        return self._write(table, row, run_callbacks=True, need_ids=True).ids

    def update(self, **fields: t.Any) -> int:
        """Update the matching rows, running hooks, and return the rowcount."""
        table = self.db._adapter.get_table(self.query)
        row = table._fields_and_values_for_update(fields)
        if not row.op_values():
            raise ValueError("No fields to update")
        return self._write(table, row, run_callbacks=True).count

    def _apply_update(self, table: Table, row: OpRow, run_callbacks: bool) -> int:
        """Route PyDAL's validated updates through the same affected-ID callbacks."""
        return self._write(table, row, run_callbacks=run_callbacks).count

    def update_naive(self, **fields: t.Any) -> int:
        """Update the matching rows without running any hooks and return the rowcount."""
        table = self.db._adapter.get_table(self.query)
        row = table._fields_and_values_for_update(fields)
        if not row.op_values():
            raise ValueError("No fields to update")
        return self._write(table, row, run_callbacks=False).count


class AffectedSet(UpdateSet):
    """An ID-restricted hook Set carrying the IDs already obtained by the write."""

    def __init__(self, db: DAL, query: Query, affected_ids: list[PrimaryKey]):
        """Match the affected rows by key, ignoring common filters (the update may have moved them out)."""
        super().__init__(db, query, ignore_common_filters=True)
        self.affected_ids = affected_ids
