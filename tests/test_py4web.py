import json

from pydal.validators import IS_EMAIL, IS_NOT_IN_DB

from src.typedal import TypedTable
from src.typedal.for_py4web import DAL, AuthUser

db = DAL("sqlite:memory")


def test_P4W():
    assert hasattr(DAL, "on_request")
    assert hasattr(db, "on_request")


def test_serialize():
    @db.define()
    class JsonTable(TypedTable):
        field: str

    row = JsonTable.insert(field="Hey")

    l = json.loads(json.dumps(JsonTable))
    assert l
    assert isinstance(l, dict)
    l = json.loads(json.dumps(row))
    assert isinstance(l, dict)
    assert l

    l = json.loads(json.dumps(JsonTable.paginate(limit=1)))

    assert l["pagination"]
    assert l["data"]


def test_auth_user():
    db.define(AuthUser)
    requirements = AuthUser.email.requires

    assert requirements
    assert isinstance(requirements, tuple)
    assert len(requirements) == 2

    assert isinstance(requirements[0], IS_EMAIL)
    assert isinstance(requirements[1], IS_NOT_IN_DB)
