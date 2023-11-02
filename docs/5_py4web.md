# 5. py4web

This library also has some py4web-specific enhancements.

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

```python
# models.py
from typedal.for_py4web import AuthUser as _AuthUser

from .common import db


class AuthUser(_AuthUser):
    redefine = True
    
    bookmarks = relationship(list["Bookmark"], ...)


db.define(AuthUser)
```

TypeDAL also provides an `AuthUser` class based on `db.auth_user`.
You can extend this class to add for example relationships.
