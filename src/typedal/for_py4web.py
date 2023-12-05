"""
ONLY USE IN COMBINATION WITH PY4WEB!
"""
import typing
from datetime import datetime
from typing import Any, Optional

import json_fix  # noqa: F401
import threadsafevariable
from py4web.core import ICECUBE
from py4web.core import Fixture as _Fixture
from pydal.validators import CRYPT, IS_EMAIL, IS_NOT_EMPTY, IS_NOT_IN_DB, IS_STRONG

from .core import TypeDAL, TypedField, TypedTable
from .fields import PasswordField
from .types import Validator


class Fixture(_Fixture):  # type: ignore
    """
    Make mypy happy.
    """


class DAL(TypeDAL, Fixture):  # pragma: no cover
    """
    Fixture similar to the py4web pydal fixture, but for typedal.
    """

    def on_request(self, _: dict[str, Any]) -> None:
        """
        Make sure there is a database connection when a request comes in.
        """
        self.get_connection_from_pool_or_new()
        threadsafevariable.ThreadSafeVariable.restore(ICECUBE)

    def on_error(self, _: dict[str, Any]) -> None:
        """
        Rollback db on error.
        """
        self.recycle_connection_in_pool_or_close("rollback")

    def on_success(self, _: dict[str, Any]) -> None:
        """
        Commit db on success.
        """
        self.recycle_connection_in_pool_or_close("commit")


class AuthUser(TypedTable):
    """
    Class for db.auth_user in py4web (probably not w2p).
    """

    redefine = True
    migrate = False

    # call db.define on this when ready

    email: TypedField[str]
    password = PasswordField(requires=[IS_STRONG(entropy=45), CRYPT()])
    first_name: TypedField[Optional[str]]
    last_name: TypedField[Optional[str]]
    sso_id: TypedField[Optional[str]]
    action_token: TypedField[Optional[str]]
    last_password_change: TypedField[Optional[datetime]]

    # past_passwords_hash: Optional[str]
    # username: Optional[str]
    # phone_number: Optional[str]

    @classmethod
    def __on_define__(cls, db: TypeDAL) -> None:
        """
        Add some requires= to the auth_user fields.
        """
        cls.email.requires = typing.cast(tuple[Validator, ...], (IS_EMAIL(), IS_NOT_IN_DB(db, "auth_user.email")))
        cls.first_name.requires = IS_NOT_EMPTY()
        cls.last_name.requires = IS_NOT_EMPTY()
