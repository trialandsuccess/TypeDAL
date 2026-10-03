"""Instance-local PyDAL adapter and dialect extensions for affected-ID updates (see set_types.UpdateSet)."""

from __future__ import annotations

import types
import typing as t

from pydal.adapters.base import BaseAdapter, SQLAdapter
from pydal.dialects.base import SQLDialect
from pydal.dialects.postgre import PostgreDialect
from pydal.dialects.sqlite import SQLiteDialect
from pydal.helpers._internals import Dispatcher

from .set_types import UpdateResult, ids_query, key_fields, key_value
from .types import Field, Query, Row, Rows, Table

update_dialects = Dispatcher("update returning dialect")


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


def _returning_update(
    adapter: SQLAdapter, table: Table, query: Query | None, fields: list[tuple[Field, t.Any]], returning: list[Field]
) -> list[t.Any]:
    """Run `UPDATE ... RETURNING` and return the raw rows, for dialects with `update_returning_supported`."""
    dialect = t.cast(UpdateDialect, adapter.dialect)
    sql = dialect.update_returning(
        adapter._update(table, query, fields), ", ".join(field._rname for field in returning)
    )
    adapter.execute(sql)
    return t.cast(list[t.Any], adapter.fetchall())


def parse_returned(adapter: SQLAdapter, table: Table, returned: list[t.Any]) -> Rows:
    """Parse raw rows holding every column of `table` (from a RETURNING clause) into PyDAL Rows."""
    columns = list(table)
    colnames = [f"{table._tablename}.{field.name}" for field in columns]
    return t.cast(Rows, adapter.parse(returned, columns, colnames))


def update_returning_row(
    adapter: SQLAdapter, table: Table, query: Query | None, fields: list[tuple[Field, t.Any]]
) -> Row | None:
    """Update and read the row back in one statement; only for dialects with UPDATE ... RETURNING."""
    returned = _returning_update(adapter, table, query, fields, list(table))
    return parse_returned(adapter, table, returned).first() if returned else None


def _restrict_to_ids(table: Table, query: Query | None, ids: list[t.Any]) -> Query:
    """Narrow `query` to exactly the rows in `ids`, keeping whether it bypasses common filters."""
    if query is None:
        return ids_query(table, ids)
    restricted = query & ids_query(table, ids)
    restricted.ignore_common_filters = query.ignore_common_filters
    return restricted


def _update_with_ids(
    adapter: SQLAdapter, table: Table, query: Query | None, fields: list[tuple[Field, t.Any]]
) -> UpdateResult:
    keys = key_fields(table)
    if t.cast(UpdateDialect, adapter.dialect).update_returning_supported:
        try:
            returned = _returning_update(adapter, table, query, fields, keys)
        except Exception as error:
            # like PyDAL's own adapter.update: a table can handle a failing UPDATE itself
            if not hasattr(table, "_on_update_error"):
                raise
            return UpdateResult(t.cast(t.Callable[..., int], table._on_update_error)(table, query, fields, error), [])
        ids = [key_value(keys, row) for row in returned]
        return UpdateResult(len(ids), ids)

    # Without RETURNING: lock the matching rows where the backend can (MySQL), then restrict the
    # UPDATE to exactly those keys, so rows changed in between are neither updated nor reported.
    # (PyDAL's SQLite for_update opens a new transaction and emits invalid FOR UPDATE syntax, so not there.)
    # Primary keys must stay unchanged here: returned IDs and after-hooks use the pre-update keys.
    for_update = bool(adapter.can_select_for_update) and adapter.dbengine not in {"sqlite", "spatialite"}
    rows = adapter.db(query).select(*keys, for_update=for_update)
    ids = [key_value(keys, [row[field] for field in keys]) for row in rows]
    if not ids:
        return UpdateResult(0, [])
    count = adapter.update(table, _restrict_to_ids(table, query, ids), fields)
    return UpdateResult(count, ids if count else [])


def install_update(adapter: BaseAdapter) -> None:
    """Extend this adapter and its dialect with `update_with_ids`, without touching PyDAL's global registrations."""
    if not isinstance(adapter.dialect, SQLDialect):
        return
    extension = update_dialects.get_for(adapter.dialect)
    setattr(adapter.dialect, "update_returning_supported", extension.supported)
    setattr(adapter.dialect, "update_returning", types.MethodType(type(extension).update_returning, adapter.dialect))
    setattr(adapter, "update_with_ids", types.MethodType(_update_with_ids, adapter))
