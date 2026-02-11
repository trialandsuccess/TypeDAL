"""
TypeDAL can be configured by a combination of pyproject.toml (static), env (dynamic) and code (programmic).
"""

import os
import typing as t
import warnings
from pathlib import Path

import tomli
from configuraptor import TypedConfig, alias
from configuraptor.helpers import find_pyproject_toml
from dotenv import dotenv_values, find_dotenv

from .types import AnyDict

from configuraptor.helpers import expand_env_vars_into_toml_values

if t.TYPE_CHECKING:
    from edwh_migrate import Config as MigrateConfig
    from pydal2sql.typer_support import Config as P2SConfig

LazyPolicy = t.Literal["forbid", "warn", "ignore", "tolerate", "allow"]


class TypeDALConfig(TypedConfig):
    """
    Unified config for TypeDAL runtime behavior and migration utilities.
    """

    # typedal:
    database: str
    dialect: str
    folder: str = "databases"
    caching: bool = True
    pool_size: int = 0
    pyproject: str
    connection: str = "default"
    lazy_policy: LazyPolicy = "tolerate"

    # pydal2sql:
    input: str = ""
    output: str = ""
    noop: bool = False
    magic: bool = True
    tables: t.Optional[list[str]] = None
    function: str = "define_tables"

    # edwh-migrate:
    # migrate uri = database
    database_to_restore: t.Optional[str]
    migrate_cat_command: t.Optional[str]
    schema_version: t.Optional[str]
    redis_host: t.Optional[str]
    migrate_table: str = "typedal_implemented_features"
    flag_location: str
    create_flag_location: bool = True
    schema: str = "public"

    # typedal (depends on properties above)
    migrate: bool = True
    fake_migrate: bool = False

    # aliases:
    db_uri: str = alias("database")
    db_type: str = alias("dialect")
    db_folder: str = alias("folder")

    # repr set by @beautify (by inheriting from TypedConfig)

    def to_pydal2sql(self) -> "P2SConfig":
        """
        Convert the config to the format required by pydal2sql.
        """
        from pydal2sql.typer_support import Config, get_pydal2sql_config

        if self.pyproject:  # pragma: no cover
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
        """
        Convert the config to the format required by edwh-migrate.
        """
        from edwh_migrate import Config, get_config

        if self.pyproject:  # pragma: no cover
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


def _load_toml(path: str | bool | Path | None = True) -> tuple[str, AnyDict]:
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
    elif (_p := Path(str(path))) and _p.is_file():
        toml_path = _p
    else:
        toml_path = find_pyproject_toml(str(path))

    if not toml_path:
        # nothing to load
        return "", {}

    try:
        with open(toml_path, "rb") as f:
            data = tomli.load(f)

        return str(toml_path) or "", t.cast(AnyDict, data["tool"]["typedal"])
    except Exception as e:
        warnings.warn(f"Could not load typedal config toml: {e}", source=e)
        return str(toml_path) or "", {}


def _load_dotenv(path: str | bool | None = True) -> tuple[str, AnyDict]:
    fallback_data = {k.lower().removeprefix("typedal_"): v for k, v in os.environ.items()}
    if path is False:
        dotenv_path = None
        fallback_data = {}
    elif path in (True, None):
        dotenv_path = find_dotenv(usecwd=True)
    elif Path(str(path)).is_file():
        dotenv_path = str(path)
    else:
        dotenv_path = str(Path(str(path)) / ".env")

    if not dotenv_path:
        return "", fallback_data

    # 1. find everything with TYPEDAL_ prefix
    # 2. remove that prefix
    # 3. format values if possible
    data = dotenv_values(dotenv_path)
    data |= os.environ  # higher prio than .env

    typedal_data = {k.lower().removeprefix("typedal_"): v for k, v in data.items()}

    return dotenv_path, typedal_data


DB_ALIASES = {
    "postgresql": "postgres",
    "psql": "postgres",
    "sqlite3": "sqlite",
}


def get_db_for_alias(db_name: str) -> str:
    """
    Convert a db dialect alias to the standard name.
    """
    return DB_ALIASES.get(db_name, db_name)


DEFAULTS: dict[str, t.Any | t.Callable[[AnyDict], t.Any]] = {
    "database": lambda data: data.get("db_uri") or "sqlite:memory",
    "dialect": lambda data: (
        get_db_for_alias(data["database"].split(":")[0]) if ":" in data["database"] else data.get("db_type")
    ),
    "migrate": lambda data: not (data.get("input") or data.get("output")),
    "folder": lambda data: data.get("db_folder"),
    "flag_location": lambda data: (
        f"{db_folder}/flags" if (db_folder := (data.get("folder") or data.get("db_folder"))) else "/flags"
    ),
    "pool_size": lambda data: 1 if data.get("dialect", "sqlite") == "sqlite" else 3,
}


def _fill_defaults(data: AnyDict, prop: str, fallback: t.Any = None) -> None:
    default = DEFAULTS.get(prop, fallback)
    if callable(default):
        default = default(data)
    data[prop] = default


def fill_defaults(data: AnyDict, prop: str) -> None:
    """
    Fill missing property defaults with (calculated) sane defaults.
    """
    if data.get(prop, None) is None:
        _fill_defaults(data, prop)


TRANSFORMS: dict[str, t.Callable[[AnyDict], t.Any]] = {
    "database": lambda data: (
        data["database"]
        if (":" in data["database"] or not data.get("dialect"))
        else (data["dialect"] + "://" + data["database"])
    )
}


def transform(data: AnyDict, prop: str) -> bool:
    """
    After the user has chosen a value, possibly transform it.
    """
    if fn := TRANSFORMS.get(prop):
        data[prop] = fn(data)
        return True
    return False


def load_config(
    connection_name: t.Optional[str] = None,
    _use_pyproject: bool | str | None = True,
    _use_env: bool | str | None = True,
    **fallback: t.Any,
) -> TypeDALConfig:
    """
    Combines multiple sources of config into one config instance.
    """
    # load toml data
    # load .env data
    # combine and fill with fallback values
    # load typedal config or fail
    toml_path, toml = _load_toml(_use_pyproject)
    _dotenv_path, dotenv = _load_dotenv(_use_env)

    expand_env_vars_into_toml_values(toml, dotenv)

    connection_name = connection_name or dotenv.get("connection", "") or toml.get("default", "")
    connection: AnyDict = (toml.get(connection_name) if connection_name else toml) or {}

    combined = connection | dotenv | fallback
    combined = {k.replace("-", "_"): v for k, v in combined.items()}

    combined["pyproject"] = toml_path
    combined["connection"] = connection_name

    for prop in TypeDALConfig.__annotations__:
        fill_defaults(combined, prop)

    for prop in TypeDALConfig.__annotations__:
        transform(combined, prop)

    return TypeDALConfig.load(combined, convert_types=True)
