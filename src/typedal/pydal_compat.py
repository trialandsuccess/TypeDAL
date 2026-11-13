"""
Imports from PyDAL internals whose location depends on the installed PyDAL version.

PyDAL 20261107.0 merged the `adapters`, `dialects`, `parsers` and `representers` packages into
`pydal.backend_base` (the shared base classes) and `pydal.backends` (one module per database), and renamed
`PostgreDialect` to `PostgresDialect`. The classes themselves kept their behavior, so TypeDAL only needs to know
where to import them from. Everything else TypeDAL uses from PyDAL kept its location.
"""

import typing as t
from collections.abc import Generator
from contextlib import contextmanager
from importlib.util import find_spec

# True on PyDAL >= 20261107.0; False on the older `pydal.adapters` / `pydal.dialects` layout.
PYDAL_BACKENDS_LAYOUT = find_spec("pydal.backend_base") is not None

if t.TYPE_CHECKING:
    # Type checkers always see the older layout (and Unknown where it isn't installed): the newer one annotates its
    # registry decorators as returning `type`, which erases the registered dialect classes.
    from pydal.adapters.base import BaseAdapter, SQLAdapter  # ty: ignore[unresolved-import]
    from pydal.dialects.base import SQLDialect  # ty: ignore[unresolved-import]
    from pydal.dialects.postgre import PostgreDialect as PostgresDialect  # ty: ignore[unresolved-import]
    from pydal.dialects.sqlite import SQLiteDialect  # ty: ignore[unresolved-import]
# Only one of these branches runs in a given environment; CI covers both by testing against both PyDAL layouts.
elif PYDAL_BACKENDS_LAYOUT:  # pragma: no cover
    from pydal.backend_base import BaseAdapter, SQLAdapter, SQLDialect
    from pydal.backends.postgres import PostgresDialect
    from pydal.backends.sqlite import SQLiteDialect
else:  # pragma: no cover
    from pydal.adapters.base import BaseAdapter, SQLAdapter
    from pydal.dialects.base import SQLDialect
    from pydal.dialects.postgre import PostgreDialect as PostgresDialect
    from pydal.dialects.sqlite import SQLiteDialect


def bound_parameters(sql: str) -> tuple[tuple[object, ...], ...]:
    """
    The extra `adapter.execute` arguments needed to run `sql`, or SQL derived from it by string operations.

    Since 20261107.0, PyDAL's SQLite and PostgreSQL compilers return a `ParamSQL`: a `str` with placeholders whose
    values ride along on `.params`. Any string operation (appending a RETURNING clause, for example) returns a plain
    `str` without them. Older PyDAL inlines all values, so there is nothing to pass on.
    """
    params = tuple(getattr(sql, "params", ()))
    return (params,) if params else ()


@contextmanager
def inline_sql(adapter: BaseAdapter) -> Generator[None]:
    """
    Make PyDAL render values into the SQL instead of binding them, for SQL that is returned as a string.

    Newer PyDAL binds values as parameters on SQLite and PostgreSQL, which turns `_insert()`, `_update()` and
    friends into SQL with `?` or `$1` placeholders and no values. PyDAL's own `Set._select` switches its compiler to
    inline mode in the same way. Older PyDAL always inlines.
    """
    compiler = getattr(adapter, "compiler", None)
    saved = getattr(compiler, "parameterize", False)
    if compiler is not None:  # pragma: no cover - only on newer PyDAL
        compiler.parameterize = False
    try:
        yield
    finally:
        if compiler is not None:  # pragma: no cover - only on newer PyDAL
            compiler.parameterize = saved


__all__ = [
    "PYDAL_BACKENDS_LAYOUT",
    "BaseAdapter",
    "PostgresDialect",
    "SQLAdapter",
    "SQLDialect",
    "SQLiteDialect",
    "bound_parameters",
    "inline_sql",
]
