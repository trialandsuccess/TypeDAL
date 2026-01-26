# 9. Function Memoization

TypeDAL provides database-aware function memoization via `db.memoize()`. This allows you to cache the result of expensive functions and automatically invalidate the cache when the underlying database rows change.

## Overview

While `.cache()` on query builders (see [3. Building Queries](./3_building_queries.md#cache)) caches query results, `db.memoize()` caches entire function results while tracking all database operations that happen inside the function.

This is designed for cases where the database query itself is fast, but the application logic that follows is expensive.

## Basic Usage

Wrap any function call with `db.memoize()` to cache its result:

```python
def process_articles(articles: TypedRows[Article]) -> dict:
    result = {}
    # dummy example, normally you'd use .join() of course
    for article in articles:
        comments = Comment.where(article=article).collect()
        result[article.id] = comments  
    return result

articles = Article.where(published=True).collect()

result, status = db.memoize(process_articles, articles)
assert status == "fresh"

result, status = db.memoize(process_articles, articles)
assert status == "cached"

Comment.first().update_record(text="Updated")

result, status = db.memoize(process_articles, articles)
assert status == "fresh"  # cache invalidated!
```

The return value is a tuple of `(result, status)`:
- `result`: The actual return value of the function
- `status`: Either `"fresh"` (newly computed) or `"cached"` (retrieved from cache)

## Automatic Dependency Tracking

TypeDAL automatically tracks all database rows loaded during function execution. This includes:

- Direct queries (`Table.where(...).collect()`)
- Joins (`Table.join().collect()`)
- Nested queries inside loops

When any tracked row is updated, inserted, or deleted, the cached result is invalidated:

```python
def something_slow():
    return list(User.join())

result, status = db.memoize(something_slow)
assert status == "fresh"

User.first().update_record(name="Changed")

result, status = db.memoize(something_slow)
assert status == "fresh"  # cache was invalidated
```

## TTL (Time To Live)

Control cache lifetime using the `ttl` parameter. It accepts seconds (int), a `timedelta`, or an absolute `datetime`:

```python
from datetime import datetime, timedelta

# Expire after 1 hour (3600 seconds)
db.memoize(func, data, ttl=3600)

# Expire after 1 hour (timedelta)
db.memoize(func, data, ttl=timedelta(hours=1))

# Expire at specific datetime
db.memoize(func, data, ttl=datetime(2026, 1, 7))
```

## Cache Maintenance

The `typedal.caching` module provides utilities for cache management:

```python
from typedal.caching import clear_cache, remove_cache_for_table, clear_expired

# Remove all cache entries
clear_cache()

# Invalidate all cache entries related to a specific table
remove_cache_for_table(User)

# Clean up expired entries only
clear_expired()
```

You can also use the CLI:

```bash
# Clean up expired entries
typedal cache.clear

# Show cache statistics
typedal cache.stats
```

## Debugging & Profiling

TypeDAL provides `before_collect`/`before_execute` and `after_collect`/`after_execute` hooks on the database instance for debugging and profiling queries:

```python
def print_query(qb: QueryBuilder):
    print("going to run", qb.to_sql())

def print_duration(_qb: QueryBuilder, rows, _raw):
    print("took", rows.metadata["select_duration"])

db.before_collect.append(print_query)
db.after_collect.append(print_duration)

TestQueryTable.all()  # will trigger both hooks
```

These hooks are used internally for dependency tracking but are exposed for debugging and observability.

## How It Works

When you call `db.memoize(func, *args, **kwargs)`:

1. TypeDAL computes a cache key based on the function and its arguments
2. If a valid cached result exists, it's returned with `status="cached"`
3. Otherwise, the function is executed while tracking all database operations
4. The result is cached along with the IDs of all rows accessed
5. When any tracked row changes, the cache entry is invalidated

The cache is stored in the `typedal_cache` (and `typedal_cache_dependency`) table (same as query-level caching).

## Disabling Dependency Tracking

If you need to disable cache invalidation hooks for a specific table:

```python
@db.define(cache_dependency=False)
class SpecialTable(TypedTable):
    ...
```

**Warning:** Disabling this may break caching functionality for queries involving this table.
