"""
TypeDAL can be configured by a combination of pyproject.toml (static), env (dynamic) and code (programmic).
"""

import os
import re
import typing
import warnings
from collections import defaultdict
from pathlib import Path
from typing import Any, Optional

import tomli
from configuraptor import TypedConfig, alias
from configuraptor.helpers import find_pyproject_toml
from dotenv import dotenv_values, find_dotenv

from .types import AnyDict

if typing.TYPE_CHECKING:
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
    caching: bool = True
    pool_size: int = 0
    pyproject: str
    connection: str = "default"

    # pydal2sql:
    input: str = ""
    output: str = ""
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

        return str(toml_path) or "", typing.cast(AnyDict, data["tool"]["typedal"])
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


DEFAULTS: dict[str, Any | typing.Callable[[AnyDict], Any]] = {
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


def _fill_defaults(data: AnyDict, prop: str, fallback: Any = None) -> None:
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


TRANSFORMS: dict[str, typing.Callable[[AnyDict], Any]] = {
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


def expand_posix_vars(posix_expr: str, context: dict[str, str]) -> str:
    """
    Replace case-insensitive POSIX and Docker Compose-like environment variables in a string with their values.

    Args:
        posix_expr (str): The input string containing case-insensitive POSIX or Docker Compose-like variables.
        context (dict): A dictionary containing variable names and their respective values.

    Returns:
        str: The string with replaced variable values.

    See Also:
        https://stackoverflow.com/questions/386934/how-to-evaluate-environment-variables-into-a-string-in-python
        and ChatGPT
    """
    env = defaultdict(lambda: "")
    for key, value in context.items():
        env[key.lower()] = value

    # Regular expression to match "${VAR:default}" pattern
    pattern = r"\$\{([^}]+)\}"

    def replace_var(match: re.Match[Any]) -> str:
        var_with_default = match.group(1)
        var_name, default_value = var_with_default.split(":") if ":" in var_with_default else (var_with_default, "")
        return env.get(var_name.lower(), default_value)

    return re.sub(pattern, replace_var, posix_expr)


def expand_env_vars_into_toml_values(toml: AnyDict, env: AnyDict) -> None:
    """
    Recursively expands POSIX/Docker Compose-like environment variables in a TOML dictionary.

    This function traverses a TOML dictionary and expands POSIX/Docker Compose-like
    environment variables (${VAR:default}) using values provided in the 'env' dictionary.
    It performs in-place modification of the 'toml' dictionary.

    Args:
        toml (dict): A TOML dictionary with string values possibly containing environment variables.
        env (dict): A dictionary containing environment variable names and their respective values.

    Returns:
        None: The function modifies the 'toml' dictionary in place.

    Notes:
        The function recursively traverses the 'toml' dictionary. If a value is a string or a list of strings,
        it attempts to substitute any environment variables found within those strings using the 'env' dictionary.

    Example:
        toml_data = {
            'key1': 'This has ${ENV_VAR:default}',
            'key2': ['String with ${ANOTHER_VAR}', 'Another ${YET_ANOTHER_VAR}']
        }
        environment = {
            'ENV_VAR': 'replaced_value',
            'ANOTHER_VAR': 'value_1',
            'YET_ANOTHER_VAR': 'value_2'
        }

        expand_env_vars_into_toml_values(toml_data, environment)
        # 'toml_data' will be modified in place:
        # {
        #     'key1': 'This has replaced_value',
        #     'key2': ['String with value_1', 'Another value_2']
        # }
    """
    if not toml or not env:
        return

    for key, var in toml.items():
        if isinstance(var, dict):
            expand_env_vars_into_toml_values(var, env)
        elif isinstance(var, list):
            toml[key] = [expand_posix_vars(_, env) for _ in var if isinstance(_, str)]
        elif isinstance(var, str):
            toml[key] = expand_posix_vars(var, env)
        else:
            # nothing to substitute
            continue


def load_config(
    connection_name: Optional[str] = None,
    _use_pyproject: bool | str | None = True,
    _use_env: bool | str | None = True,
    **fallback: Any,
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
