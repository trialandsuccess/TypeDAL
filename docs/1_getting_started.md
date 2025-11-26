# 1. Getting Started

TypeDAL is built on top of pyDAL, which has excellent documentation. It is recommended to be familiar with at least the
basics of this abstraction, before using this library. TypeDAL uses many of the same concepts, but with slightly
different wording and syntax in some cases.

[web2py - Chapter 6: The database abstraction layer](http://www.web2py.com/books/default/chapter/29/06/the-database-abstraction-layer)

---

### Installation

```shell
pip install typedal
# or, if you're using py4web:
pip install typedal[py4web]
```

### First Steps

```python
from typedal import TypeDAL
# or, if in py4web:
from typedal.for_py4web import TypeDAL

db = TypeDAL("sqlite:memory")
```

TypeDAL accepts the same connection string format and other arguments as `pydal.DAL`.
Again, see
[their documentation](http://www.web2py.com/books/default/chapter/29/06/the-database-abstraction-layer#The-DAL-A-quick-tour)
for more info about this. For additional configuration options specific to TypeDAL, see the [7. Advanced Configuration](./7_configuration.md) page.

When using py4web, it is recommended to import the py4web-specific TypeDAL, which is a Fixture that handles database
connections on request (just like the py4web specific DAL class does). More information about this can be found on [5. py4web](./5_py4web.md)

### Simple Queries

For direct SQL access, use `executesql()`:

```python
rows = db.executesql("SELECT * FROM some_table")
```

#### Safely Injecting Variables

Use t-strings (Python 3.14+) for automatic SQL escaping:

```python
name = "Robert'); DROP TABLE Students;--"
rows = db.executesql(t"SELECT * FROM some_table WHERE name = {name}")
```

Or use the `placeholders` argument with positional or named parameters:

```python
# Positional
rows = db.executesql(
    "SELECT * FROM some_table WHERE name = %s AND age > %s",
    placeholders=[name, 18]
)

# Named
rows = db.executesql(
    "SELECT * FROM some_table WHERE name = %(name)s AND age > %(age)s",
    placeholders={"name": name, "age": 18}
)
```

#### Result Formatting

By default, `executesql()` returns rows as tuples. To map results to specific fields, use `fields` (takes
Field/TypedField objects) or `colnames` (takes column name strings):

```python
rows = db.executesql(
    "SELECT id, name FROM some_table",
    colnames=["id", "name"]
)

rows = db.executesql(
    "SELECT id, name FROM some_table",
    fields=[some_table.id, some_table.name]  # Requires table definition
)
```

You can also use `as_dict` or `as_ordered_dict` to return dictionaries instead of tuples.

Most of the time, you probably don't want to write raw queries. For that, you'll need to define some tables!
Head to [2. Defining Tables](./2_defining_tables.md) to learn how.

