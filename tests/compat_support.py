"""
Shared pieces for the compatibility tests that pin baseline behaviour before the performance work.
"""

import typing as t
from pathlib import Path

import pytest
from pydal.objects import Row, Rows

from src.typedal import TypeDAL


@pytest.fixture(params=["sqlite", "postgres"])
def compat_uri(request: pytest.FixtureRequest, tmp_path: Path) -> str:
    """Connection string for each backend the compatibility tests run on."""
    if request.param == "sqlite":
        return f"sqlite://{tmp_path / 'compat.db'}"
    return t.cast(str, request.getfixturevalue("dal_psql_uri"))


def is_postgres(db: t.Any) -> bool:
    """True when `db` talks to PostgreSQL."""
    return db._dbname == "postgres"


def make_db(uri: str, folder: Path, cls: type[TypeDAL] = TypeDAL, **kwargs: t.Any) -> t.Any:
    """A TypeDAL without config files, so the tests only depend on their own arguments."""
    kwargs.setdefault("migrate", True)
    return cls(uri, folder=str(folder), use_pyproject=False, use_env=False, attempts=1, **kwargs)


def drop_all(db: t.Any) -> None:
    """Drop every table `db` defined (PostgreSQL keeps them between tests in a module), then close it."""
    try:
        db.rollback()
        if is_postgres(db):
            for name in reversed(list(db.tables)):
                db[name].drop("cascade")
            db.commit()
    finally:
        db.close()


def norm(value: t.Any) -> t.Any:
    """Reduce a parsed value to plain data that two databases can be compared on, keeping types and key order."""
    if isinstance(value, Rows):
        return {
            "type": type(value).__name__,
            "colnames": list(value.colnames),
            "compact": value.compact,
            "records": [norm(r) for r in value.records],
            "iterated": [norm(r) for r in value],
        }
    if isinstance(value, Row):
        return [type(value).__name__, [(k, norm(v)) for k, v in value.__dict__.items()]]
    if isinstance(value, dict):
        return [type(value).__name__, [(k, norm(v)) for k, v in value.items()]]
    if isinstance(value, (list, tuple)):
        return [type(value).__name__, [norm(v) for v in value]]
    if callable(value) or hasattr(value, "query"):
        # RecordUpdater, LazySet, Method fields and so on: compared by type here, exercised by their own tests
        return ("<callable>", type(value).__name__)
    return (type(value).__name__, repr(value))
