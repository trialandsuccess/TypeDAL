"""
TypeDAL can be configured by a combination of pyproject.toml (static), env (dynamic) and code (programmic).
"""
import os
import typing
from typing import Any, Optional

import tomli
from configuraptor import TypedConfig, alias
from configuraptor.helpers import find_pyproject_toml
from dotenv import dotenv_values, find_dotenv


class TypeDALConfig(TypedConfig):
    """
    Unified config for TypeDAL runtime behavior and migration utilities.
    """

    # typedal:
    database: str
    dialect: str
    folder: str = "databases"
    migrate: bool = True
    fake_migrate: bool = False
    caching: bool = True
    pool_size: int = 0

    # pydal2sql:
    input: Optional[str]  # noqa: A003
    output: Optional[str]
    noop: bool = False
    magic: bool = True
    tables: Optional[list[str]] = None

    # edwh-migrate:
    # migrate uri = database
    database_to_restore: Optional[str]
    migrate_cat_command: Optional[str]
    schema_version: Optional[str]
    redis_host: Optional[str]
    migrate_table: str = "typedal_implemented_features"
    flag_location: str
    create_flag_location: bool = True
    schema: str = "public"

    # aliases:
    db_folder = alias("folder")

    def __repr__(self) -> str:
        """
        Dump the config to a (fancy) string.
        """
        return f"<TypeDAL {self.__dict__}>"


def _load_toml() -> dict[str, Any]:
    if not (toml_path := find_pyproject_toml()):
        return {}

    try:
        with open(toml_path, "rb") as f:
            data = tomli.load(f)

        return typing.cast(dict[str, Any], data["tool"]["typedal"])
    except Exception:
        return {}


def _load_dotenv() -> dict[str, Any]:
    if not (dotenv_path := find_dotenv(usecwd=True)):
        return {}

    # 1. find everything with TYPEDAL_ prefix
    # 2. remove that prefix
    # 3. format values if possible
    data = dotenv_values(dotenv_path)
    data |= os.environ  # higher prio than .env

    typedal_data = {k.lower().removeprefix("typedal_"): v for k, v in data.items() if k.lower().startswith("typedal_")}

    return typedal_data


def load_config(**fallback: Any) -> TypeDALConfig:
    """
    Combines multiple sources of config into one config instance.
    """
    # load toml data
    # load .env data
    # combine and fill with fallback values
    # load typedal config or fail
    toml = _load_toml()
    dotenv = _load_dotenv()

    connection_name = dotenv.get("connection", "") or toml.get("default", "")
    connection: dict[str, Any] = toml.get(connection_name) or {}

    combined = connection | dotenv | fallback
    combined = {k.replace("-", "_"): v for k, v in combined.items()}

    if not combined.get("database"):
        combined["database"] = "sqlite:memory"

    if not combined.get("dialect") and ":" in combined["database"]:
        combined["dialect"] = combined["database"].split(":")[0]

    if not combined.get("migrate"):
        # if 'input' or 'output' is defined, you're probably using edwh-migrate -> don't auto migrate!
        combined["migrate"] = not ("input" in combined or "output" in combined)

    if not combined.get("flag_location"):
        if db_folder := combined.get("folder") or combined.get("db_folder"):
            combined["flag_location"] = f"{db_folder}/flags"
        else:
            combined["flag_location"] = "/flags"

    if not combined.get("pool_size"):
        combined["pool_size"] = 1 if combined["dialect"] == "sqlite" else 3

    return TypeDALConfig.load(combined)


""" # toml:
[tool.typedal]
# in .env:
# TYPEDAL_CONNECTION = "postgres"
# TYPEDAL_DATABASE = "postgres://..."

default = "sqlite"

[tool.typedal.sqlite]
dialect = 'sqlite' # optional, could be implied?
input = 'lib/models.py'
output = 'migrations_sqlite.py' # or migrations-file
database = "sqlite://storage.db" # other name; from .env?
db-folder = "databases" # optional

[tool.typedal.postgres]
dialect = 'psql' # optional, could be implied?
input = 'lib/models.py'
output = 'migrations_postgres.py'  # or migrations-file
# `database` from env different name

"""

""" # .env
TYPEDAL_CONNECTION="sqlite"
TYPEDAL_DATABASE="storage.db"
"""
