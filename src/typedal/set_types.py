"""
TypeDAL's PyDAL Set subclasses with affected-ID update callbacks.

Separate from updates.py (the adapter and dialect extensions) so that types.py can re-export these classes:
this module only needs pydal at runtime.
"""

from __future__ import annotations

import functools
import operator
import typing as t
from dataclasses import dataclass

from pydal.objects import Set

if t.TYPE_CHECKING:
    from pydal import DAL

    from .types import Field, OpRow, PrimaryKey, Query, Table, UpsertKeyValue


@dataclass
class UpdateResult:
    """Rowcount and IDs, collected only when requested or needed by after-hooks."""

    count: int
    ids: list[PrimaryKey]


class UpdateAdapter(t.Protocol):
    """Typed interface for the adapter's instance-local `update_with_ids` extension (see updates.install_update)."""

    def update_with_ids(self, table: Table, query: Query | None, fields: list[tuple[Field, t.Any]]) -> UpdateResult:
        """Update the matching rows and return the rowcount and their primary keys."""
        ...


def key_fields(table: Table) -> list[Field]:
    """The primary key columns: `id` for normal tables, `primarykey=[...]` for keyed tables."""
    primarykey = getattr(table, "_primarykey", None)
    if primarykey:
        return [table[name] for name in primarykey]
    return [table._id]


def key_value(fields: list[Field], values: t.Sequence[UpsertKeyValue]) -> PrimaryKey:
    """A scalar for single-column keys (so integer ids stay plain ints), a tuple for composite keys."""
    return values[0] if len(fields) == 1 else tuple(values)


def match_fields(pairs: t.Iterable[tuple[Field, t.Any]]) -> Query:
    """AND together `field == value` for every pair; there must be at least one."""
    return t.cast("Query", functools.reduce(operator.and_, (field == value for field, value in pairs)))


def ids_query(table: Table, ids: list[PrimaryKey]) -> Query:
    """Match exactly the rows identified by `ids` (as produced by `key_value`)."""
    fields = key_fields(table)
    if len(fields) == 1 or not ids:
        return fields[0].belongs(ids)
    # composite keys (more than one key field) are always tuples, see key_value
    matches = [match_fields(zip(fields, t.cast("tuple[UpsertKeyValue, ...]", key), strict=True)) for key in ids]
    return t.cast("Query", functools.reduce(operator.or_, matches))


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

    def _update_row(self, fields: dict[str, t.Any]) -> tuple[Table, OpRow]:
        """The table this set updates and the validated changes; refuses an update without any."""
        table = self.db._adapter.get_table(self.query)
        row = table._fields_and_values_for_update(fields)
        if not row.op_values():
            raise ValueError("No fields to update")
        return table, row

    def _write(self, table: Table, row: OpRow, run_callbacks: bool, need_ids: bool = False) -> UpdateResult:
        """
        Run the update, with PyDAL's before- and after-hooks when `run_callbacks`.

        The affected IDs are only collected when the caller (`need_ids`) or an after-hook needs them.
        """
        if run_callbacks and any(hook(self, row) for hook in table._before_update):
            return UpdateResult(0, [])
        if not need_ids and not (run_callbacks and table._after_update):
            count = self.db._adapter.update(table, self.query, row.op_values())
            return UpdateResult(count, [])
        adapter = t.cast("UpdateAdapter", self.db._adapter)
        result = adapter.update_with_ids(table, self.query, row.op_values())
        if result.ids and run_callbacks:
            rows = affected_set(table, result.ids)
            for hook in table._after_update:
                hook(rows, row)
        return result

    def update_ids(self, **fields: t.Any) -> list[PrimaryKey]:
        """Update the matching rows, running hooks, and return the primary keys of the updated rows."""
        return self._write(*self._update_row(fields), run_callbacks=True, need_ids=True).ids

    def update(self, **fields: t.Any) -> int:
        """Update the matching rows, running hooks, and return the rowcount."""
        return self._write(*self._update_row(fields), run_callbacks=True).count

    def _apply_update(self, table: Table, row: OpRow, run_callbacks: bool) -> int:
        """Route PyDAL's validated updates through the same affected-ID callbacks."""
        return self._write(table, row, run_callbacks=run_callbacks).count

    def update_naive(self, **fields: t.Any) -> int:
        """Update the matching rows without running any hooks and return the rowcount."""
        return self._write(*self._update_row(fields), run_callbacks=False).count


class AffectedSet(UpdateSet):
    """An ID-restricted hook Set carrying the IDs already obtained by the write."""

    def __init__(self, db: DAL, query: Query, affected_ids: list[PrimaryKey]):
        """Match the affected rows by key, ignoring common filters (the update may have moved them out)."""
        super().__init__(db, query, ignore_common_filters=True)
        self.affected_ids = affected_ids
