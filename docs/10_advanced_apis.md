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

### Unique-key upsert in v6

`upsert(key, **values)` inserts or updates by a unique key and returns the resulting
typed instance. It is a table-class operation; query builders and row instances do
not expose it. `upsert_async` has the same contract.

```python
user = User.upsert({"email": "a@example.com"}, name="Alice")
user = await User.upsert_async({"email": "a@example.com"}, name="Bob")
```

The key must be nonempty, contain known fields, exclude `id`, and have no `None`
values. Key fields cannot also occur in the values. To change a key field, use
`update_or_insert({"email": "old@example.com"}, email="new@example.com")`.
A call with only a key returns an existing row unchanged, without after-hooks,
or inserts a new row using defaults.

PostgreSQL uses atomic `INSERT ... ON CONFLICT ... RETURNING`. A key without a
matching unique constraint or index raises `UpsertKeyError`. SQLite, MySQL, and
tables with a common filter use a Python lookup followed by insert or update.
This path respects the common filter, does not inspect indexes, and raises
`UpsertAmbiguityError` if the key matches multiple visible rows. It is not atomic
under concurrent writes, so database unique constraints remain recommended.
Conflicts on a different unique column raise the database's normal integrity error.
PostgreSQL checks the proposed insert's constraints before resolving the conflict;
provide required insertion values even when you expect to update an existing row.
The key-only existing-row path avoids attempting that insertion.
It also skips PostgreSQL's conflict-target validation because it executes no write.

Before-hooks never run on either path because the branch is unknown beforehand.
After-hooks run for the branch that occurred: `after_insert(row, id)` or
`after_update(affected_set, row)`. The hook row is PyDAL's operation row, and the
update set is restricted to the affected ID, ignoring common filters so the hook
can still access a row moved outside its filter.

Register a before-hook's upsert policy explicitly:

```python
User.before_insert(validate_user, upsert="error")  # Require update_or_insert instead.
User.before_update(normalize_user, upsert="ignore")  # Skip silently during upsert.
```

An unmarked before-hook emits `UpsertHooksWarning` and is skipped, including mixin
hooks for slugs and timestamps. An `"error"` registration raises `UpsertHookError`
before executing SQL. Policies belong to each model registration; they do not
change ordinary inserts, updates, or `update_or_insert`.

Inserts apply field defaults and computations. Upsert updates write only explicitly
supplied values: omitted `Field(update=...)` and `compute` fields stay unchanged.
Supply these values explicitly if they need to change. TypeDAL cache invalidation
runs through the normal after-hooks.

### Affected-ID update hooks since 6.0

Updates through TypeDAL's query builders, `db(query).update(...)`, and record updates
pass `Set(id.belongs(affected_ids))` to after-update hooks instead of the original
query. Hooks receive an `AffectedSet` whose `affected_ids` exposes the IDs already
captured by the write, including for cache invalidation. PostgreSQL and supported
SQLite versions obtain IDs with `UPDATE ... RETURNING`.
MySQL selects IDs before updating without locking; concurrent changes can make that
selected list differ from the rows actually updated.
Raw updates with no after-hooks use ordinary rowcount without collecting IDs.
QueryBuilder updates always collect IDs for their return value. Large affected-ID
lists currently stay in memory; there is no threshold or temporary-table strategy.

PyDAL's reverse-reference LazySets and `db.smart_query(...)` construct plain Sets
and retain PyDAL's original-query after-hook semantics. Their hooks receive a plain
`Set` without `affected_ids`; cache invalidation falls back to selecting IDs from
that query. Updates that change the query's predicate can therefore miss cache
invalidation on these PyDAL paths.

### Validation and general update-or-insert

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

## TypeScript schema generation

TypeDAL can generate TypeScript types from your models.

Install the optional dependency first:

```bash
uv pip install TypeDAL[typescript]  # or typedal[all]
```

### From Python APIs

Generate schema for one model:

```python
ts = User.as_typescript()
print(ts)
```

Generate schema for all currently defined models on a database instance:

```python
ts = db.as_typescript()
print(ts)
```

Generate schema for a subset of models:

```python
ts = db.as_typescript("User", "Post")
# or:
ts = db.as_typescript(User, Post)
```

### From the CLI

Generate TypeScript from your configured table definitions:

```bash
typedal typescript.generate
```

Useful variants:

```bash
typedal typescript.generate path/to/models.py
typedal typescript.generate --tables User --tables Post
typedal typescript.generate --output-file src/types/typedal.ts
```

Configuration details for `typescript.generate` (including `typescript_output`) are documented in
[7. Configuration](./7_configuration.md).

---

Want the ORM without blocking the event loop?
Continue with [11. Async](./11_async.md).
