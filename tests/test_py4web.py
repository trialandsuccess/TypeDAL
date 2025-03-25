import json
import tempfile

from contextlib_chdir import chdir
from pydal.validators import IS_EMAIL, IS_NOT_IN_DB

from src.typedal import TypedTable
from src.typedal.for_py4web import DAL, AuthUser, setup_py4web_tables
from src.typedal.serializers import as_json
from typedal.config import TypeDALConfig

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


def test_py4web_dal_singleton():
    with tempfile.TemporaryDirectory() as d:
        with chdir(d):
            # note: typedal caching is disabled here because otherwise the cache table might send logs to {d}/log.sql
            # which can lead to problems in other tests :(
            db_1a = DAL("sqlite:memory", enable_typedal_caching=False)
            db_1b = DAL("sqlite:memory", enable_typedal_caching=False)

            db_2a = DAL("sqlite://test_py4web_dal_singleton", folder=d, enable_typedal_caching=False)
            db_2b = DAL("sqlite://test_py4web_dal_singleton", folder=d, enable_typedal_caching=False)

            conf = {"database": "sqlite:memory", "dialect": "sqlite", "pyproject": "", "flag_location": f"{d}/flags"}
            db_3a = DAL(config=TypeDALConfig.load(conf))
            db_3b = DAL(config=TypeDALConfig.load(conf))

            assert db_1a is db_1b
            assert db_1a._uri == db_1b._uri
            assert db_1a._db_uid == db_1b._db_uid

            assert db_2a is db_2b
            assert db_2a._uri == db_2b._uri
            assert db_2a._db_uid == db_2b._db_uid

            assert db_1a is not db_2a
            assert db_1a._uri != db_2a._uri
            assert db_1a._db_uid != db_2a._db_uid

            assert db_1b is not db_2b
            assert db_1b._uri != db_2b._uri
            assert db_1b._db_uid != db_2b._db_uid

            assert db_3a is db_3b
            assert db_3a is not db_1a
            assert db_3a is not db_2a

            assert db_1a._uri == db_1b._uri
            assert db_3a._uri == db_1a._uri
            assert db_3a._uri != db_2a._uri

    # reset singletons for later use:
    DAL._clear()
