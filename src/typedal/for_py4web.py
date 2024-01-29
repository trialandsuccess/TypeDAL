"""
ONLY USE IN COMBINATION WITH PY4WEB!
"""

import threadsafevariable
from py4web.core import ICECUBE
from py4web.core import Fixture as _Fixture

from .core import TypeDAL
from .types import AnyDict
from .web2py_py4web_shared import AuthUser


class Fixture(_Fixture):  # type: ignore
    """
    Make mypy happy.
    """


class DAL(TypeDAL, Fixture):  # pragma: no cover
    """
    Fixture similar to the py4web pydal fixture, but for typedal.
    """

    def on_request(self, _: AnyDict) -> None:
        """
        Make sure there is a database connection when a request comes in.
        """
        self.get_connection_from_pool_or_new()
        threadsafevariable.ThreadSafeVariable.restore(ICECUBE)

    def on_error(self, _: AnyDict) -> None:
        """
        Rollback db on error.
        """
        self.recycle_connection_in_pool_or_close("rollback")

    def on_success(self, _: AnyDict) -> None:
        """
        Commit db on success.
        """
        self.recycle_connection_in_pool_or_close("commit")


def setup_py4web_tables(db: TypeDAL) -> None:
    """
    Setup all the (default) required auth table.
    """
    db.define(AuthUser, migrate=False, redefine=True)


__all__ = [
    "AuthUser",
    "Fixture",
    "DAL",
    "setup_py4web_tables",
]
