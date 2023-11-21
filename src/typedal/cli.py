"""
Typer CLI for TypeDAL.
"""
import warnings
from pathlib import Path
from typing import Optional

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
from .config import load_config

app = typer.Typer(
    no_args_is_help=True,
)


@app.command()
@with_exit_code(hide_tb=IS_DEBUG)
def setup() -> None:
    print("Interactive or cli or dummy.")


@app.command()
@with_exit_code(hide_tb=IS_DEBUG)
def generate_migrations(
    filename_before: OptionalArgument[str] = None,
    filename_after: OptionalArgument[str] = None,
    db_type: DBType_Option = None,
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
        db_type=db_type.value if db_type else None,
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
def run_migrations() -> None:
    # todo: cli opts?
    # 1. build migrate Config from TypeDAL config
    # 2. import right file
    # 3. `activate_migrations`
    generic_config = load_config()
    migrate_config = generic_config.to_migrate()

    print("import", migrate_config.migrations_file)

    edwh_migrate.console_hook([], config=migrate_config)


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
