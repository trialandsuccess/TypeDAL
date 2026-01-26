"""
Helpers to facilitate db-based caching.
"""

import contextlib
import datetime as dt
import hashlib
import json
import typing as t

import dill  # nosec
from pydal.objects import Field, Rows, Set

from .fields import TypedField
from .helpers import throw
from .rows import TypedRows
from .tables import TypedTable
from .types import CacheStatus, Query

if t.TYPE_CHECKING:
    from .core import TypeDAL
    from .query_builder import QueryBuilder


def get_now(tz: dt.timezone = dt.timezone.utc) -> dt.datetime:
    """
    Get the default datetime, optionally in a specific timezone.
    """
    return dt.datetime.now(tz)


class _TypedalCache(TypedTable):
    """
    Internal table to store previously loaded models.
    """

    key: TypedField[str]
    data: TypedField[bytes]
    cached_at = TypedField(dt.datetime, default=get_now)
    expires_at: TypedField[dt.datetime | None]


class _TypedalCacheDependency(TypedTable):
    """
    Internal table that stores dependencies to invalidate cache when a related table is updated.
    """

    entry: TypedField[_TypedalCache]
    table: TypedField[str]
    idx: TypedField[int]


def prepare(field: t.Any) -> str:
    """
    Prepare data to be used in a cache key.

    By sorting and stringifying data, queries can be syntactically different from each other \
        but when semantically exactly the same will still be loaded from cache.
    """
    if isinstance(field, str):
        return field
    elif isinstance(field, (dict, t.Mapping)):
        data = {str(k): prepare(v) for k, v in field.items()}
        return json.dumps(data, sort_keys=True)
    elif isinstance(field, t.Iterable):
        return ",".join(sorted([prepare(_) for _ in field]))
    elif isinstance(field, bool):
        return str(int(field))
    else:
        return str(field)


def create_cache_key(*fields: t.Any) -> str:
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


def create_and_hash_cache_key(*fields: t.Any) -> tuple[str, str]:
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


def _determine_dependencies_auto(rows: Rows) -> DependencyTupleSet:
    dependency_keys = []
    for field in rows.fields:
        if str(field).endswith(".id"):
            table_name = _get_table_name(field)

            dependency_keys.append((field, table_name))

    return _get_dependency_ids(rows, dependency_keys)


def _determine_dependencies(instance: TypedRows[t.Any], rows: Rows, depends_on: list[t.Any]) -> DependencyTupleSet:
    if not depends_on:
        return _determine_dependencies_auto(rows)

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


def remove_cache(idx: int | t.Iterable[int], table: str) -> None:
    """
    Remove any cache entries that are dependant on one or multiple indices of a table.
    """
    if not isinstance(idx, t.Iterable):
        idx = [idx]

    related = (
        _TypedalCacheDependency.where(table=table).where(lambda row: row.idx.belongs(idx)).select("entry").to_sql()
    )

    _TypedalCache.where(_TypedalCache.id.belongs(related)).delete()


def remove_cache_for_table(table: str) -> None:
    """
    Remove all cache entries that depend on a table.

    Used for inserts where we don't know which cached queries
    the new row would match.
    """
    related = _TypedalCacheDependency.where(table=table).select("entry").to_sql()
    _TypedalCache.where(_TypedalCache.id.belongs(related)).delete()


def clear_cache() -> None:
    """
    Remove everything from the cache.

    Immediately commits
    """
    db: TypeDAL = _TypedalCache._db or throw(
        RuntimeError("@define or db.define is not called on typedal caching classes yet!")
    )

    _TypedalCache.truncate("RESTART IDENTITY CASCADE")
    db.commit()


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


T_TypedTable = t.TypeVar("T_TypedTable", bound=TypedTable)


def get_expire(
    expires_at: t.Optional[dt.datetime] = None,
    ttl: t.Optional[int | dt.timedelta] = None,
    now: t.Optional[dt.datetime] = None,
) -> dt.datetime | None:
    """
    Based on an expires_at date or a ttl (in seconds or a time delta), determine the expire date.
    """
    now = now or get_now()

    if expires_at and ttl:
        raise ValueError("Please only supply an `expired at` date or a `ttl` in seconds!")
    elif isinstance(ttl, dt.timedelta):
        return now + ttl
    elif ttl:
        return now + dt.timedelta(seconds=ttl)
    elif expires_at:
        return expires_at

    return None


def _insert_cache_entry(
    db: "TypeDAL",
    key: str,
    data: t.Any,
    expires_at: dt.datetime | None,
    deps: DependencyTupleSet,
) -> None:
    """
    Shared internal function to insert cache entry and dependencies.
    """
    entry = _TypedalCache.insert(
        key=key,
        data=dill.dumps(data),
        expires_at=expires_at,
    )

    _TypedalCacheDependency.bulk_insert([{"entry": entry, "table": table, "idx": idx} for table, idx in deps])

    db.commit()


def save_to_cache(
    instance: TypedRows[T_TypedTable],
    rows: Rows,
    expires_at: t.Optional[dt.datetime] = None,
    ttl: t.Optional[int | dt.timedelta] = None,
) -> TypedRows[T_TypedTable]:
    """
    Save a typedrows result to the database, and save dependencies from rows.

    You can call .cache(...) with dependent fields (e.g. User.id) or this function will determine them automatically.
    """
    db = rows.db
    if (c := instance.metadata.get("cache", {})) and c.get("enabled") and (key := c.get("key")):
        expires_at = get_expire(expires_at=expires_at, ttl=ttl) or c.get("expires_at")
        deps = _determine_dependencies(instance, rows, c["depends_on"])

        _insert_cache_entry(db, key, instance, expires_at, deps)

        instance.metadata["cache"]["status"] = "fresh"
    return instance


class CacheMiss:
    """Sentinel class to represent a cache miss, distinguishing it from a None value."""

    def __bool__(self) -> bool:  # pragma: no cover
        """
        Ensures the sentinel evaluates to False in boolean contexts.

        Example:
            res = _load_memoize_from_cache("key")
            if not res:  # Triggers if a CacheMiss occurs
                ...
        """
        return False


_CACHE_SENTINEL: t.Final[CacheMiss] = CacheMiss()


def _fetch_cached_payload(key: str) -> tuple[t.Any, t.Any] | None:
    """
    Retrieves and validates a cache entry from the database.

    If the entry exists but is expired, it is deleted from the database
    to maintain storage hygiene.

    Args:
        key: The unique string identifier for the cache entry.

    Returns:
        A tuple of (deserialized_data, db_row) if valid; None if missing or expired.
    """
    row = _TypedalCache.where(key=key).first()
    if not row:
        return None

    now = get_now()
    # Ensure comparison is offset-aware if the row has a timestamp
    expires = row.expires_at.replace(tzinfo=dt.timezone.utc) if row.expires_at else None

    if expires and now >= expires:
        row.delete_record()
        return None

    # Only one place for deserialization to happen
    return dill.loads(row.data), row  # nosec


def _load_from_cache(key: str, db: "TypeDAL") -> t.Any | None:
    """
    Loads a specific TypeDAL model instance from the cache and re-hydrates it.

    This binds the cached object back to the active database connection and
    restores its instance methods/metadata.

    Args:
        key: Cache key.
        db: The active TypeDAL database instance.

    Returns:
        The re-hydrated model instance, or None if the load fails.
    """
    result = _fetch_cached_payload(key)
    if not result:
        return None

    inst, row = result

    # Re-hydrate the model instance with metadata and DB context
    inst.metadata.setdefault("cache", {}).update(
        {"status": "cached", "cached_at": row.cached_at, "expires_at": row.expires_at},
    )

    inst.db = db
    inst.model = db._class_map[inst.model]
    inst.model._setup_instance_methods(inst.model)
    return inst


def _load_memoize_from_cache(key: str) -> t.Any:
    """
    Low-level retrieval for memoized results.

    Used when the caller doesn't need TypeDAL model re-hydration, just the raw data.

    Args:
        key: Cache key.

    Returns:
        The deserialized data or the _CACHE_SENTINEL object if not found.
    """
    with contextlib.suppress(Exception):
        if result := _fetch_cached_payload(key):
            return result[0]

    return _CACHE_SENTINEL


def load_from_cache(key: str, db: "TypeDAL") -> t.Any | None:
    """
    Public entry point to load model instances from cache.

    Wraps the internal loader in a broad exception handler to ensure that
    cache failures never crash the main application flow.

    Args:
        key: Cache key.
        db: The active TypeDAL database instance.

    Returns:
        The model instance or None on any failure/miss.
    """
    try:
        return _load_from_cache(key, db)
    except Exception:  # pragma: no cover
        return None


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


T = t.TypeVar("T")
Stats = t.TypedDict("Stats", {"total": T, "valid": T, "expired": T})

RowStats = t.TypedDict(
    "RowStats",
    {
        "Dependent Cache Entries": int,
    },
)


def _row_stats(db: "TypeDAL", table: str, query: Query) -> RowStats:
    count_field = _TypedalCacheDependency.entry.count()
    stats: TypedRows[_TypedalCacheDependency] = db(query & (_TypedalCacheDependency.table == table)).select(
        _TypedalCacheDependency.entry,
        count_field,
        groupby=_TypedalCacheDependency.entry,
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


TableStats = t.TypedDict(
    "TableStats",
    {
        "Dependent Cache Entries": int,
        "Associated Table IDs": int,
    },
)


def _table_stats(db: "TypeDAL", table: str, query: Query) -> TableStats:
    count_field = _TypedalCacheDependency.entry.count()
    stats: TypedRows[_TypedalCacheDependency] = db(query & (_TypedalCacheDependency.table == table)).select(
        _TypedalCacheDependency.entry,
        count_field,
        groupby=_TypedalCacheDependency.entry,
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


GenericStats = t.TypedDict(
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


def memoize(
    db: "TypeDAL",
    func: t.Callable[..., T],
    *args: TypedRows[t.Any] | TypedTable,
    key: str | None = None,
    ttl: int | dt.timedelta | dt.datetime | None = None,
    **kwargs: t.Any,
) -> tuple[T, CacheStatus]:
    """
    Cache the result of a function applied to TypedRow(s).

    Tracks dependencies on the table(s) so the cache invalidates
    when those rows are updated/deleted.

    Args:
        db: TypeDAL database
        func: Function to cache
        *args: TypedRow/TypedRows instances only
        key: Cache key (required for lambdas)
        ttl: Time to live in seconds/timedelta, or datetime to expire at
        **kwargs: Extra parameters passed to func

    Returns:
        tuple of (result, cache_status)
    """
    if not key:
        if func.__name__ == "<lambda>":
            raise ValueError(
                "Lambda functions require explicit 'key' parameter. Use: db.memoize(your_func, data, key='my_key')",
            )
        key = func.__qualname__

    # Extract dependencies from args
    deps: DependencyTupleSet = set()
    for arg in args:
        if isinstance(arg, TypedRows):
            for row in arg:
                deps.add((str(row._table), row.id))
        elif isinstance(arg, TypedTable):
            deps.add((str(arg._table), arg.id))

    # Generate cache key
    _, hashed_key = create_and_hash_cache_key(key, *[getattr(arg, "id", None) for arg in args], kwargs)

    # Try to load from cache
    cached = _load_memoize_from_cache(hashed_key)
    if cached is not _CACHE_SENTINEL:
        return cached, "cached"
    # Cache miss - compute result

    def track_execute(qb: "QueryBuilder[t.Any]", raw: Rows):
        # find dependant table+id combinations, includes relationships:
        deps.update(_determine_dependencies_auto(raw))

        # tables: qb.model;
        # something with qb.relationships
        # something with qb.select_args

        related_tables = (
            {
                # original table
                str(qb.model)
            }
            | {
                # other tables in select()
                _get_table_name(arg)
                for arg in qb.select_args
            }
            | {
                # other tables in relationships
                str(r.table)
                for r in qb.relationships.values()
            }
        )

        # mark dependency for every relevant table in this query without id:
        deps.update({(table, 0) for table in related_tables})

    def track_collect(qb: "QueryBuilder[t.Any]", _: TypedRows[t.Any], raw: Rows) -> None:
        return track_execute(qb, raw)

    # hooks every .collect() to track extra dependencies
    db._after_collect.append(track_collect)
    db._after_execute.append(track_execute)
    try:
        result = func(*args, **kwargs)
    finally:
        db._after_collect.remove(track_collect)
        db._after_execute.remove(track_execute)

    # Save to cache
    if isinstance(ttl, dt.datetime):
        expires_at: dt.datetime | None = ttl
    else:
        expires_at = get_expire(ttl=ttl)

    _insert_cache_entry(db, hashed_key, result, expires_at, deps)

    return result, "fresh"
