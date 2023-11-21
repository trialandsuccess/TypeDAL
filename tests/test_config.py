import shutil
import tempfile
import warnings
from contextlib import chdir
from pathlib import Path

import psycopg2
import pytest

from src.typedal import TypeDAL
from src.typedal.config import load_config


@pytest.fixture
def at_temp_dir():
    with tempfile.TemporaryDirectory() as d:
        with chdir(d):
            yield d


def _load_db_after_setup(dialect: str):
    config = load_config()
    try:
        db = TypeDAL(attempts=1)
        assert db._uri == config.database
    except (psycopg2.OperationalError, RuntimeError) as e:
        # postgres not running
        warnings.warn("Postgres is not running!", source=e)

    assert f"'dialect': '{dialect}'" in repr(config)

    return True


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


# test_fallback
"""
    uri: Optional[str] = None,
    pool_size: int = 0,
    folder: Optional[str | Path] = None,
    db_codec: str = "UTF-8",
    check_reserved: Optional[list[str]] = None,
    migrate: bool = True,
    fake_migrate: bool = False,
    migrate_enabled: bool = True,
    fake_migrate_all: bool = False,
    decode_credentials: bool = False,
    driver_args: Optional[dict[str, Any]] = None,
    adapter_args: Optional[dict[str, Any]] = None,
    attempts: int = 5,
    auto_import: bool = False,
    bigint_id: bool = False,
    debug: bool = False,
    lazy_tables: bool = False,
    db_uid: Optional[str] = None,
    after_connection: typing.Callable[..., Any] = None,
    tables: Optional[list[str]] = None,
    ignore_field_case: bool = True,
    entity_quoting: bool = True,
    table_hash: Optional[str] = None,
    enable_typedal_caching: bool = True,
"""
