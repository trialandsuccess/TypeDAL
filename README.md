# TypeDAL

Typing support for [PyDAL](http://web2py.com/books/default/chapter/29/6).
This package aims to improve the typing support for PyDAL. By using classes instead of the define_table method,
type hinting the result of queries can improve the experience while developing. In the background, the queries are still
generated and executed by pydal itself, this package only proves some logic to properly pass calls from class methods to
the underlying `db.define_table` pydal Tables.

- `TypeDAL` is the replacement class for DAL that manages the code on top of DAL.
- `TypedTable` must be the parent class of any custom Tables you define (e.g. `class SomeTable(TypedTable)`)
- `TypedField` can be used instead of Python native types when extra settings (such as default) are required (
  e.g. `name: TypedField(str, default="John Doe")`)
- `TypedRows`: can be used as the return type of .select() and subscribed with the actual table class, so
  e.g. `rows: TypedRows[SomeTable]`. If you're lazy, `list[SomeTable]` works fine too but that misses hinting
  possibilities such as `.first()`.

### Translations from pydal to typedal

<table>
<tr>
<td>Description</td>
<td> pydal </td> <td> pydal alternative </td> <td> typedal </td> <td> typedal alternative(s) </td> <td> ... </td>
</tr>
<tr>
<tr>
<td>Setup</td>
<td>

```python
from pydal import DAL, Field

db = DAL(...)
```

</td>

<td></td>
<td>

```python
from typedal import TypeDAL, TypedTable, TypedField, TypedRows
from typedal.fields import TextField
from typing import Optional

db = TypeDAL(...)
```

</td>

</tr>
<tr>
<td>Table Definitions</td>
<td>

```python
db.define_table("table_name",
                Field("fieldname", "string", required=True),
                Field("otherfield", "float"),
                Field("yet_another", "text", default="Something")
                )
```

</td>
<td>
</td>

<td>

```python
@db.define
class TableName(TypedTable):
    fieldname: str
    otherfield: float | None
    yet_another: TypedField(str, type="text", default="something", required=False)
```

</td>

<td>

```python
import typing


class TableName(TypedTable):
    fieldname: str
    otherfield: typing.Optional[float]
    yet_another: TextField(default="something", required=False)


db.define(TableName)
```

</td>
</tr>

<tr>
<td>Insert</td>

<td>

```python
db.table_name.insert(fieldname="value")
```

</td>

<td></td>

<td>

```python
db.table_name.insert(fieldname="value")
```

<td>

```python
TableName.insert(fieldname="value")
```

</td>
</tr>

<tr>
<td>(quick) Select</td>


<td>

```python
rows = db(db.table_name).select()  # -> Any (Rows)
row = db.table_name(id=1)  # -> Any (Row)
```

</td>

<td></td>

<td>

```python
rows: TypedRows[TableName] = db(db.table_name).select()  # -> TypedRows[TableName]
row: TableName = db.table_name(id=1)  # -> TableName
```

<td>

```python
rows: TypedRows[TableName] = db(TableName).select()  # -> TypedRows[TableName]
row = TableName(id=1)  # -> TableName
```

</td>


</tr>

</table>


<!-- 
<td>

```python

```

</td>

<td></td>

<td>

<td>

```python

```

</td>
</tr>
-->

### All Types

| pydal                                     | typedal (native python type) | typedal (using TypedField)               | typedal (using specific Field)      |
|-------------------------------------------|------------------------------|------------------------------------------|-------------------------------------|
| `Field('name', 'string')`                 | `name: str`                  | `name: TypedField(str)`                  | `name: StringField()`               |
| `Field('name', 'text')`                   | ×                            | `name: TypedField(str, type="text")`     | `name: TextField()`                 |
| `Field('name', 'blob')`                   | `name: bytes`                | `name: TypedField(bytes)`                | `name: BlobField()`                 |
| `Field('name', 'boolean')`                | `name: bool`                 | `name: TypedField(bool)`                 | `name: BooleanField()`              |
| `Field('name', 'integer')`                | `name: int`                  | `name: TypedField(int)`                  | `name: IntegerField()`              |
| `Field('name', 'double')`                 | `name: float`                | `name: TypedField(float)`                | `name: DoubleField()`               |
| `Field('name', 'decimal(n,m)')`           | `name: decimal.Decimal`      | `name: TypedField(decimal.Decimal)`      | `name: DecimalField(n=n, m=m)`      |
| `Field('name', 'date')`                   | `name: datetime.date`        | `name: TypedField(datetime.date)`        | `name: DateField()`                 |
| `Field('name', 'time')`                   | `name: datetime.time`        | `name: TypedField(datetime.time)`        | `name: TimeField()`                 |
| `Field('name', 'datetime')`               | `name: datetime.datetime`    | `name: TypedField(datetime.datetime)`    | `name: DatetimeField()`             |
| `Field('name', 'password')`               | ×                            | `name: TypedField(str, type="password")` | `name: PasswordField()`             |
| `Field('name', 'upload')`                 | ×                            | `name: TypedField(str, type="upload)`    | `name: UploadField()`               |
| `Field('name', 'reference <table>')`      | `name: Table`                | `name: TypedField(Table)`                | `name: ReferenceField('table')`     |
| `Field('name', 'list:string')`            | `name: list[str]`            | `name: TypedField(list[str])`            | `name: ListStringField()`           |
| `Field('name', 'list:integer')`           | `name: list[int]`            | `name: TypedField()`                     | `name: ListIntegerField()`          |
| `Field('name', 'list:reference <table>')` | `name: list[Table]`          | `name: TypedField()`                     | `name: ListReferenceField('table')` |
| `Field('name', 'json')`                   | ×                            | `name: TypedField()`                     | `name: JSONField()`                 |
| `Field('name', 'bigint')`                 | ×                            | `name: TypedField()`                     | `name: BigintField()`               |
| `Field('name', 'big-id')`                 | ×                            | ×                                        | ×                                   |
| `Field('name', 'big-reference')`          | ×                            | ×                                        | ×                                   |

### Making a field required/optional

| pydal                                    | typedal (native python type)                                              | typedal (using TypedField)                           | typedal (using specific Field)      |
|------------------------------------------|---------------------------------------------------------------------------|------------------------------------------------------|-------------------------------------|
| `Field('name', 'string', required=True)` | `name: str`                                                               | `name: TypedField(str, required=True)`               | `name: StringField(required=True)`  |
| `Field('name', 'text', required=False)`  | `name: typing.Optional[str]` or  <br/> <code>name: str &#124; None</code> | `name: TypedField(str, type="text", required=False)` | `name: StringField(required=False)` |

## Caveats

- This package depends heavily on the current implementation of annotations (which are computed when the class is
  defined). PEP 563 (Postponed Evaluation of Annotations, accepted) aims to change this behavior (
  and `from __future__ import annotations` already does) in a way that this module currently can not handle: all
  annotations are converted to string representations. This makes it very hard to re-evaluate the annotation into the
  original type, since the variable scope is lost (and thus references to variables or other classes are ambiguous or
  simply impossible to find).
- `TypedField` limitations; Since pydal implements some magic methods to perform queries, some features of typing will
  not work on a typed field: `typing.Optional` or a union (`Field() | None`) will result in errors. The only way to make
  a typedfield optional right now, would be to set `required=False` as an argument yourself. This is also a reason
  why `typing.get_type_hints` is not a solution for the first caveat.