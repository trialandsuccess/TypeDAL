"""
Core functionality of TypeDAL.
"""

from __future__ import annotations

import sys
import typing
import warnings
from pathlib import Path
from typing import Any, Optional, Type

import pydal

from .config import TypeDALConfig, load_config
from .helpers import (
    default_representer,
    sql_expression,
    to_snake,
)

if typing.TYPE_CHECKING:
    from .types import AnyDict, Expression, T, T_Query, Table


def evaluate_forward_reference(fw_ref: typing.ForwardRef) -> type:
    """
    Extract the original type from a forward reference string.
    """
    # can't be moved out of core because we need these globals:
    kwargs = dict(
        localns=locals(),
        globalns=globals(),
        recursive_guard=frozenset(),
    )
    if sys.version_info >= (3, 13):  # pragma: no cover
        # suggested since 3.13 (warning) and not supported before. Mandatory after 1.15!
        kwargs["type_params"] = ()

    return fw_ref._evaluate(**kwargs)  # type: ignore


class TypeDAL(pydal.DAL):  # type: ignore
    """
    Drop-in replacement for pyDAL with layer to convert class-based table definitions to classical pydal define_tables.
    """

    _config: TypeDALConfig
    _builder: TableDefinitionBuilder

    def __init__(
        self,
        uri: Optional[str] = None,  # default from config or 'sqlite:memory'
        pool_size: int = None,  # default 1 if sqlite else 3
        folder: Optional[str | Path] = None,  # default 'databases' in config
        db_codec: str = "UTF-8",
        check_reserved: Optional[list[str]] = None,
        migrate: Optional[bool] = None,  # default True by config
        fake_migrate: Optional[bool] = None,  # default False by config
        migrate_enabled: bool = True,
        fake_migrate_all: bool = False,
        decode_credentials: bool = False,
        driver_args: Optional[AnyDict] = None,
        adapter_args: Optional[AnyDict] = None,
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
        enable_typedal_caching: bool = None,
        use_pyproject: bool | str = True,
        use_env: bool | str = True,
        connection: Optional[str] = None,
        config: Optional[TypeDALConfig] = None,
    ) -> None:
        """
        Adds some internal tables after calling pydal's default init.

        Set enable_typedal_caching to False to disable this behavior.
        """
        config = config or load_config(connection, _use_pyproject=use_pyproject, _use_env=use_env)
        config.update(
            database=uri,
            dialect=uri.split(":")[0] if uri and ":" in uri else None,
            folder=str(folder) if folder is not None else None,
            migrate=migrate,
            fake_migrate=fake_migrate,
            caching=enable_typedal_caching,
            pool_size=pool_size,
        )

        self._config = config
        self.db = self
        self._builder = TableDefinitionBuilder(self)

        if config.folder:
            Path(config.folder).mkdir(exist_ok=True)

        super().__init__(
            config.database,
            config.pool_size,
            config.folder,
            db_codec,
            check_reserved,
            config.migrate,
            config.fake_migrate,
            migrate_enabled,
            fake_migrate_all,
            decode_credentials,
            driver_args,
            adapter_args,
            attempts,
            auto_import,
            bigint_id,
            debug,
            lazy_tables,
            db_uid,
            after_connection,
            tables,
            ignore_field_case,
            entity_quoting,
            table_hash,
        )

        if config.caching:
            self.try_define(_TypedalCache)
            self.try_define(_TypedalCacheDependency)

    def try_define(self, model: Type[T], verbose: bool = False) -> Type[T]:
        """
        Try to define a model with migrate or fall back to fake migrate.
        """
        try:
            return self.define(model, migrate=True)
        except Exception as e:
            # clean up:
            self.rollback()
            if (tablename := self.to_snake(model.__name__)) and tablename in dir(self):
                delattr(self, tablename)

            if verbose:
                warnings.warn(f"{model} could not be migrated, try faking", source=e, category=RuntimeWarning)

            # try again:
            return self.define(model, migrate=True, fake_migrate=True, redefine=True)

    default_kwargs: typing.ClassVar[AnyDict] = {
        # fields are 'required' (notnull) by default:
        "notnull": True,
    }

    @typing.overload
    def define(self, maybe_cls: None = None, **kwargs: Any) -> typing.Callable[[Type[T]], Type[T]]:
        """
        Typing Overload for define without a class.

        @db.define()
        class MyTable(TypedTable): ...
        """

    @typing.overload
    def define(self, maybe_cls: Type[T], **kwargs: Any) -> Type[T]:
        """
        Typing Overload for define with a class.

        @db.define
        class MyTable(TypedTable): ...
        """

    def define(self, maybe_cls: Type[T] | None = None, **kwargs: Any) -> Type[T] | typing.Callable[[Type[T]], Type[T]]:
        """
        Can be used as a decorator on a class that inherits `TypedTable`, \
          or as a regular method if you need to define your classes before you have access to a 'db' instance.

        You can also pass extra arguments to db.define_table.
            See http://www.web2py.com/books/default/chapter/29/06/the-database-abstraction-layer#Table-constructor

        Example:
            @db.define
            class Person(TypedTable):
                ...

            class Article(TypedTable):
                ...

            # at a later time:
            db.define(Article)

        Returns:
            the result of pydal.define_table
        """

        def wrapper(cls: Type[T]) -> Type[T]:
            return self._builder.define(cls, **kwargs)

        if maybe_cls:
            return wrapper(maybe_cls)

        return wrapper

    def __call__(self, *_args: T_Query, **kwargs: Any) -> "TypedSet":
        """
        A db instance can be called directly to perform a query.

        Usually, only a query is passed.

        Example:
            db(query).select()

        """
        args = list(_args)
        if args:
            cls = args[0]
            if isinstance(cls, bool):
                raise ValueError("Don't actually pass a bool to db()! Use a query instead.")

            if isinstance(cls, type) and issubclass(type(cls), type) and issubclass(cls, TypedTable):
                # table defined without @db.define decorator!
                _cls: Type[TypedTable] = cls
                args[0] = _cls.id != None

        _set = super().__call__(*args, **kwargs)
        return typing.cast(TypedSet, _set)

    def __getitem__(self, key: str) -> "Table":
        """
        Allows dynamically accessing a table by its name as a string.

        If you need the TypedTable class instead of the pydal table, use find_model instead.

        Example:
            db['users'] -> user
        """
        return typing.cast(Table, super().__getitem__(str(key)))

    def find_model(self, table_name: str) -> Type["TypedTable"] | None:
        """
        Retrieves a mapped table class by its name.

        This method searches for a table class matching the given table name
        in the defined class map dictionary. If a match is found, the corresponding
        table class is returned; otherwise, None is returned, indicating that no
        table class matches the input name.

        Args:
            table_name: The rname of the table to retrieve the mapped class for.

        Returns:
            The mapped table class if it exists, otherwise None.
        """
        return self._builder.class_map.get(table_name, None)

    @property
    def _class_map(self) -> dict[str, Type["TypedTable"]]:
        # alias for backward-compatibility
        return self._builder.class_map

    @staticmethod
    def to_snake(camel: str) -> str:
        """
        Moved to helpers, kept as a static method for legacy reasons.
        """
        return to_snake(camel)

    def sql_expression(
        self,
        sql_fragment: str,
        *raw_args: Any,
        output_type: str | None = None,
        **raw_kwargs: Any,
    ) -> Expression:
        """
        Creates a pydal Expression object representing a raw SQL fragment.

        Args:
            sql_fragment: The raw SQL fragment.
            *raw_args: Arguments to be interpolated into the SQL fragment.
            output_type: The expected output type of the expression.
            **raw_kwargs: Keyword arguments to be interpolated into the SQL fragment.

        Returns:
            A pydal Expression object.
        """
        return sql_expression(self, sql_fragment, *raw_args, output_type=output_type, **raw_kwargs)


TypeDAL.representers.setdefault("rows_render", default_representer)

# note: these imports exist at the bottom of this file to prevent circular import issues:

from .fields import *  # noqa: E402 F403 # isort: skip ; to fill globals() scope
from .define import TableDefinitionBuilder  # noqa: E402
from .rows import TypedSet  # noqa: E402
from .tables import TypedTable  # noqa: E402

from .caching import (  # isort: skip # noqa: E402
    _TypedalCache,
    _TypedalCacheDependency,
)
