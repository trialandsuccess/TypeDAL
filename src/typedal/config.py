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
    pyproject: str

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
        from pydal2sql.typer_support import Config, get_pydal2sql_config

        if self.pyproject:
            project = Path(self.pyproject).read_text()

            if "[tool.typedal]" not in project and "[tool.pydal2sql]" in project:
                # no typedal config, but existing p2s config:
                return get_pydal2sql_config(self.pyproject)

        return Config.load(
            {
                "db_type": self.dialect,
                "format": "edwh-migrate",
                "tables": self.tables,
                "magic": self.magic,
                "function": self.function,
                "input": self.input,
                "output": self.output,
                "pyproject": self.pyproject,
            }
        )

    def to_migrate(self) -> "MigrateConfig":
        from edwh_migrate import Config, get_config

        if self.pyproject:
            project = Path(self.pyproject).read_text()

            if "[tool.typedal]" not in project and "[tool.migrate]" in project:
                # no typedal config, but existing p2s config:
                return get_config()

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


def _load_toml(path: str | bool | None = True) -> tuple[str, dict[str, Any]]:
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
        return "", {}

    try:
        with open(toml_path, "rb") as f:
            data = tomli.load(f)

        return toml_path or "", typing.cast(dict[str, Any], data["tool"]["typedal"])
    except Exception:
        return toml_path or "", {}


def _load_dotenv(path: str | bool | None = True) -> tuple[str, dict[str, Any]]:
    if path is False:
        dotenv_path = None
    elif path in (True, None):
        dotenv_path = find_dotenv(usecwd=True)
    elif Path(str(path)).is_file():
        dotenv_path = str(path)
    else:
        dotenv_path = str(Path(str(path)) / ".env")

    if not dotenv_path:
        return "", {}

    # 1. find everything with TYPEDAL_ prefix
    # 2. remove that prefix
    # 3. format values if possible
    data = dotenv_values(dotenv_path)
    data |= os.environ  # higher prio than .env

    typedal_data = {k.lower().removeprefix("typedal_"): v for k, v in data.items() if k.lower().startswith("typedal_")}

    return dotenv_path, typedal_data


DEFAULTS: dict[str, Any | typing.Callable[[dict[str, Any]], Any]] = {
    "database": "sqlite:memory",
    "dialect": lambda data: data["database"].split(":")[0] if ":" in data["database"] else None,
    "migrate": lambda data: not ("input" in data or "output" in data),
    "flag_location": lambda data: f"{db_folder}/flags"
    if (db_folder := (data.get("folder") or data.get("db_folder")))
    else "/flags",
    "pool_size": lambda data: 1 if data.get("dialect", "sqlite") == "sqlite" else 3,
}

# todo: TRANSFORMATIONS dict?
# transform database -> dialect://database if not : in db

TRANSFORMS: dict[str, typing.Callable[[dict[str, Any]], Any]] = {
    "database": lambda data: data["database"]
    if (":" in data["database"] or not data.get("dialect"))
    else (data["dialect"] + "://" + data["database"])
}


def fill_defaults(data: dict[str, Any], prop: str) -> None:
    if data.get(prop, None) is None:
        default = DEFAULTS.get(prop)
        if callable(default):
            default = default(data)
        data[prop] = default


def transform(data: dict[str, Any], prop: str) -> None:
    if fn := TRANSFORMS.get(prop):
        data[prop] = fn(data)


def load_config(
    _use_pyproject: bool | str | None = True, _use_env: bool | str | None = True, **fallback: Any
) -> TypeDALConfig:
    """
    Combines multiple sources of config into one config instance.
    """
    # load toml data
    # load .env data
    # combine and fill with fallback values
    # load typedal config or fail
    toml_path, toml = _load_toml(_use_pyproject)
    dotenv_path, dotenv = _load_dotenv(_use_env)

    connection_name = dotenv.get("connection", "") or toml.get("default", "")
    connection: dict[str, Any] = toml.get(connection_name) or {}

    combined = connection | dotenv | fallback
    combined = {k.replace("-", "_"): v for k, v in combined.items()}

    combined["pyproject"] = toml_path

    for prop in TypeDALConfig.__annotations__:
        fill_defaults(combined, prop)

    for prop in TypeDALConfig.__annotations__:
        transform(combined, prop)

    return TypeDALConfig.load(combined)
