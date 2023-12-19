import sqlite3

import pytest

from src.typedal import TypeDAL
from src.typedal.for_web2py import setup_web2py_tables

db = TypeDAL("sqlite:memory")


def test_define():
    setup_web2py_tables(db, migrate=True)
    db.commit()

    db.executesql("SELECT * FROM auth_user")
    db.executesql("SELECT * FROM w2p_auth_membership")

    with pytest.raises(sqlite3.OperationalError):
        db.executesql("SELECT * FROM w2p_auth_user")
    with pytest.raises(sqlite3.OperationalError):
        db.executesql("SELECT * FROM auth_membership")
