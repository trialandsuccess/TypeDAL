"""
Typer CLI for TypeDAL.
"""
import warnings
from pathlib import Path
from typing import Optional

import tomli

try:
    import edwh_migrate
    import pydal2sql  # noqa: F401
    import typer
except ImportError as e:
    # ImportWarning is hidden by default
    warnings.warn(
        "`migrations` extra not installed. Please run `pip install typedal[migrations]` to fix this.",
        source=e,
        category=RuntimeWarning,
    )
    exit(127)  # command not found

from pydal2sql.typer_support import IS_DEBUG, with_exit_code
from pydal2sql.types import (
    DBType_Option,
    OptionalArgument,
    OutputFormat_Option,
    Tables_Option,
)
from pydal2sql_core import core_alter, core_create
from typing_extensions import Never

from .__about__ import __version__
from .config import TypeDALConfig, load_config

app = typer.Typer(
    no_args_is_help=True,
)


@app.command()
@with_exit_code(hide_tb=IS_DEBUG)
def setup(config_file: Optional[str] = None) -> None:
    print("Interactive or cli or dummy.")
    # 1. check if [tool.typedal] in pyproject.toml and ask missing questions (excl .env vars)
    # 2. else if [tool.migrate] and/or [tool.pydal2sql] exist in the config, ask the user with copied defaults
    # 3. else: ask the user every question or minimal questions based on cli arg
    #      todo: choose --minimal, --full
    # todo: rename --config-file to --config and -c

    config = load_config(config_file)

    toml_path = Path(config.pyproject)

    if not (config.pyproject and toml_path.exists()):
        # no pyproject.toml found!
        print("no pyproject.toml")
        return

    toml_contents = toml_path.read_text()
    toml_obj = tomli.loads(toml_contents)

    if "[tool.typedal]" in toml_contents:
        print("existing typedal", toml_obj["tool"]["typedal"])
        return

    if "[tool.pydal2sql]" in toml_contents:
        mapping = {"": ""}  # <- placeholder

        extra_config = toml_obj["tool"]["pydal2sql"]
        extra_config = {mapping.get(k, k): v for k, v in extra_config.items()}
        extra_config.pop("format", None)  # always edwh-migrate
        config.update(**extra_config)

    if "[tool.migrate]" in toml_contents:
        mapping = {"migrate_uri": "database"}

        extra_config = toml_obj["tool"]["migrate"]
        extra_config = {mapping.get(k, k): v for k, v in extra_config.items()}

        config.update(**extra_config)

    for prop, annotation in TypeDALConfig.__annotations__.items():
        default_value = getattr(config, prop, None)
        print(prop, annotation, default_value)

    # todo: include logic from `load_config` before/after user answers questions
    # e.g. 'schema' from database BEFORE asking schema (-> default)
    #      schema://database if no : in database (-> automatically)


@app.command()
@with_exit_code(hide_tb=IS_DEBUG)
def generate_migrations(
    filename_before: OptionalArgument[str] = None,
    filename_after: OptionalArgument[str] = None,
    dialect: DBType_Option = None,
    tables: Tables_Option = None,
    magic: Optional[bool] = None,
    noop: Optional[bool] = None,
    function: Optional[str] = None,
    output_format: OutputFormat_Option = None,
    output_file: Optional[str] = None,
) -> bool:
    # 1. choose CREATE or ALTER based on whether 'output' exists?
    # 2. pass right args based on 'config' to function chosen in 1.
    generic_config = load_config()
    pydal2sql_config = generic_config.to_pydal2sql()
    pydal2sql_config.update(
        magic=magic,
        noop=noop,
        tables=tables,
        db_type=dialect.value if dialect else None,
        function=function,
        format=output_format,
        input=filename_before,
        output=output_file,
    )

    if pydal2sql_config.output and Path(pydal2sql_config.output).exists():
        return core_alter(
            pydal2sql_config.input,
            filename_after or pydal2sql_config.input,
            db_type=pydal2sql_config.db_type,
            tables=pydal2sql_config.tables,
            noop=pydal2sql_config.noop,
            magic=pydal2sql_config.magic,
            function=pydal2sql_config.function,
            output_format=pydal2sql_config.format,
            output_file=pydal2sql_config.output,
        )
    else:
        return core_create(
            filename=pydal2sql_config.input,
            db_type=pydal2sql_config.db_type,
            tables=pydal2sql_config.tables,
            noop=pydal2sql_config.noop,
            magic=pydal2sql_config.magic,
            function=pydal2sql_config.function,
            output_format=pydal2sql_config.format,
            output_file=pydal2sql_config.output,
        )


@app.command()
@with_exit_code(hide_tb=IS_DEBUG)
def run_migrations(
    migrations_file: OptionalArgument[str] = None,
    db_uri: Optional[str] = None,
    db_folder: Optional[str] = None,
    schema_version: Optional[str] = None,
    redis_host: Optional[str] = None,
    migrate_cat_command: Optional[str] = None,
    database_to_restore: Optional[str] = None,
    migrate_table: Optional[str] = None,
    flag_location: Optional[str] = None,
    schema: Optional[str] = None,
    create_flag_location: Optional[bool] = None,
) -> None:
    # 1. build migrate Config from TypeDAL config
    # 2. import right file
    # 3. `activate_migrations`
    generic_config = load_config()
    migrate_config = generic_config.to_migrate()

    migrate_config.update(
        migrate_uri=db_uri,
        schema_version=schema_version,
        redis_host=redis_host,
        migrate_cat_command=migrate_cat_command,
        database_to_restore=database_to_restore,
        migrate_table=migrate_table,
        flag_location=flag_location,
        schema=schema,
        create_flag_location=create_flag_location,
        db_folder=db_folder,
        migrations_file=migrations_file,
    )

    print(f"{migrate_config=}")

    return edwh_migrate.console_hook([], config=migrate_config)


def version_callback() -> Never:
    """
    --version requested!
    """

    print(f"pydal2sql Version: {__version__}")

    raise typer.Exit(0)


def config_callback() -> Never:
    """
    --show-config requested.
    """
    config = load_config()

    print(repr(config))

    raise typer.Exit(0)


@app.callback(invoke_without_command=True)
def main(
    _: typer.Context,
    # stops the program:
    show_config: bool = False,
    version: bool = False,
) -> None:
    """
    This script can be used to generate the create or alter sql from pydal or typedal.
    """
    if show_config:
        config_callback()
    elif version:
        version_callback()
    # else: just continue


if __name__ == "__main__":
    app()
