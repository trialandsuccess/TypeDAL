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

TypeDAL accepts the same connection string format and other arguments as `pydal.DAL`. Again,
see [their documentation](http://www.web2py.com/books/default/chapter/29/06/the-database-abstraction-layer#The-DAL-A-quick-tour)
for more info about this.

When using py4web, it is recommended to import the py4web-specific TypeDAL, which is a Fixture that handles database
connections on request (just like the py4web specific DAL class does).
