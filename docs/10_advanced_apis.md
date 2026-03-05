# 10. Advanced APIs

This chapter documents a few public APIs that are useful in specific cases, but are not part of the default onboarding flow.

## QueryBuilder on old-style pyDAL tables

If you are migrating incrementally, you can still use TypeDAL's query builder on existing pyDAL tables:

```python
from typedal import QueryBuilder

rows = QueryBuilder(db.some_table).where(id=2).collect()
```

This gives you partial builder ergonomics while keeping your existing table definitions.

> **Important:** support is intentionally limited for old-style tables.
> Internally, `.collect()` is effectively a passthrough to `.execute()` and returns regular pyDAL `Rows`.
> `.first()`/`.first_or_fail()` return a regular pyDAL `Row` in this mode.

### Verified working methods

- Query composition: `.where(...)`, `.select(...)`, `.orderby(...)`, `.groupby(...)`, `.having(...)`
- Execution/introspection: `.execute()`, `.collect()`, `.to_sql()`
- Row access: `.first()`, `.first_or_fail()`
- Pagination helpers: `.paginate()`, `.chunk()`
- Basic set operations: `.count()`, `.exists()`, `.update(...)`, `.delete(...)`, `.collect_or_fail()`
- `.cache(...).collect()` runs (returns pyDAL `Rows`)

### Verified unsupported methods

- `.join(...)`: **not supported** for old-style tables (depends on TypeDAL model relationship internals)

### Behavioral caveat

Legacy mode does not perform typed model mapping. Expect pyDAL `Rows`/`Row` outputs rather than typed entities.

If you need full QueryBuilder behavior (typed entities, relationships, typed joins, cache integration), 
migrate that table to `TypedTable`.

## Upsert and validation helpers

`TypedTable` exposes convenience methods for common upsert/validation flows:

```python
# update if found, otherwise insert
user = User.update_or_insert(User.email == "a@example.com", email="a@example.com", name="Alice")

# validate before insert
created, errors = User.validate_and_insert(email="a@example.com")

# validate before update
updated, errors = User.validate_and_update(User.id == 1, name="Alice Updated")

# validate before update-or-insert
row, errors = User.validate_and_update_or_insert(User.email == "a@example.com", name="Alice")
```

Behavior notes:

- `update_or_insert(...)` returns the resulting instance.
- `validate_and_*` methods return `(instance_or_none, errors_or_none)`.

## Reordering table fields

You can reorder fields on a defined table with `reorder_fields`:

```python
# Keep listed fields first, keep all other fields after them
MyTable.reorder_fields(MyTable.id, MyTable.name)

# Keep only the listed fields
MyTable.reorder_fields(MyTable.id, MyTable.name, keep_others=False)
```

This is useful when you want deterministic field order for SQL generation, inspection, or exports.
