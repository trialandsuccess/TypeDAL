# TypeDAL

[![PyPI - Version](https://img.shields.io/pypi/v/TypeDAL.svg)](https://pypi.org/project/TypeDAL)
[![PyPI - Python Version](https://img.shields.io/pypi/pyversions/TypeDAL.svg)](https://pypi.org/project/TypeDAL)  
[![Code style: black](https://img.shields.io/badge/code%20style-black-000000.svg)](https://github.com/psf/black)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)  
[![su6 checks](https://github.com/trialandsuccess/TypeDAL/actions/workflows/su6.yml/badge.svg?branch=development)](https://github.com/trialandsuccess/TypeDAL/actions)
![coverage.svg](coverage.svg)

Typing support for [PyDAL](http://web2py.com/books/default/chapter/29/6).
This package aims to improve the typing support for PyDAL. By using classes instead of the define_table method,
type hinting the result of queries can improve the experience while developing. In the background, the queries are still
generated and executed by pydal itself, this package only provides some logic to properly pass calls from class methods to
the underlying `db.define_table` pydal Tables.

- `TypeDAL` is the replacement class for DAL that manages the code on top of DAL.
- `TypedTable` must be the parent class of any custom Tables you define (e.g. `class SomeTable(TypedTable)`)
- `TypedField` can be used instead of Python native types when extra settings (such as `default`) are required (
  e.g. `name = TypedField(str, default="John Doe")`). It can also be used in an annotation (`name: TypedField[str]`) to
  improve
  editor support over only annotating with `str`.
- `TypedRows`: can be used as the return type annotation of pydal's `.select()` and subscribed with the actual table
  class, so
  e.g. `rows: TypedRows[SomeTable] = db(...).select()`. When using the QueryBuilder, a `TypedRows` instance is returned
  by `.collect()`.

Version 2.0 also introduces more ORM-like funcionality.
Most notably, a Typed Query Builder that sees your table classes as models with relationships to each other.
See [3. Building Queries](https://typedal.readthedocs.io/en/stable/3_building_queries/) for more
details.

## CLI
The Typedal CLI provides a convenient interface for generating SQL migrations for [edwh-migrate](https://github.com/educationwarehouse/migrate#readme)
from PyDAL or TypeDAL configurations using [pydal2sql](https://github.com/robinvandernoord/pydal2sql). 
It offers various commands to streamline database management tasks.

### Usage

```bash
typedal --help
```

## Options

- `--show-config`: Toggle to show configuration details. Default is `no-show-config`.
- `--version`: Toggle to display version information. Default is `no-version`.
- `--install-completion`: Install completion for the current shell.
- `--show-completion`: Show completion for the current shell, for copying or customization.
- `--help`: Display help message and exit.

## Commands

- `cache.clear`: Clear expired items from the cache.
- `cache.stats`: Show caching statistics.
- `migrations.fake`: Mark one or more migrations as completed in the database without executing the SQL code.
- `migrations.generate`: Run `pydal2sql` based on the TypeDAL configuration.
- `migrations.run`: Run `edwh-migrate` based on the TypeDAL configuration.
- `setup`: Interactively setup a `[tool.typedal]` entry in the local `pyproject.toml`.

### Configuration

TypeDAL and its CLI can be configured via `pyproject.toml`.  
See [6. Migrations](https://typedal.readthedocs.io/en/stable/6_migrations/) for more information about configuration.


## TypeDAL for PyDAL users - Quick Overview

Below you'll find a quick overview of translation from pydal to TypeDAL.  
For more info, see **[the docs](https://typedal.readthedocs.io/en/latest/)**.

---

### Translations from pydal to typedal

<table>
<tr>
<td>Description</td>
<td> pydal </td> <td> typedal </td> <td> typedal alternative(s) </td> <td> ... </td>
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

<td>

```python
from typedal import TypeDAL, TypedTable, TypedField

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

```python
@db.define
class TableName(TypedTable):
    fieldname: str
    otherfield: float | None
    yet_another = TypedField(str, type="text", default="something", required=False)
```

</td>

<td>

```python
import typing


class TableName(TypedTable):
    fieldname: TypedField[str]
    otherfield: TypedField[typing.Optional[float]]
    yet_another = TextField(default="something", required=False)


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

<td>

```python
TableName.insert(fieldname="value")
```

<td>

```python
# the old syntax is also still supported:
db.table_name.insert(fieldname="value")
```

</td>
</tr>

<tr>
<td>(quick) Select</td>


<td>

```python
# all:
all_rows = db(db.table_name).select()  # -> Any (Rows)
# some:
rows = db((db.table_name.id > 5) & (db.table_name.id < 50)).select(db.table_name.id)
# one:
row = db.table_name(id=1)  # -> Any (Row)
```

</td>

<td>

```python
# all:
all_rows = TableName.collect()  # or .all()
# some:
# order of select and where is interchangable here
rows = TableName.select(Tablename.id).where(TableName.id > 5).where(TableName.id < 50).collect()
# one:
row = TableName(id=1)  # or .where(...).first()

```

<td>

```python
# you can also still use the old syntax and type hint on top of it;
# all:
all_rows: TypedRows[TableName] = db(db.table_name).select()
# some:
rows: TypedRows[TableName] = db((db.table_name.id > 5) & (db.table_name.id < 50)).select(db.table_name.id)
# one:
row: TableName = db.table_name(id=1)
```

</td>


</tr>

</table>


<!-- 
<td>

```python

```

</td>

<td>

<td>

```python

```

</td>
</tr>
-->

### All Types

See [2. Defining Tables](https://typedal.readthedocs.io/en/stable/2_defining_tables/)

### Helpers

TypeDAL provides some utility functions to interact with the underlying pyDAL objects:

- **`get_db(TableName)`**:  
  Retrieve the DAL instance associated with a given TypedTable or pyDAL Table.

- **`get_table(TableName)`**:  
  Access the original PyDAL Table from a TypedTable instance (`db.table_name`).

- **`get_field(TableName.fieldname)`**:  
  Get the pyDAL Field from a TypedField. This ensures compatibility when interacting directly with PyDAL.

These helpers are useful for scenarios where direct access to the PyDAL objects is needed while still using TypeDAL.
An example of this is when you need to do a `db.commit()` but you can't import `db` directly:

```python
from typedal.helpers import get_db #, get_table, get_field

MyTable.insert(...)
db = get_db(MyTable)
db.commit() # this is usually done automatically but sometimes you want to manually commit.
```

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
