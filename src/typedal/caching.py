"""
Helpers to facilitate db-based caching.
"""
import contextlib
import hashlib
import json
import typing
from typing import Any

import dill  # nosec
from pydal.objects import Field, Rows, Set

from .core import TypedField, TypedRows, TypedTable


class _TypedalCache(TypedTable):
    """
    Internal table to store previously loaded models.
    """

    key: TypedField[str]
    data: TypedField[bytes]
    # todo: cached_at + ttl


class _TypedalCacheDependency(TypedTable):
    """
    Internal table that stores dependencies to invalidate cache when a related table is updated.
    """

    entry: TypedField[_TypedalCache]
    table: TypedField[str]
    idx: TypedField[int]


def prepare(field: Any) -> str:
    """
    Prepare data to be used in a cache key.

    By sorting and stringifying data, queries can be syntactically different from each other \
        but when semantically exactly the same will still be loaded from cache.
    """
    if isinstance(field, str):
        return field
    elif isinstance(field, (dict, typing.Mapping)):
        data = {str(k): prepare(v) for k, v in field.items()}
        return json.dumps(data, sort_keys=True)
    elif isinstance(field, typing.Iterable):
        return ",".join(sorted([prepare(_) for _ in field]))
    elif isinstance(field, bool):
        return str(int(field))
    else:
        return str(field)


def create_cache_key(*fields: Any) -> str:
    """
    Turn any fields of data into a string.
    """
    return "|".join(prepare(field) for field in fields)


def hash_cache_key(cache_key: str | bytes) -> str:
    """
    Hash the input cache key with SHA 256.
    """
    h = hashlib.sha256()
    h.update(cache_key.encode() if isinstance(cache_key, str) else cache_key)
    return h.hexdigest()


def create_and_hash_cache_key(*fields: Any) -> tuple[str, str]:
    """
    Combine the input fields into one key and hash it with SHA 256.
    """
    key = create_cache_key(*fields)
    return key, hash_cache_key(key)


DependencyTuple = tuple[str, int]  # table + id
DependencyTupleSet = set[DependencyTuple]


def _get_table_name(field: Field) -> str:
    """
    Get the table name from a field or alias.
    """
    return str(field._table).split(" AS ")[0].strip()


def _get_dependency_ids(rows: Rows, dependency_keys: list[tuple[Field, str]]) -> DependencyTupleSet:
    dependencies = set()
    for row in rows:
        for field, table in dependency_keys:
            if idx := row[field]:
                dependencies.add((table, idx))

    return dependencies


def _determine_dependencies_auto(_: TypedRows[Any], rows: Rows) -> DependencyTupleSet:
    dependency_keys = []
    for field in rows.fields:
        if str(field).endswith(".id"):
            table_name = _get_table_name(field)

            dependency_keys.append((field, table_name))

    return _get_dependency_ids(rows, dependency_keys)


def _determine_dependencies(instance: TypedRows[Any], rows: Rows, depends_on: list[Any]) -> DependencyTupleSet:
    if not depends_on:
        return _determine_dependencies_auto(instance, rows)

    target_field_names = set()
    for field in depends_on:
        if "." not in field:
            field = f"{instance.model._table}.{field}"

        target_field_names.add(str(field))

    dependency_keys = []
    for field in rows.fields:
        if str(field) in target_field_names:
            table_name = _get_table_name(field)

            dependency_keys.append((field, table_name))

    return _get_dependency_ids(rows, dependency_keys)


def remove_cache(idx: int | typing.Iterable[int], table: str) -> None:
    """
    Remove any cache entries that are dependant on one or multiple indices of a table.
    """
    if not isinstance(idx, typing.Iterable):
        idx = [idx]

    related = (
        _TypedalCacheDependency.where(table=table).where(lambda row: row.idx.belongs(idx)).select("entry").to_sql()
    )

    _TypedalCache.where(_TypedalCache.id.belongs(related)).delete()


def _remove_cache(s: Set, tablename: str) -> None:
    """
    Used as the table._before_update and table._after_update for every TypeDAL table (on by default).
    """
    indeces = s.select("id").column("id")
    remove_cache(indeces, tablename)


T_TypedTable = typing.TypeVar("T_TypedTable", bound=TypedTable)


def save_to_cache(instance: TypedRows[T_TypedTable], rows: Rows) -> TypedRows[T_TypedTable]:
    """
    Save a typedrows result to the database, and save dependencies from rows.

    You can call .cache(...) with dependent fields (e.g. User.id) or this function will determine them automatically.
    """
    db = rows.db
    if (c := instance.metadata.get("cache", {})) and c.get("enabled") and (key := c.get("key")):
        deps = _determine_dependencies(instance, rows, c["depends_on"])

        entry = _TypedalCache.insert(key=key, data=dill.dumps(instance))

        _TypedalCacheDependency.bulk_insert([{"entry": entry, "table": table, "idx": idx} for table, idx in deps])

        db.commit()
        instance.metadata["cache"]["status"] = "fresh"
    return instance


def _load_from_cache(key: str) -> Any | None:
    if row := _TypedalCache.where(key=key).first():
        inst = dill.loads(row.data)  # nosec
        inst.metadata["cache"]["status"] = "cached"
        return inst

    return None


def load_from_cache(key: str) -> Any | None:
    """
    If 'key' matches a non-expired row in the database, try to load the dill.

    If anything fails, return None.
    """
    with contextlib.suppress(Exception):
        return _load_from_cache(key)

    return None  # pragma: no cover
