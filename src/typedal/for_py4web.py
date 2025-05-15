"""
ONLY USE IN COMBINATION WITH PY4WEB!
"""

import typing

import threadsafevariable
from py4web.core import ICECUBE
from py4web.core import Fixture as _Fixture
from pydal.base import MetaDAL, hashlib_md5

from .core import TypeDAL
from .types import AnyDict
from .web2py_py4web_shared import AuthUser


class Fixture(_Fixture):  # type: ignore
    """
    Make mypy happy.
    """


class PY4WEB_DAL_SINGLETON(MetaDAL):
    _instances: typing.ClassVar[typing.MutableMapping[str, TypeDAL]] = {}

    def __call__(cls, uri: typing.Optional[str] = None, *args: typing.Any, **kwargs: typing.Any) -> TypeDAL:
        db_uid = kwargs.get("db_uid", hashlib_md5(repr(uri or (args, kwargs))).hexdigest())
        if db_uid not in cls._instances:
            cls._instances[db_uid] = super().__call__(uri, *args, **kwargs)

        return cls._instances[db_uid]

    def _clear(cls) -> None:
        cls._instances.clear()


class DAL(TypeDAL, Fixture, metaclass=PY4WEB_DAL_SINGLETON):  # pragma: no cover
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
    "DAL",
    "AuthUser",
    "Fixture",
    "setup_py4web_tables",
]
