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

from .types import Field, OpRow, Query, Set, Table

update_dialects = Dispatcher("update returning dialect")


@dataclass
class UpdateResult:
    """Rowcount and IDs, collected only when requested or needed by after-hooks."""

    count: int
    ids: list[t.Any]


class UpdateAdapter(t.Protocol):
    def update_with_ids(self, table: Table, query: Query | None, fields: list[tuple[Field, t.Any]]) -> UpdateResult: ...


class UpdateDialect(t.Protocol):
    update_returning_supported: bool

    def update_returning(self, sql: str, field: str) -> str: ...


@update_dialects.register_for(SQLDialect)
class SQLUpdateDialect:
    supported = False

    def __init__(self, dialect: SQLDialect):
        self.adapter = dialect.adapter

    def update_returning(self, sql: str, field: str) -> str:
        raise NotImplementedError("This dialect does not support UPDATE RETURNING")


@update_dialects.register_for(PostgreDialect)
class PostgreUpdateDialect(SQLUpdateDialect):
    supported = True

    def update_returning(self, sql: str, field: str) -> str:
        return sql.rstrip().removesuffix(";") + f" RETURNING {field};"


@update_dialects.register_for(SQLiteDialect)
class SQLiteUpdateDialect(PostgreUpdateDialect):
    def __init__(self, dialect: SQLDialect):
        super().__init__(dialect)
        self.supported = getattr(self.adapter.driver, "sqlite_version_info", (0,)) >= (3, 35, 0)


def key_fields(table: Table) -> list[Field]:
    """The primary key columns: `id` for normal tables, `primarykey=[...]` for keyed tables."""
    primarykey = getattr(table, "_primarykey", None)
    if primarykey:
        return [table[name] for name in primarykey]
    return [table._id]


def _key_value(fields: list[Field], values: t.Sequence[t.Any]) -> t.Any:
    """A scalar for single-column keys (so integer ids stay plain ints), a tuple for composite keys."""
    return values[0] if len(fields) == 1 else tuple(values)


def ids_query(table: Table, ids: list[t.Any]) -> Query:
    """Match exactly the rows identified by `ids` (as produced by `_key_value`)."""
    fields = key_fields(table)
    if len(fields) == 1 or not ids:
        return fields[0].belongs(ids)
    matches = [functools.reduce(operator.and_, map(operator.eq, fields, values)) for values in ids]
    return t.cast(Query, functools.reduce(operator.or_, matches))


def _update_with_ids(
    adapter: SQLAdapter, table: Table, query: Query | None, fields: list[tuple[Field, t.Any]]
) -> UpdateResult:
    keys = key_fields(table)
    dialect = t.cast(UpdateDialect, adapter.dialect)
    if dialect.update_returning_supported:
        returning = ", ".join(field._rname for field in keys)
        sql = dialect.update_returning(adapter._update(table, query, fields), returning)
        adapter.execute(sql)
        ids = [_key_value(keys, row) for row in adapter.fetchall()]
        return UpdateResult(len(ids), ids)

    # Without RETURNING: lock the matching rows where the backend can (MySQL), then restrict the
    # UPDATE to exactly those keys, so rows changed in between are neither updated nor reported.
    # (PyDAL's SQLite for_update opens a new transaction and emits invalid FOR UPDATE syntax, so not there.)
    for_update = bool(adapter.can_select_for_update) and adapter.dbengine not in {"sqlite", "spatialite"}
    rows = adapter.db(query).select(*keys, for_update=for_update)
    ids = [_key_value(keys, [row[field] for field in keys]) for row in rows]
    if not ids:
        return UpdateResult(0, [])
    restricted = ids_query(table, ids) if query is None else query & ids_query(table, ids)
    count = adapter.update(table, restricted, fields)
    return UpdateResult(count, ids if count else [])


def install_update(adapter: BaseAdapter) -> None:
    if not isinstance(adapter.dialect, SQLDialect):
        return
    extension = update_dialects.get_for(adapter.dialect)
    setattr(adapter.dialect, "update_returning_supported", extension.supported)
    setattr(adapter.dialect, "update_returning", types.MethodType(type(extension).update_returning, adapter.dialect))
    setattr(adapter, "update_with_ids", types.MethodType(_update_with_ids, adapter))


def affected_set(table: Table, ids: list[t.Any]) -> AffectedSet:
    """After-hooks must still see rows that no longer match a common filter."""
    return AffectedSet(table._db, ids_query(table, ids), ids)


class UpdateSet(Set):
    """A real PyDAL Set with affected-ID after-update callback semantics."""

    def where(self, query: Query | None, ignore_common_filters: bool = False) -> UpdateSet:
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

    def update_ids(self, **fields: t.Any) -> list[t.Any]:
        table = self.db._adapter.get_table(self.query)
        row = table._fields_and_values_for_update(fields)
        if not row.op_values():
            raise ValueError("No fields to update")
        return self._write(table, row, run_callbacks=True, need_ids=True).ids

    def update(self, **fields: t.Any) -> int:
        table = self.db._adapter.get_table(self.query)
        row = table._fields_and_values_for_update(fields)
        if not row.op_values():
            raise ValueError("No fields to update")
        return self._write(table, row, run_callbacks=True).count

    def _apply_update(self, table: Table, row: OpRow, run_callbacks: bool) -> int:
        """Route PyDAL's validated updates through the same affected-ID callbacks."""
        return self._write(table, row, run_callbacks=run_callbacks).count

    def update_naive(self, **fields: t.Any) -> int:
        table = self.db._adapter.get_table(self.query)
        row = table._fields_and_values_for_update(fields)
        if not row.op_values():
            raise ValueError("No fields to update")
        return self._write(table, row, run_callbacks=False).count


class AffectedSet(UpdateSet):
    """An ID-restricted hook Set carrying the IDs already obtained by the write."""

    def __init__(self, db: DAL, query: Query, affected_ids: list[t.Any]):
        super().__init__(db, query, ignore_common_filters=True)
        self.affected_ids = affected_ids
