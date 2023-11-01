import hashlib
import json
import typing
from typing import Any

import dill  # nosec
from pydal.objects import Field, Rows, Set, Table

from .core import TypedField, TypedTable


class _TypedalCache(TypedTable):
    key: TypedField[str]
    data: TypedField[bytes]


class _TypedalCacheDependency(TypedTable):
    entry: TypedField[_TypedalCache]
    table: TypedField[str]
    idx: TypedField[int]


def prepare(field: Any) -> str:
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
    return "|".join(prepare(field) for field in fields)


def hash_cache_key(cache_key: str | bytes) -> str:
    h = hashlib.sha256()
    h.update(cache_key.encode() if isinstance(cache_key, str) else cache_key)
    return h.hexdigest()


def create_and_hash_cache_key(*fields: Any) -> tuple[str, str]:
    key = create_cache_key(*fields)
    return key, hash_cache_key(key)


class HasMetadataProtocol(typing.Protocol):
    _table: Table
    metadata: dict[str, Any]


T_Metadata = typing.TypeVar("T_Metadata", bound=HasMetadataProtocol)

DependencyTuple = tuple[str, int]  # table + id
DependencyTupleSet = set[DependencyTuple]


def _get_table_name(field: Field) -> str:
    return str(field._table).split(" AS ")[0].strip()


def _get_dependency_ids(rows: Rows, dependency_keys: list[tuple[Field, str]]) -> DependencyTupleSet:
    dependencies = set()
    for row in rows:
        for field, table in dependency_keys:
            if idx := row[field]:
                dependencies.add((table, idx))

    return dependencies


def _determine_dependencies_auto(_: T_Metadata, rows: Rows) -> DependencyTupleSet:
    dependency_keys = []
    for field in rows.fields:
        if str(field).endswith(".id"):
            table_name = _get_table_name(field)

            dependency_keys.append((field, table_name))

    return _get_dependency_ids(rows, dependency_keys)


def _determine_dependencies(instance: T_Metadata, rows: Rows, depends_on: list[Any]) -> DependencyTupleSet:
    if not depends_on:
        return _determine_dependencies_auto(instance, rows)

    target_field_names = set()
    for field in depends_on:
        if "." not in field:
            field = f"{instance._table}.{field}"

        target_field_names.add(str(field))

    dependency_keys = []
    for field in rows.fields:
        if str(field) in target_field_names:
            table_name = _get_table_name(field)

            dependency_keys.append((field, table_name))

    return _get_dependency_ids(rows, dependency_keys)


def remove_cache(idx: int | typing.Iterable[int], table: str) -> None:
    if not isinstance(idx, typing.Iterable):
        idx = [idx]

    related = (
        _TypedalCacheDependency.where(table=table).where(lambda row: row.idx.belongs(idx)).select("entry").to_sql()
    )

    _TypedalCache.where(_TypedalCache.id.belongs(related)).delete()


def _remove_cache(s: Set, tablename: str) -> None:
    indeces = s.select("id").column("id")
    remove_cache(indeces, tablename)


def save_to_cache(instance: T_Metadata, rows: Rows) -> T_Metadata:
    db = rows.db
    if (c := instance.metadata.get("cache", {})) and c.get("enabled") and (key := c.get("key")):
        deps = _determine_dependencies(instance, rows, c["depends_on"])

        entry = _TypedalCache.insert(key=key, data=dill.dumps(instance))

        _TypedalCacheDependency.bulk_insert([{"entry": entry, "table": table, "idx": idx} for table, idx in deps])

        db.commit()
        instance.metadata["cache"]["status"] = "fresh"
    return instance


def load_from_cache(key: str) -> Any | None:
    if row := _TypedalCache.where(key=key).first():
        inst = dill.loads(row.data)  # nosec
        inst.metadata["cache"]["status"] = "cached"
        return inst

    return None
