"""
TypeDAL can be configured by a combination of pyproject.toml (static), env (dynamic) and code (programmic).
"""
import os
import typing
from pathlib import Path
from typing import Any, Optional

import black.files
import tomli
from configuraptor import TypedConfig, alias
from dotenv import dotenv_values, find_dotenv

if typing.TYPE_CHECKING:  # pragma: no cover
    from edwh_migrate import Config as MigrateConfig
    from pydal2sql.typer_support import Config as P2SConfig


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
    function: str = "define_tables"

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

    def to_pydal2sql(self) -> "P2SConfig":
        from pydal2sql.typer_support import Config

        return Config.load(
            {
                "db_type": self.dialect,
                "format": "edwh-migrate",
                "tables": self.tables,
                "magic": self.magic,
                "function": self.function,
                "input": self.input,
                "output": self.output,
                "pyproject": find_pyproject_toml(),
            }
        )

    def to_migrate(self) -> "MigrateConfig":
        from edwh_migrate import Config

        return Config.load(
            {
                "migrate_uri": self.database,
                "schema_version": self.schema_version,
                "redis_host": self.redis_host,
                "migrate_cat_command": self.migrate_cat_command,
                "database_to_restore": self.database_to_restore,
                "migrate_table": self.migrate_table,
                "flag_location": self.flag_location,
                "create_flag_location": self.create_flag_location,
                "schema": self.schema,
                "db_folder": self.folder,
                "migrations_file": self.output,
            }
        )


def find_pyproject_toml(directory: str | None = None) -> typing.Optional[str]:
    """
    Find the project's config toml, looks up until it finds the project root (black's logic).
    """
    return black.files.find_pyproject_toml((directory or os.getcwd(),))


def _load_toml(path: str | bool = True) -> dict[str, Any]:
    """
    Path can be a file, a directory, a bool or None.

    If it is True or None, the default logic is used.
    If it is False, no data is loaded.
    if it is a directory, the pyproject.toml will be searched there.
    If it is a path, that file will be used.
    """
    if path is False:
        toml_path = None
    elif path in (True, None):
        toml_path = find_pyproject_toml()
    elif Path(str(path)).is_file():
        toml_path = str(path)
    else:
        toml_path = find_pyproject_toml(str(path))

    if not toml_path:
        # nothing to load
        return {}

    try:
        with open(toml_path, "rb") as f:
            data = tomli.load(f)

        return typing.cast(dict[str, Any], data["tool"]["typedal"])
    except Exception:
        return {}


def _load_dotenv(path: str | bool = True) -> dict[str, Any]:
    if path is False:
        dotenv_path = None
    elif path in (True, None):
        dotenv_path = find_dotenv(usecwd=True)
    elif Path(str(path)).is_file():
        dotenv_path = str(path)
    else:
        dotenv_path = str(Path(str(path)) / ".env")

    if not dotenv_path:
        return {}

    # 1. find everything with TYPEDAL_ prefix
    # 2. remove that prefix
    # 3. format values if possible
    data = dotenv_values(dotenv_path)
    data |= os.environ  # higher prio than .env

    typedal_data = {k.lower().removeprefix("typedal_"): v for k, v in data.items() if k.lower().startswith("typedal_")}

    return typedal_data


def load_config(_use_pyproject: bool | str = True, _use_env: bool | str = True, **fallback: Any) -> TypeDALConfig:
    """
    Combines multiple sources of config into one config instance.
    """
    # load toml data
    # load .env data
    # combine and fill with fallback values
    # load typedal config or fail
    toml = _load_toml(_use_pyproject)
    dotenv = _load_dotenv(_use_env)

    connection_name = dotenv.get("connection", "") or toml.get("default", "")
    connection: dict[str, Any] = toml.get(connection_name) or {}

    combined = connection | dotenv | fallback
    combined = {k.replace("-", "_"): v for k, v in combined.items()}

    if not combined.get("database"):
        combined["database"] = "sqlite:memory"

    if not combined.get("dialect") and ":" in combined["database"]:
        combined["dialect"] = combined["database"].split(":")[0]

    if ":" not in combined["database"] and (dialect := combined.get("dialect")):
        _db = combined["database"]
        combined["database"] = f"{dialect}://{_db}"

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
