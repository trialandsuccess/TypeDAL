import json

from pydal.validators import IS_EMAIL, IS_NOT_IN_DB

from src.typedal import TypedTable
from src.typedal.for_py4web import DAL, AuthUser, setup_py4web_tables
from src.typedal.serializers import as_json

db = DAL("sqlite:memory")


def test_P4W():
    assert hasattr(DAL, "on_request")
    assert hasattr(db, "on_request")


def test_serialize():
    @db.define()
    class JsonTable(TypedTable):
        field: str

    row = JsonTable.insert(field="Hey")

    l = json.loads(as_json.encode(JsonTable))
    assert l
    assert isinstance(l, dict)
    l = json.loads(as_json.encode(row))
    assert isinstance(l, dict)
    assert l

    l = json.loads(as_json.encode(JsonTable.paginate(limit=1)))

    assert l["pagination"]
    assert l["data"]


def test_auth_user():
    setup_py4web_tables(db)
    requirements = AuthUser.email.requires

    assert requirements
    assert isinstance(requirements, tuple)
    assert len(requirements) == 2

    assert isinstance(requirements[0], IS_EMAIL)
    assert isinstance(requirements[1], IS_NOT_IN_DB)
