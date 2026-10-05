"""Instance-local PyDAL adapter and dialect extensions for upsert."""

from __future__ import annotations

import types
import typing as t
import warnings
from dataclasses import dataclass

from pydal.adapters.base import BaseAdapter, SQLAdapter
from pydal.dialects.base import SQLDialect
from pydal.dialects.postgre import PostgreDialect
from pydal.helpers._internals import Dispatcher
from pydal.helpers.methods import attempt_upload, delete_uploaded_files

from .exceptions import UpsertAmbiguityError, UpsertHookError, UpsertKeyError
from .set_types import affected_set, match_fields
from .types import UPSERT_KEY_TYPES, AnyCallable, AnyDict, Field, OpRow, Reference, Row, Table, UpsertHookPolicy
from .updates import parse_returned, update_returning_row
from .warnings import UpsertHooksWarning

upsert_dialects = Dispatcher("upsert dialect")


@dataclass
class HookRegistration:
    """A model-local policy for one before-hook registration."""

    branch: t.Literal["insert", "update"]
    hook: t.Callable[..., t.Any]
    policy: UpsertHookPolicy | None


def register_before_hook(
    hooks: list[t.Callable[..., t.Any]],
    registrations: list[HookRegistration],
    branch: t.Literal["insert", "update"],
    hook: t.Callable[..., t.Any],
    policy: UpsertHookPolicy | None,
) -> None:
    """Register the callable with PyDAL and the policy with its TypeDAL model."""
    if policy is not None and policy not in t.get_args(UpsertHookPolicy):
        raise ValueError(f"Invalid upsert hook policy: {policy!r}")
    if hook not in hooks:
        hooks.append(hook)
    # Replace this hook's policy while preserving the model's registration list.
    registrations[:] = [
        registration
        for registration in registrations
        if not (registration.branch == branch and registration.hook == hook)
    ]
    registrations.append(HookRegistration(branch, hook, policy))


def is_pydal_upload_hook(hook: AnyCallable) -> bool:
    """Recognize the upload before-hooks PyDAL registers on every table; upsert handles uploads itself."""
    if hook is delete_uploaded_files:
        return True
    qualname = getattr(hook, "__qualname__", "")
    return getattr(hook, "__module__", None) == attempt_upload.__module__ and qualname.startswith(
        ("attempt_upload_on_insert.", "attempt_upload_on_update.")
    )


def check_before_hooks(
    before_insert_hooks: list[t.Callable[..., t.Any]],
    before_update_hooks: list[t.Callable[..., t.Any]],
    registrations: list[HookRegistration],
    table_name: str,
) -> None:
    """Enforce policies without running any before-hook."""
    unmarked: list[str] = []
    for branch, hooks in (("insert", before_insert_hooks), ("update", before_update_hooks)):
        for hook in hooks:
            if is_pydal_upload_hook(hook):
                continue
            policy = next(
                (
                    registration.policy
                    for registration in registrations
                    if registration.branch == branch and registration.hook == hook
                ),
                None,
            )
            if policy == "error":
                hook_name = getattr(hook, "__name__", type(hook).__name__)
                raise UpsertHookError(
                    f"{table_name}.before_{branch} hook {hook_name!r} forbids upsert; "
                    "use update_or_insert to run before-hooks"
                )
            if policy is None:
                unmarked.append(f"before_{branch}: {getattr(hook, '__name__', type(hook).__name__)}")
    if unmarked:
        warnings.warn(
            f"{table_name}.upsert skips unmarked before-hooks (" + ", ".join(unmarked) + "). "
            "Register them with upsert='ignore' or upsert='error' to choose an explicit policy.",
            UpsertHooksWarning,
            stacklevel=3,
        )


@dataclass
class UpsertResult:
    """The written row and the operation data needed by PyDAL after-hooks."""

    row: Row
    outcome: t.Literal["inserted", "updated", "unchanged"]
    operation: OpRow


class UpsertDialect(t.Protocol):
    """Additional dialect methods installed on an existing PyDAL dialect."""

    upsert_supported: bool
    upsert_returning: bool

    def insert(self, table: str, fields: str, values: str) -> str:
        """Render PyDAL's own INSERT statement."""
        ...

    def upsert(
        self,
        table: str,
        fields: str,
        values: str,
        conflict: str,
        update: str,
        returning: str | None = None,
    ) -> str:
        """Render the native upsert statement."""
        ...


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
    ) -> UpsertResult:
        """Execute the native upsert and return the written row."""
        ...


@upsert_dialects.register_for(SQLDialect)
class SQLUpsertDialect:
    """Dialect without native upsert; execution uses the Python implementation."""

    native = False
    returning = False

    def __init__(self, dialect: SQLDialect):
        """Keep the adapter of the dialect this extension is installed on."""
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


@upsert_dialects.register_for(PostgreDialect)
class PostgreUpsertDialect(SQLUpsertDialect):
    """PostgreSQL conflict handling returns the branch as execution metadata."""

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
        if returning:  # pragma: no branch - the adapter always asks Postgres for the row
            sql += f" RETURNING {returning}, (xmax = 0) AS inserted"
        return sql + ";"


def validate_upsert(table: Table, key: t.Any, values: AnyDict, model_name: str) -> AnyDict:
    """Reject an invalid key or values before any SQL runs; returns the key as a dict."""
    if not isinstance(key, t.Mapping) or not key:
        raise UpsertKeyError("upsert requires a nonempty key mapping")
    key = dict(key)
    id_names = {"id", table._id.name}
    if id_names & key.keys():
        raise UpsertKeyError("upsert key must not contain id")
    if id_names & values.keys():
        raise UpsertKeyError("upsert values must not contain id; it would re-key the existing row")
    if any(value is None for value in key.values()):
        raise UpsertKeyError("upsert key values must not be None")
    if invalid := sorted(name for name, value in key.items() if not isinstance(value, UPSERT_KEY_TYPES)):
        raise UpsertKeyError(f"upsert key values must be plain scalars; invalid for: {', '.join(invalid)}")
    if overlap := key.keys() & values.keys():
        # field names only: values may come from request data and should not end up in logs
        lookup = ", ".join(f"{name!r}: ..." for name in key)
        kwargs = ", ".join(f"{name}=..." for name in key | values)
        raise UpsertKeyError(
            f"upsert key fields must not also be supplied as values ({', '.join(sorted(overlap))}). "
            f"To change a key field, use {model_name}.update_or_insert({{{lookup}}}, {kwargs})."
        )
    if unknown := (key.keys() | values.keys()) - set(table.fields):
        raise UpsertKeyError(f"Unknown upsert fields: {', '.join(sorted(unknown))}")
    return key


def _execute_native(adapter: SQLAdapter, sql: str) -> None:
    try:
        adapter.execute(sql)
    except Exception as error:
        if getattr(error, "sqlstate", None) == "42P10" or getattr(error, "pgcode", None) == "42P10":
            raise UpsertKeyError("upsert key must match a database unique constraint or unique index") from error
        raise


def _require_row(record: Row | None) -> Row:
    """The row a write just produced; a concurrent delete (or a trigger) can make it disappear before it's read."""
    if record is None:
        raise RuntimeError("The upserted row could not be retrieved; it may have been deleted concurrently")
    return record


def _lookup(table: Table, key: AnyDict) -> Row | None:
    rows = table._db(match_fields((table[name], value) for name, value in key.items())).select(
        table.ALL, limitby=(0, 2)
    )
    if len(rows) > 1:
        raise UpsertAmbiguityError("upsert key matches multiple rows; use a unique key")
    return t.cast(Row | None, rows.first())


def _insert_operation(table: Table, key: AnyDict, values: AnyDict) -> OpRow:
    """Apply insert defaults and computations while reusing already converted keys."""
    filtered = table._filter_fields_for_operation(values)
    empty, fields = t.cast(tuple[list[str], dict[str, tuple[Field, t.Any]]], filtered)
    fields.update({name: (table[name], value) for name, value in key.items()})
    to_compute = []
    for name in empty:
        if name in key:
            continue
        field = table[name]
        if field.compute:
            to_compute.append((name, field))
        elif field.default is not None:
            fields[name] = (field, field.default)
        elif field.required:
            raise RuntimeError("Table: missing required field: %s" % name)
    return table._compute_fields_for_operation(fields, to_compute)


def _autodelete_upload_fields(table: Table, values: AnyDict) -> list[str]:
    """Upload fields in `values` whose replaced file PyDAL would delete on a normal update."""
    if delete_uploaded_files not in table._before_update:
        return []
    return [
        name
        for name in values
        if table[name].type == "upload" and table[name].uploadfield is True and table[name].autodelete
    ]


def _is_filtered(table: Table) -> bool:
    """Common filters and multi-tenant defaults restrict which rows a write may touch."""
    tenant = table._db._request_tenant
    return bool(table._common_filter) or (tenant in table.fields and table[tenant].default is not None)


def _prepare(table: Table, key: AnyDict, values: AnyDict) -> tuple[AnyDict, AnyDict]:
    """Convert the key once (so lookup and insert agree) and store uploaded files, like PyDAL's upload hooks."""
    _, key_fields = table._filter_fields_for_operation(key)
    key_operation = table._compute_fields_for_operation(key_fields, [])
    key = {name: key_operation[name] for name in key}
    if t.cast(set[str], table._upload_fieldnames) & values.keys():
        # same conversion PyDAL's attempt_upload_on_insert/update hooks do (store file objects, keep names)
        values = dict(values)
        attempt_upload(table, values)
    return key, values


def _write_native(table: Table, key: AnyDict, values: AnyDict) -> UpsertResult:
    operation = _insert_operation(table, key, values)
    return t.cast(UpsertAdapter, table._db._adapter).upsert(
        table, [table[name] for name in key], operation.op_values(), [table[name] for name in values]
    )


def _write_insert(table: Table, key: AnyDict, values: AnyDict) -> UpsertResult:
    operation = _insert_operation(table, key, values)
    row_id = table._db._adapter.insert(table, operation.op_values())
    record = affected_set(table, [int(row_id)]).select(table.ALL).first()
    return UpsertResult(_require_row(record), "inserted", operation)


def _write_update(table: Table, existing: Row, values: AnyDict, autodelete: list[str]) -> UpsertResult:
    adapter = table._db._adapter
    _, fields = table._filter_fields_for_operation(values)
    operation = table._compute_fields_for_operation(fields, [])
    rows = affected_set(table, [int(existing.id)])
    if autodelete:
        delete_uploaded_files(rows, {name: values[name] for name in autodelete})
    if getattr(adapter.dialect, "update_returning_supported", False):  # Postgres, SQLite 3.35+
        record = update_returning_row(adapter, table, rows.query, operation.op_values())
    else:
        adapter.update(table, rows.query, operation.op_values())
        record = rows.select(table.ALL).first()
    return UpsertResult(_require_row(record), "updated", operation)


def _run_after_hooks(table: Table, result: UpsertResult) -> None:
    """PyDAL's after-hooks for the branch that was actually taken; 'unchanged' runs none."""
    row_id = Reference(int(result.row.id))
    row_id._table = table
    if result.outcome == "inserted":
        for after_insert_hook in table._after_insert:
            after_insert_hook(result.operation, row_id)
    elif result.outcome == "updated":  # pragma: no branch - only a race makes a native upsert 'unchanged'
        rows = affected_set(table, [int(row_id)])
        for after_update_hook in table._after_update:
            after_update_hook(rows, result.operation)


def execute_upsert(table: Table, key: AnyDict, values: AnyDict) -> UpsertResult:
    """Write without PyDAL callbacks, then run after-hooks for the actual branch."""
    key, values = _prepare(table, key, values)
    autodelete = _autodelete_upload_fields(table, values)
    # ON CONFLICT bypasses common filters and tenant defaults, and can't remove a replaced upload first,
    # so those cases take the select-then-write path (which applies the filters to its lookup):
    native = getattr(table._db._adapter.dialect, "upsert_supported", False) and not _is_filtered(table)
    native = native and not autodelete
    # a key-only call returns an existing row as-is, so look it up even when the write would be native:
    existing = None if native and values else _lookup(table, key)
    if existing is not None and not values:
        return UpsertResult(existing, "unchanged", OpRow(table))

    if native:
        result = _write_native(table, key, values)
    elif existing is None:
        result = _write_insert(table, key, values)
    else:
        result = _write_update(table, existing, values, autodelete)
    _run_after_hooks(table, result)
    return result


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
) -> UpsertResult:
    """Adapter.upsert: execute on the current connection and decode the resulting row."""
    upsert_adapter = t.cast(UpsertAdapter, adapter)
    _execute_native(adapter, upsert_adapter._upsert(table, key_fields, insert_fields, update_fields))
    record = None
    outcome: t.Literal["inserted", "updated", "unchanged"] = "unchanged"
    if upsert_adapter.dialect.upsert_returning:  # pragma: no branch - the only native dialect returns rows
        returned = adapter.fetchall()
        if returned:
            outcome = "inserted" if returned[0][-1] else "updated"
            record = parse_returned(adapter, table, [row[:-1] for row in returned]).first()
    if record is None:
        key_names = {field.name for field in key_fields}
        # Reuse converted values instead of evaluating filter_in or callable values twice.
        query = match_fields((field, value) for field, value in insert_fields if field.name in key_names)
        record = table._db(query, ignore_common_filters=True).select(table.ALL, limitby=(0, 1)).first()
    record = _require_row(record)
    names = {field.name for field in update_fields}
    operation = OpRow(table)
    for field, value in insert_fields:
        if outcome == "inserted" or field.name in names:
            operation.set_value(field.name, value, field)
    return UpsertResult(record, outcome, operation)


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
