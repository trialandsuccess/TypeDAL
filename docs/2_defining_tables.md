# 2. Defining Tables

The syntax for creating a table is very different, but built on the same principles:

```python
from pydal import Field

# pydal:
db.define_table('my_table', Field('my_field'))
```

```python
from typedal import TypedTable, TypedField


# typedal:
@db.define()
class MyTable(TypedTable):
    my_field: TypedField[str]  # or just `my_field: str`
```

In this example, pydal's `Field('my_field')` implicitly sets the type to 'string'.
The TypeDAL variant uses Python type annotations and maps these to the right database types.  
The `TypedField[]` annotation is not necessary (at runtime), but improves type hinting and IDE support.

Any keyword arguments you would pass to `db.define_table`, you can also pass to `db.define()`.

### All Types

| pydal                                     | typedal (native python type) | typedal (using TypedField annotations) | typedal (using TypedField)                | typedal (using specific Field)       |
|-------------------------------------------|------------------------------|----------------------------------------|-------------------------------------------|--------------------------------------|
| `Field('name', 'string')`                 | `name: str`                  | `name: TypedField[str]`                | `name = TypedField(str)`                  | `name = StringField()`               |
| `Field('name', 'text')`                   | ×                            | ×                                      | `name = TypedField(str, type="text")`     | `name = TextField()`                 |
| `Field('name', 'blob')`                   | `name: bytes`                | `name: TypedField[bytes]`              | `name = TypedField(bytes)`                | `name = BlobField()`                 |
| `Field('name', 'boolean')`                | `name: bool`                 | `name: TypedField[bool]`               | `name = TypedField(bool)`                 | `name = BooleanField()`              |
| `Field('name', 'integer')`                | `name: int`                  | `name: TypedField[int]`                | `name = TypedField(int)`                  | `name = IntegerField()`              |
| `Field('name', 'double')`                 | `name: float`                | `name: TypedField[float]`              | `name = TypedField(float)`                | `name = DoubleField()`               |
| `Field('name', 'decimal(n,m)')`           | `name: decimal.Decimal`      | `name: TypedField[decimal.Decimal]`    | `name = TypedField(decimal.Decimal)`      | `name = DecimalField(n=n, m=m)`      |
| `Field('name', 'date')`                   | `name: datetime.date`        | `name: TypedField[datetime.date]`      | `name = TypedField(datetime.date)`        | `name = DateField()`                 |
| `Field('name', 'time')`                   | `name: datetime.time`        | `name: TypedField[datetime.time]`      | `name = TypedField(datetime.time)`        | `name = TimeField()`                 |
| `Field('name', 'datetime')`               | `name: datetime.datetime`    | `name: TypedField[datetime.datetime]`  | `name = TypedField(datetime.datetime)`    | `name = DatetimeField()`             |
| `Field('name', 'password')`               | ×                            | ×                                      | `name = TypedField(str, type="password")` | `name = PasswordField()`             |
| `Field('name', 'upload')`                 | ×                            | ×                                      | `name = TypedField(str, type="upload)`    | `name = UploadField()`               |
| `Field('name', 'reference <table>')`      | `name: Table`                | `name: TypedField[Table]`              | `name = TypedField(Table)`                | `name = ReferenceField('table')`     |
| `Field('name', 'list:string')`            | `name: list[str]`            | `name: TypedField[list[str]]`          | `name = TypedField(list[str])`            | `name = ListStringField()`           |
| `Field('name', 'list:integer')`           | `name: list[int]`            | `name: TypedField[list[int]]`          | `name = TypedField()`                     | `name = ListIntegerField()`          |
| `Field('name', 'list:reference <table>')` | `name: list[Table]`          | `name: TypedField[list[Table]]`        | `name = TypedField()`                     | `name = ListReferenceField('table')` |
| `Field('name', 'json')`                   | ×                            | ×                                      | `name = TypedField()`                     | `name = JSONField()`                 |
| `Field('name', 'bigint')`                 | ×                            | ×                                      | `name = TypedField()`                     | `name = BigintField()`               |
| `Field('name', 'big-id')`                 | ×                            | ×                                      | ×                                         | ×                                    |
| `Field('name', 'big-reference')`          | ×                            | ×                                      | ×                                         | ×                                    |

### Making a field required/optional

| pydal                                    | typedal (native python type)                                              | typedal (using TypedField annotation)                                                             | typedal (using TypedField)                            | typedal (using specific Field)       |
|------------------------------------------|---------------------------------------------------------------------------|---------------------------------------------------------------------------------------------------|-------------------------------------------------------|--------------------------------------|
| `Field('name', 'string', required=True)` | `name: str`                                                               | `name: TypedField[str]`                                                                           | `name = TypedField(str, required=True)`               | `name = StringField(required=True)`  |
| `Field('name', 'text', required=False)`  | `name: typing.Optional[str]` or  <br/> <code>name: str &#124; None</code> | `name: TypedField[typing.Optional[str]]` or  <br/> <code>name: TypedField[str &#124; None]</code> | `name = TypedField(str, type="text", required=False)` | `name = StringField(required=False)` |
