import datetime as dt
import os
import shutil
import tempfile
import uuid
from pathlib import Path

import pytest

# from contextlib import chdir
from contextlib_chdir import chdir
from pydal2sql import generate_sql
from testcontainers.postgres import PostgresContainer

from src.typedal import TypeDAL, TypedField, TypedTable
from src.typedal.config import (
    _load_dotenv,
    _load_toml,
    expand_env_vars_into_toml_values,
    load_config,
)
from src.typedal.fields import PointField, TimestampField, UUIDField

postgres = PostgresContainer(
    dbname="postgres",
    username="someuser",
    password="somepass",
)


@pytest.fixture(scope="module", autouse=True)
def psql(request):
    postgres.ports = {
        5432: 9631,  # as set in valid.env
    }

    request.addfinalizer(postgres.stop)
    postgres.start()


@pytest.fixture
def at_temp_dir():
    with tempfile.TemporaryDirectory() as d:
        with chdir(d):
            yield d


def _load_db_after_setup(dialect: str):
    config = load_config()
    db = TypeDAL(attempts=1)
    assert db._uri == config.database

    assert f"'dialect': '{dialect}'" in repr(config)

    return True


def test_load_toml(at_temp_dir):
    base = Path("pyproject.toml")
    base.write_text("# empty")

    with pytest.warns(UserWarning):
        # it should warn because the toml is empty
        assert _load_toml(False) == ("", {})
        assert _load_toml(None) == (str(base.resolve().absolute()), {})
        assert _load_toml(str(base)) == ("pyproject.toml", {})
        assert _load_toml(".") == (str(base.resolve().absolute()), {})


def test_load_dotenv(at_temp_dir):
    base = Path(".env")
    base.write_text("# empty")

    assert _load_dotenv(False)[0] == ""
    assert _load_dotenv(None)[0] == str(base.resolve().absolute())
    assert _load_dotenv(str(base))[0] == ".env"
    assert _load_dotenv(".")[0] == ".env"


def test_load_empty_config(at_temp_dir):
    assert _load_db_after_setup("sqlite")


def test_load_toml_config(at_temp_dir):
    examples = Path(__file__).parent / "configs"
    shutil.copy(examples / "valid.toml", "./pyproject.toml")

    assert _load_db_after_setup("sqlite")


def test_load_env_config(at_temp_dir):
    examples = Path(__file__).parent / "configs"
    shutil.copy(examples / "valid.env", "./.env")

    assert _load_db_after_setup("postgres")


def test_load_simple_config(at_temp_dir):
    examples = Path(__file__).parent / "configs"
    shutil.copy(examples / "valid.env", "./.env")
    shutil.copy(examples / "simple.toml", "./pyproject.toml")

    assert _load_db_after_setup("postgres")


def test_load_both_config(at_temp_dir):
    examples = Path(__file__).parent / "configs"
    shutil.copy(examples / "valid.env", "./.env")
    shutil.copy(examples / "valid.toml", "./pyproject.toml")

    assert _load_db_after_setup("postgres")


def test_converting(at_temp_dir):
    from edwh_migrate import Config as MigrateConfig
    from pydal2sql.typer_support import Config as P2SConfig

    config = load_config()

    assert isinstance(config.to_migrate(), MigrateConfig)
    assert isinstance(config.to_pydal2sql(), P2SConfig)


def test_environ(at_temp_dir):
    os.environ["DB_URI"] = "sqlite:///tmp/db.sqlite"
    config = load_config(False, True)

    assert config.database == "sqlite:///tmp/db.sqlite"


def test_expand_env_vars():
    # str
    input_str = "${MYVALUE:default}"
    data = {"myvar": input_str}
    expand_env_vars_into_toml_values(data, {})
    assert data["myvar"] == input_str

    expand_env_vars_into_toml_values(data, {"unrelated": "data"})
    assert data["myvar"] == "default"

    data = {"myvar": input_str}
    expand_env_vars_into_toml_values(data, {"myvalue": "123"})

    assert data["myvar"] == "123"

    # list
    data = {"myvar": [input_str, input_str]}
    expand_env_vars_into_toml_values(data, {"myvalue": "456"})

    assert data["myvar"] == ["456", "456"]

    # dict
    data = {"myvar": {"value": input_str}}
    expand_env_vars_into_toml_values(data, {"myvalue": "789"})
    assert data["myvar"]["value"] == "789"

    # other - non-str
    data = {"myvar": None, "mynumber": 123}
    expand_env_vars_into_toml_values(data, {"myvalue": "789"})
    assert data["myvar"] is None
    assert data["mynumber"] == 123


# note: these are not really 'config' specific but we already have access to postgres here so good enough:


def test_timestamp_fields_sqlite(at_temp_dir):
    db = TypeDAL("sqlite:memory")

    class Timestamp(TypedTable):
        ts = TimestampField(default=dt.datetime.now)
        dt = TypedField(dt.datetime, default=dt.datetime.now)

    db.define(Timestamp)

    row = Timestamp.insert()

    # old:
    assert isinstance(row.dt, dt.datetime), "not a datetime"
    assert "." not in str(row.dt)  # no ms precision

    # new:
    assert isinstance(row.ts, dt.datetime), "not a datetime"
    assert "." in str(row.ts)  # ms precision

    assert '"ts" timestamp NOT NULL' in Timestamp._sql()


def test_timestamp_fields_psql(at_temp_dir):
    examples = Path(__file__).parent / "configs"
    shutil.copy(examples / "valid.env", "./.env")
    shutil.copy(examples / "simple.toml", "./pyproject.toml")

    assert _load_db_after_setup("postgres")
    db = TypeDAL(attempts=1)

    class Timestamp(TypedTable):
        ts = TimestampField(default=dt.datetime.now)
        dt = TypedField(dt.datetime, default=dt.datetime.now)

    db.define(Timestamp)

    row = Timestamp.insert()

    # old:
    assert isinstance(row.dt, dt.datetime), "not a datetime"
    assert "." not in str(row.dt)  # no ms precision

    # new:
    assert isinstance(row.ts, dt.datetime), "not a datetime"
    assert "." in str(row.ts)  # ms precision

    assert '"ts" timestamp NOT NULL' in Timestamp._sql()


def test_point_fields_sqlite(at_temp_dir):
    db = TypeDAL("sqlite:memory")

    class Point(TypedTable):
        pt = PointField()

    db.define(Point)

    row1 = Point.insert(pt=(1, 0))
    row2 = Point.insert(pt="(1, 0)")

    assert row1.pt == row2.pt

    x, y = row1.pt
    assert x == 1
    assert y == 0

    with pytest.raises(Exception):
        Point.insert(pt=123)

    with pytest.raises(Exception):
        Point.insert(pt="123")

    # note: psql will check this whereas sqlite won't:
    Point.insert(pt=())

    assert '"pt" point NOT NULL' in Point._sql()


def test_point_fields_psql(at_temp_dir):
    examples = Path(__file__).parent / "configs"
    shutil.copy(examples / "valid.env", "./.env")
    shutil.copy(examples / "simple.toml", "./pyproject.toml")

    assert _load_db_after_setup("postgres")
    db = TypeDAL(attempts=1)

    class Point(TypedTable):
        pt = PointField()

    db.define(Point)

    row1 = Point.insert(pt=(1, 0))
    row2 = Point.insert(pt="(1, 0)")

    assert row1.pt == row2.pt

    x, y = row1.pt
    assert x == 1
    assert y == 0

    with pytest.raises(Exception):
        Point.insert(pt=123)

    with pytest.raises(Exception):
        Point.insert(pt="123")

    # note: psql will check this whereas sqlite won't:

    with pytest.raises(Exception):
        Point.insert(pt=())

    assert '"pt" point NOT NULL' in Point._sql()


def test_uuid_fields_psql(at_temp_dir):
    examples = Path(__file__).parent / "configs"
    shutil.copy(examples / "valid.env", "./.env")
    shutil.copy(examples / "simple.toml", "./pyproject.toml")

    assert _load_db_after_setup("postgres")
    db = TypeDAL(attempts=1)

    class UUIDTable(TypedTable):
        gid = UUIDField(default=uuid.uuid4)

    db.define(UUIDTable)

    row = UUIDTable.insert()

    assert isinstance(row.gid, uuid.UUID)

    with pytest.raises(Exception):
        UUIDTable.insert(gid="not-a-uuid")

    assert '"gid" uuid NOT NULL' in UUIDTable._sql()


def test_uuid_fields_sqlite(at_temp_dir):
    db = TypeDAL("sqlite:memory")

    class UUIDTable(TypedTable):
        gid = UUIDField(default=uuid.uuid4)

    db.define(UUIDTable)

    row = UUIDTable.insert()

    assert isinstance(row.gid, uuid.UUID)

    with pytest.raises(Exception):
        UUIDTable.insert(gid="not-a-uuid")

    assert '"gid" uuid NOT NULL' in UUIDTable._sql()
