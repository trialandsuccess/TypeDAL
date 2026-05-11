# Basics

note that mypy scopes classes like typedal.tables.TypedTable or test_snippet.WithBraces

```python only=mypy
from typedal import TypeDAL, TypedTable, TypedField, relationship, Ref, Relationship

db = TypeDAL()

reveal_type(db)  # revealed: typedal.core.TypeDAL

@db.define()
class WithBraces(TypedTable):
    gid = TypedField(str)

with_braces = WithBraces.first_or_fail()

reveal_type(with_braces) # revealed: test_snippet.WithBraces

@db.define
class WithoutBraces(TypedTable):
    with_braces = relationship(WithBraces, condition=lambda self, other: self.id == other.id, join="inner")
    deferred = relationship(Ref["DeferredDefine"], on=lambda self, other: [other.on(other.id == self.id)], join="left")
    multiple = relationship(list["WithoutBraces"], condition=lambda self, other: self.id != other.id)

without_braces = WithoutBraces.where().first()

reveal_type(without_braces) # revealed: test_snippet.WithoutBraces | None


class DeferredDefine(TypedTable):
    ...

db.define(DeferredDefine)

for deferred_define in DeferredDefine.paginate(limit=1):
    reveal_type(deferred_define) # revealed: test_snippet.DeferredDefine

new_row = WithBraces.insert()

reveal_type(new_row) # revealed: test_snippet.WithBraces
reveal_type(new_row.id) # revealed: int

reveal_type(WithBraces.gid) # revealed: typedal.fields.TypedField[str]
reveal_type(new_row.gid) # revealed: str

db.commit()

joined = WithoutBraces.join().first_or_fail()

reveal_type(WithoutBraces.with_braces) # revealed: typedal.relationships.Relationship[test_snippet.WithBraces]
reveal_type(WithoutBraces.deferred) # revealed: typedal.relationships.Relationship[test_snippet.DeferredDefine]
reveal_type(WithoutBraces.multiple) # revealed: typedal.relationships.Relationship[test_snippet.WithoutBraces]

reveal_type(joined.with_braces) # revealed: test_snippet.WithBraces
reveal_type(joined.deferred) # revealed: test_snippet.DeferredDefine | None
reveal_type(joined.multiple) # revealed: list[test_snippet.WithoutBraces]

```

# 1. Field Dual Behavior (Class vs Instance)

```python only=mypy
from typedal import TypeDAL, TypedTable, TypedField

db = TypeDAL()

@db.define
class MyTable(TypedTable):
    fancy = TypedField(str)

reveal_type(MyTable.fancy.lower())  # revealed: typedal.types.Expression
reveal_type(MyTable().fancy.lower())  # revealed: str
```

# 2. Alias Roundtrip Typing

```python only=mypy
from typedal import TypeDAL, TypedTable

db = TypeDAL()

@db.define
class MyTable(TypedTable):
    ...

aliased_cls = MyTable.with_alias("---")
reveal_type(aliased_cls)  # revealed: type[test_snippet.MyTable]

aliased_instance = aliased_cls()
reveal_type(aliased_instance)  # revealed: test_snippet.MyTable
```

# 3. Query Object Typing and Compatibility

```python only=mypy
from typedal import TypeDAL, TypedTable

db = TypeDAL()

@db.define
class MyTable(TypedTable):
    ...

my_query = MyTable.id > 3
reveal_type(my_query)  # revealed: typedal.types.Query

query = MyTable.id == 3

reveal_type(query) # revealed: typedal.types.Query

new = MyTable.update(query)
reveal_type(new)  # revealed: test_snippet.MyTable | None

MyTable.update_or_insert(MyTable)
MyTable.update_or_insert(my_query)
MyTable.update_or_insert(db.my_table.id > 3)
```

# 4. TypedRows Inference Behavior

```python only=mypy
from typedal import TypeDAL, TypedRows, TypedTable

db = TypeDAL()

@db.define
class MyTable(TypedTable):
    ...

select1 = db(MyTable).select()  # error: [var-annotated]
select2: TypedRows[MyTable] = db(MyTable).select()
select3 = MyTable.select().collect()

reveal_type(select1)  # revealed: typedal.rows.TypedRows[Any]
reveal_type(select2)  # revealed: typedal.rows.TypedRows[test_snippet.MyTable]
reveal_type(select3)  # revealed: typedal.rows.TypedRows[test_snippet.MyTable]

reveal_type(select1.first())  # revealed: Any | None
reveal_type(select2.first())  # revealed: test_snippet.MyTable | None
reveal_type(select3.first())  # revealed: test_snippet.MyTable | None

for row in select2:
    reveal_type(row)  # revealed: test_snippet.MyTable

for row in MyTable.select():
    reveal_type(row)  # revealed: test_snippet.MyTable
```

# 5. where().column(...) Overloads

```python only=mypy
import typing
from typedal import TypeDAL, TypedField, TypedTable

db = TypeDAL()

@db.define
class MyTable(TypedTable):
    normal: str
    fancy = TypedField(str)

SomeField: typing.Any = ...

reveal_type(MyTable.where().column(SomeField))  # revealed: list[Any]
reveal_type(MyTable.where().column(MyTable.normal))  # revealed: list[str]
reveal_type(MyTable.where().column(MyTable.fancy))  # revealed: list[str]
```

# 6. rows.render() Overloads

```python only=mypy
from typedal import TypeDAL, TypedTable

db = TypeDAL()

@db.define
class MyTable(TypedTable):
    ...

rows = MyTable.where().collect()
reveal_type(rows.render())  # revealed: typing.Generator[test_snippet.MyTable, None, None]
reveal_type(rows.render(1))  # revealed: test_snippet.MyTable
```

# 7. Mixin-as-Table Type Argument

```python only=mypy
from typedal import TypeDAL, TypedTable
from typedal.mixins import Mixin

db = TypeDAL()

class SearchMixin(Mixin):
    ...

@db.define
class SearchableTable(TypedTable, SearchMixin):
    title: str

def using_mixin(table: type[SearchMixin]) -> None:
    reveal_type(table.where())  # revealed: typedal.query_builder.QueryBuilder[test_snippet.SearchMixin]

using_mixin(SearchableTable)
```

# 8. Cache Tuple Protocol Checks

```python only=mypy
import typing
from typedal.types import CacheFn, CacheTuple, Rows

def cache_model(key: str, fn: CacheFn, expire: int) -> Rows:
    return fn()

cache_valid: CacheTuple = (cache_model, 3000)

def invalid_cache_model(key: str, fn: typing.Callable[..., list[str]], _: typing.Optional[int] = None) -> list[str]:
    return fn()

cache_invalid: CacheTuple = (invalid_cache_model, 3000)  # error: [assignment]
```

# Parity with test_mypy.py

There are still a few tests missing in this file:

- Hook callback typing parity:
  - `_after_insert` list type behavior
  - accepted callbacks: `(Any, Reference)`, `(MyTable, Reference)`, `(OpRow, Reference)`
  - rejected callback: `(str, Reference)` on both `.append(...)` and `MyTable.after_insert(...)`

- Instance update flow:
  - `inst = MyTable(3)` reveal
  - guarded `inst._update()` reveal
  - `inst.update_record()` reveal

- Old-style table count paths:
  - `db(db.old_style).count()`
  - `db(old_style).count()`

- Raw DAL entrypoints:
  - `db(MyTable.id > 0)`
  - `db(db.old_style.id > 3)`
  - `db(MyTable)`
  - `db(db.old_style)`

- Field/type reveals not explicitly repeated in new sections:
  - `MyTable.normal` and `MyTable().normal`
  - `MyTable.options` and `MyTable().options`
