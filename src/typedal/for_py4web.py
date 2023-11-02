"""
ONLY USE IN COMBINATION WITH PY4WEB!
"""
from datetime import datetime
from typing import Any, Optional

import json_fix  # noqa: F401
import threadsafevariable
from py4web.core import ICECUBE, Fixture

from .core import TypeDAL, TypedTable


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

    email: str
    password: str
    first_name: Optional[str]
    last_name: Optional[str]
    sso_id: Optional[str]
    action_token: Optional[str]
    last_password_change: Optional[datetime]
    # past_passwords_hash: Optional[str]
    # username: Optional[str]
    # phone_number: Optional[str]
