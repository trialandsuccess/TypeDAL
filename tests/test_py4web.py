import json

from src.typedal import TypedTable
from src.typedal.for_py4web import DAL

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
