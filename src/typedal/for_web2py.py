"""
ONLY USE IN COMBINATION WITH WEB2PY!
"""

import datetime as dt

from pydal.validators import IS_NOT_IN_DB

from .core import TypeDAL, TypedField, TypedTable
from .fields import TextField
from .web2py_py4web_shared import AuthUser

DAL = TypeDAL  # export as DAL for compatibility with py4web


class AuthGroup(TypedTable):
    """
    Model for w2p_auth_group.
    """

    role: TypedField[str | None]
    description = TextField(notnull=False)

    @classmethod
    def __on_define__(cls, db: TypeDAL) -> None:
        """
        When we have access to 'db', set the NOT IN DB requirement to make the role unique.
        """
        super().__on_define__(db)

        cls.role.requires = IS_NOT_IN_DB(db, "w2p_auth_group.role")


class AuthMembership(TypedTable):
    """
    Model for w2p_auth_membership.
    """

    user_id: TypedField[AuthUser]
    group_id: TypedField[AuthGroup]


class AuthPermission(TypedTable):
    """
    Model for w2p_auth_permission.
    """

    group_id: TypedField[AuthGroup]
    name: TypedField[str]
    table_name: TypedField[str]
    record_id: TypedField[int]


class AuthEvent(TypedTable):
    """
    Model for w2p_auth_event.
    """

    time_stamp: TypedField[dt.datetime | None]
    client_ip: TypedField[str | None]
    user_id: TypedField[AuthUser | None]
    origin: TypedField[str | None]
    description = TextField(notnull=False)


def setup_web2py_tables(db: TypeDAL, migrate: bool = False) -> None:
    """
    Setup all the (default) web2py required tables.
    """
    db.define(AuthUser, redefine=True, migrate=migrate)
    db.define(AuthGroup, rname="w2p_auth_group", redefine=True, migrate=migrate)
    db.define(AuthMembership, rname="w2p_auth_membership", redefine=True, migrate=migrate)
    db.define(AuthPermission, rname="w2p_auth_permission", redefine=True, migrate=migrate)
    db.define(AuthEvent, rname="w2p_auth_event", redefine=True, migrate=migrate)
