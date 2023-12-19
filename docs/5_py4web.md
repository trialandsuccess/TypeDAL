# 5. py4web and web2py

This library also has some py4web/web2py-specific enhancements.

## py4web

```python
# common.py
from typedal.for_py4web import DAL

db = DAL(
    settings.DB_URI,
    ...
)
```

This version of the `DAL` is also a py4web Fixture that manages database connections `on_request`, just as py4web's own
DAL Fixture does.

## Auth User (py4web)

```python
# models.py
from typedal.for_py4web import setup_py4web_tables, AuthUser as _AuthUser
from .common import db


# you can now customize auth user:

class AuthUser(_AuthUser):
    bookmarks = relationship(list["Bookmark"], ...)


db.define(AuthUser, redefine=True)

# or if you don't want to customize auth user:
setup_py4web_tables(db)

```

TypeDAL also provides an `AuthUser` class based on `db.auth_user`.
You can extend this class to add for example relationships.

## Auth User and other Auth tables (web2py)

Similarly, there are TypeDAL models for the builtin web2py auth tables:

```python
# models.py
from typedal.for_web2py import setup_web2py_tables, AuthUser as _AuthUser
from .common import db


# you can now customize auth user:

class AuthUser(_AuthUser):
    bookmarks = relationship(list["Bookmark"], ...)


db.define(AuthUser, redefine=True)

# or if you don't want to customize auth user:
setup_web2py_tables(db)
# this will also set up AuthGroup, AuthMembership, AuthPermission and AuthEvent
```

The AuthUser table is shared between `for_py4web` and `for_web2py` so you can use the same database table for both!
