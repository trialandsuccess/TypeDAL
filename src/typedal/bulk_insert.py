"""Multi-row INSERT statements for Model.bulk_insert, where they do what pydal's row-by-row loop does."""

import sqlite3
import typing as t

from pydal import SQLCustomType
from pydal.adapters.base import SQLAdapter
from pydal.helpers.classes import ExecutionHandler
from pydal.objects import Table as PydalTable

from .caching import AfterInsertCacheHook, _dependency_probe_sql
from .types import Reference, Table

# rows per INSERT statement; keeps statements far below any driver or server limit
BATCH_SIZE = 500
# bytes per INSERT statement where the backend doesn't tell its own limit
MAX_SQL_BYTES = 1 << 20

# adapter methods an insert goes through; an instance-level override of any of them is custom
_INSERT_PATH = ("insert", "bulk_insert", "_insert", "execute", "expand", "fetchall")


def _can_batch(table: Table) -> bool:
    """Whether a multi-row INSERT gives the same result as pydal's Table.bulk_insert for this table."""
    db = table._db
    adapter = db._adapter
    cls = type(adapter)
    return (
        bool(getattr(adapter.dialect, "update_returning_supported", False))  # RETURNING (TypeDAL's update extension)
        and cls.__module__.startswith("pydal.adapters.")
        and cls.insert is SQLAdapter.insert
        and cls.bulk_insert is SQLAdapter.bulk_insert
        and not any(name in vars(adapter) and name != "expand" for name in _INSERT_PATH)
        and type(table).bulk_insert is PydalTable.bulk_insert  # a table_class may do its own thing
        and type(table)._fields_and_values_for_insert is PydalTable._fields_and_values_for_insert
        and not _autocommit(adapter.connection)
        and not (isinstance(adapter.connection, sqlite3.Connection) and _custom_handlers(db))
        and not any(isinstance(field.type, SQLCustomType) for field in table)  # encoders run per row in pydal
        and not hasattr(table, "_on_insert_error")
        and not hasattr(table, "_primarykey")
        and _only_cache_hooks(table)
    )


def _custom_handlers(db: t.Any) -> bool:
    """Whether an extra execution handler acts after execute (on SQLite, RETURNING's statement is still open then)."""
    from .core import TypeDAL

    extra = set(db.execution_handlers) - set(TypeDAL.execution_handlers)
    return any(handler.after_execute is not ExecutionHandler.after_execute for handler in extra)


def _autocommit(connection: t.Any) -> bool:
    """Whether each statement commits on its own, so pydal's loop would keep the rows before a failing one."""
    if isinstance(connection, sqlite3.Connection) and connection.isolation_level is None:
        return True  # sqlite3's legacy autocommit mode
    return getattr(connection, "autocommit", False) is True


def _only_cache_hooks(table: Table) -> bool:
    """Whether TypeDAL's cache invalidation is the only after-insert hook (others get every row with its id)."""
    return all(type(hook) is AfterInsertCacheHook for hook in table._after_insert)


def _max_sql_bytes(connection: t.Any) -> int:
    """The longest statement to send, in bytes: the backend's own limit where it tells, else MAX_SQL_BYTES."""
    if isinstance(connection, sqlite3.Connection):
        return min(MAX_SQL_BYTES, connection.getlimit(sqlite3.SQLITE_LIMIT_SQL_LENGTH))
    return MAX_SQL_BYTES


def _reference(table: Table, row_id: t.Any) -> Reference:
    """The id as pydal's insert returns it."""
    ref = Reference(row_id)
    ref._table, ref._record = table, None
    return ref


def _insert_like_pydal(table: Table, data: list[t.Any], values: list[t.Any]) -> list[t.Any]:
    """The rest of pydal's Table.bulk_insert after its before-insert hooks: insert row by row, then the after hooks."""
    result = table._db._adapter.bulk_insert(table, values)
    for hook in table._after_insert if result else ():
        for row, row_id in zip(data, result, strict=True):
            hook(row, row_id)
    return t.cast(list[t.Any], result)


def bulk_insert_ids(table: Table, items: t.Iterable[dict[str, t.Any]]) -> list[t.Any] | int | None:
    """`table.bulk_insert(items)` with one INSERT per batch of rows; None (nothing done) when it can't do the same."""
    # pydal's order: defaults and computed fields for every row, all before-insert hooks, the inserts, the after hooks
    # a row that fails makes its whole batch fail (accepted: the error leaves the transaction to the caller)
    if not _can_batch(table):
        return None

    adapter = table._db._adapter
    data = [table._fields_and_values_for_insert(item) for item in items]
    if any(hook(row) for row in data for hook in table._before_insert):
        return 0
    values = [row.op_values() for row in data]
    if (
        not _only_cache_hooks(table)
        or _autocommit(adapter.connection)
        or any(callable(value) for row in values for _, value in row)  # pydal calls these right before each insert
    ):
        # a before-insert hook changed what batching relies on: finish exactly as pydal does
        return _insert_like_pydal(table, data, values)

    prefix = f"INSERT INTO {table._rname}"
    suffix = f" RETURNING {table._id._rname};"
    max_bytes = _max_sql_bytes(adapter.connection)
    ids: list[t.Any] = []
    batch: list[str] = []
    batch_columns = ""
    batch_bytes = 0

    def flush() -> None:
        if batch:
            # identifiers are pydal's quoted names and every value went through the adapter's own escaping (expand)
            adapter.execute(f"{prefix}({batch_columns}) VALUES {','.join(batch)}{suffix}")
            ids.extend(row[0] for row in adapter.fetchall())
            batch.clear()

    for row_values in values:
        if not row_values:  # INSERT ... DEFAULT VALUES can't share a statement
            flush()
            ids.append(adapter.insert(table, row_values))
            continue
        columns = ",".join(field._rname for field, _ in row_values)
        rendered = "(" + ",".join(adapter.expand(value, field.type) for field, value in row_values) + ")"
        size = len(rendered.encode()) + 1
        if columns != batch_columns or len(batch) >= BATCH_SIZE or batch_bytes + size > max_bytes:
            flush()
            batch_columns = columns
            batch_bytes = len(f"{prefix}({columns}) VALUES {suffix}".encode())
        batch.append(rendered)
        batch_bytes += size
    flush()

    if not ids:
        return ids
    if not _only_cache_hooks(table):
        # a hook was added while inserting (e.g. by an execution handler): every hook gets every row, as in pydal
        refs = [_reference(table, row_id) for row_id in ids]
        for hook in table._after_insert:
            for row, ref in zip(data, refs, strict=False):  # a skipping trigger leaves fewer ids (see DECISIONS)
                hook(row, ref)
        return ids
    for hook in table._after_insert:
        if _dependency_probe_sql(table._db, t.cast(AfterInsertCacheHook, hook).tablename) is None:
            # customised cache tables (hooks, filters) may treat each invalidation differently: one per row
            for row, row_id in zip(data, ids, strict=False):  # a trigger may have skipped rows
                hook(row, row_id)
        else:
            # the cache hook ignores the row and a repeat finds nothing left: once per call, so one cache probe
            hook(data[0], ids[0])
    return ids
