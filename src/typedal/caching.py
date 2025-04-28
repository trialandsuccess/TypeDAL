"""
Helpers to facilitate db-based caching.
"""

import contextlib
import hashlib
import json
import typing
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable, Mapping, Optional, TypeVar

import dill  # nosec
from pydal.objects import Field, Rows, Set

from .core import TypedField, TypedRows, TypedTable
from .types import Query

if typing.TYPE_CHECKING:
    from .core import TypeDAL


def get_now(tz: timezone = timezone.utc) -> datetime:
    """
    Get the default datetime, optionally in a specific timezone.
    """
    return datetime.now(tz)


class _TypedalCache(TypedTable):
    """
    Internal table to store previously loaded models.
    """

    key: TypedField[str]
    data: TypedField[bytes]
    cached_at = TypedField(datetime, default=get_now)
    expires_at: TypedField[datetime | None]


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
    elif isinstance(field, (dict, Mapping)):
        data = {str(k): prepare(v) for k, v in field.items()}
        return json.dumps(data, sort_keys=True)
    elif isinstance(field, Iterable):
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


def remove_cache(idx: int | Iterable[int], table: str) -> None:
    """
    Remove any cache entries that are dependant on one or multiple indices of a table.
    """
    if not isinstance(idx, Iterable):
        idx = [idx]

    related = (
        _TypedalCacheDependency.where(table=table).where(lambda row: row.idx.belongs(idx)).select("entry").to_sql()
    )

    _TypedalCache.where(_TypedalCache.id.belongs(related)).delete()


def clear_cache() -> None:
    """
    Remove everything from the cache.
    """
    _TypedalCacheDependency.truncate()
    _TypedalCache.truncate()


def clear_expired() -> int:
    """
    Remove all expired items from the cache.

    By default, expired items are only removed when trying to access them.
    """
    now = get_now()
    return len(_TypedalCache.where(_TypedalCache.expires_at != None).where(_TypedalCache.expires_at < now).delete())


def _remove_cache(s: Set, tablename: str) -> None:
    """
    Used as the table._before_update and table._after_update for every TypeDAL table (on by default).
    """
    indeces = s.select("id").column("id")
    remove_cache(indeces, tablename)


T_TypedTable = TypeVar("T_TypedTable", bound=TypedTable)


def get_expire(
    expires_at: Optional[datetime] = None, ttl: Optional[int | timedelta] = None, now: Optional[datetime] = None
) -> datetime | None:
    """
    Based on an expires_at date or a ttl (in seconds or a time delta), determine the expire date.
    """
    now = now or get_now()

    if expires_at and ttl:
        raise ValueError("Please only supply an `expired at` date or a `ttl` in seconds!")
    elif isinstance(ttl, timedelta):
        return now + ttl
    elif ttl:
        return now + timedelta(seconds=ttl)
    elif expires_at:
        return expires_at

    return None


def save_to_cache(
    instance: TypedRows[T_TypedTable],
    rows: Rows,
    expires_at: Optional[datetime] = None,
    ttl: Optional[int | timedelta] = None,
) -> TypedRows[T_TypedTable]:
    """
    Save a typedrows result to the database, and save dependencies from rows.

    You can call .cache(...) with dependent fields (e.g. User.id) or this function will determine them automatically.
    """
    db = rows.db
    if (c := instance.metadata.get("cache", {})) and c.get("enabled") and (key := c.get("key")):
        expires_at = get_expire(expires_at=expires_at, ttl=ttl) or c.get("expires_at")

        deps = _determine_dependencies(instance, rows, c["depends_on"])

        entry = _TypedalCache.insert(
            key=key,
            data=dill.dumps(instance),
            expires_at=expires_at,
        )

        _TypedalCacheDependency.bulk_insert([{"entry": entry, "table": table, "idx": idx} for table, idx in deps])

        db.commit()
        instance.metadata["cache"]["status"] = "fresh"
    return instance


def _load_from_cache(key: str, db: "TypeDAL") -> Any | None:
    if not (row := _TypedalCache.where(key=key).first()):
        return None

    now = get_now()

    expires = row.expires_at.replace(tzinfo=timezone.utc) if row.expires_at else None

    if expires and now >= expires:
        row.delete_record()
        return None

    inst = dill.loads(row.data)  # nosec

    inst.metadata["cache"]["status"] = "cached"
    inst.metadata["cache"]["cached_at"] = row.cached_at
    inst.metadata["cache"]["expires_at"] = row.expires_at

    inst.db = db
    inst.model = db._class_map[inst.model]
    inst.model._setup_instance_methods(inst.model)  # type: ignore
    return inst


def load_from_cache(key: str, db: "TypeDAL") -> Any | None:
    """
    If 'key' matches a non-expired row in the database, try to load the dill.

    If anything fails, return None.
    """
    with contextlib.suppress(Exception):
        return _load_from_cache(key, db)

    return None  # pragma: no cover


def humanize_bytes(size: int | float) -> str:
    """
    Turn a number of bytes into a human-readable version (e.g. 124 GB).
    """
    if not size:
        return "0"

    suffixes = ["B", "KB", "MB", "GB", "TB", "PB"]  # List of suffixes for different magnitudes
    suffix_index = 0

    while size > 1024 and suffix_index < len(suffixes) - 1:
        suffix_index += 1
        size /= 1024.0

    return f"{size:.2f} {suffixes[suffix_index]}"


def _expired_and_valid_query() -> tuple[str, str]:
    expired_items = (
        _TypedalCache.where(lambda row: (row.expires_at < get_now()) & (row.expires_at != None))
        .select(_TypedalCache.id)
        .to_sql()
    )

    valid_items = _TypedalCache.where(~_TypedalCache.id.belongs(expired_items)).select(_TypedalCache.id).to_sql()

    return expired_items, valid_items


T = typing.TypeVar("T")
Stats = typing.TypedDict("Stats", {"total": T, "valid": T, "expired": T})

RowStats = typing.TypedDict(
    "RowStats",
    {
        "Dependent Cache Entries": int,
    },
)


def _row_stats(db: "TypeDAL", table: str, query: Query) -> RowStats:
    count_field = _TypedalCacheDependency.entry.count()
    stats: TypedRows[_TypedalCacheDependency] = db(query & (_TypedalCacheDependency.table == table)).select(
        _TypedalCacheDependency.entry, count_field, groupby=_TypedalCacheDependency.entry
    )
    return {
        "Dependent Cache Entries": len(stats),
    }


def row_stats(db: "TypeDAL", table: str, row_id: str) -> Stats[RowStats]:
    """
    Collect caching stats for a specific table row (by ID).
    """
    expired_items, valid_items = _expired_and_valid_query()

    query = _TypedalCacheDependency.idx == row_id

    return {
        "total": _row_stats(db, table, query),
        "valid": _row_stats(db, table, _TypedalCacheDependency.entry.belongs(valid_items) & query),
        "expired": _row_stats(db, table, _TypedalCacheDependency.entry.belongs(expired_items) & query),
    }


TableStats = typing.TypedDict(
    "TableStats",
    {
        "Dependent Cache Entries": int,
        "Associated Table IDs": int,
    },
)


def _table_stats(db: "TypeDAL", table: str, query: Query) -> TableStats:
    count_field = _TypedalCacheDependency.entry.count()
    stats: TypedRows[_TypedalCacheDependency] = db(query & (_TypedalCacheDependency.table == table)).select(
        _TypedalCacheDependency.entry, count_field, groupby=_TypedalCacheDependency.entry
    )
    return {
        "Dependent Cache Entries": len(stats),
        "Associated Table IDs": sum(stats.column(count_field)),
    }


def table_stats(db: "TypeDAL", table: str) -> Stats[TableStats]:
    """
    Collect caching stats for a table.
    """
    expired_items, valid_items = _expired_and_valid_query()

    return {
        "total": _table_stats(db, table, _TypedalCacheDependency.id > 0),
        "valid": _table_stats(db, table, _TypedalCacheDependency.entry.belongs(valid_items)),
        "expired": _table_stats(db, table, _TypedalCacheDependency.entry.belongs(expired_items)),
    }


GenericStats = typing.TypedDict(
    "GenericStats",
    {
        "entries": int,
        "dependencies": int,
        "size": str,
    },
)


def _calculate_stats(db: "TypeDAL", query: Query) -> GenericStats:
    sum_len_field = _TypedalCache.data.len().sum()
    size_row = db(query).select(sum_len_field).first()

    size = size_row[sum_len_field] if size_row else 0

    return {
        "entries": _TypedalCache.where(query).count(),
        "dependencies": db(_TypedalCacheDependency.entry.belongs(query)).count(),
        "size": humanize_bytes(size),
    }


def calculate_stats(db: "TypeDAL") -> Stats[GenericStats]:
    """
    Collect generic caching stats.
    """
    expired_items, valid_items = _expired_and_valid_query()

    return {
        "total": _calculate_stats(db, _TypedalCache.id > 0),
        "valid": _calculate_stats(db, _TypedalCache.id.belongs(valid_items)),
        "expired": _calculate_stats(db, _TypedalCache.id.belongs(expired_items)),
    }
