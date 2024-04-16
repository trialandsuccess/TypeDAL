"""
Both py4web and web2py can share this Auth User table definition.
"""

import datetime as dt

from pydal.validators import CRYPT, IS_EMAIL, IS_NOT_EMPTY, IS_NOT_IN_DB, IS_STRONG

from .core import TypeDAL, TypedField, TypedTable
from .fields import PasswordField


class AuthUser(TypedTable):
    """
    Class for db.auth_user in py4web and web2py.
    """

    # call db.define with redefine=True and migrate=False on this when ready

    first_name = TypedField(str, requires=IS_NOT_EMPTY())
    last_name = TypedField(str, requires=IS_NOT_EMPTY())
    email = TypedField(str)
    password = PasswordField(requires=[IS_STRONG(entropy=45), CRYPT()])
    sso_id = TypedField(str, readable=False, writable=False, notnull=False)
    action_token = TypedField(str, readable=False, writable=False, notnull=False)
    last_password_change = TypedField(dt.datetime, default=dt.datetime.now, readable=False, writable=False)
    registration_key = TypedField(str, readable=False, writable=False, notnull=False)
    reset_password_key = TypedField(str, readable=False, writable=False, notnull=False)
    registration_id = TypedField(str, readable=False, writable=False, notnull=False)

    @classmethod
    def __on_define__(cls, db: TypeDAL) -> None:
        """
        When we have access to 'db', set the IS_NOT_IN_DB requirement.
        """
        super().__on_define__(db)

        cls.email.requires = (
            IS_EMAIL(),
            IS_NOT_IN_DB(
                db,
                "auth_user.email",
            ),
        )
