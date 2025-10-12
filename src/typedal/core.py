"""
Core functionality of TypeDAL.
"""

from __future__ import annotations

import sys
import typing as t
import warnings
from pathlib import Path
from typing import Optional

import pydal

from .config import TypeDALConfig, load_config
from .helpers import (
    SYSTEM_SUPPORTS_TEMPLATES,
    default_representer,
    sql_escape_template,
    sql_expression,
    to_snake,
)
from .types import Field, T, Template  # type: ignore

try:
    # python 3.14+
    from annotationlib import ForwardRef
except ImportError:  # pragma: no cover
    # python 3.13-
    from typing import ForwardRef

if t.TYPE_CHECKING:
    from .fields import TypedField
    from .types import AnyDict, Expression, T_Query, Table


# note: these functions can not be moved to a different file,
#  because then they will have different globals and it breaks!


def evaluate_forward_reference_312(fw_ref: ForwardRef, namespace: dict[str, type]) -> type:  # pragma: no cover
    """
    Extract the original type from a forward reference string.

    Variant for python 3.12 and below
    """
    return t.cast(
        type,
        fw_ref._evaluate(
            localns=locals(),
            globalns=globals() | namespace,
            recursive_guard=frozenset(),
        ),
    )


def evaluate_forward_reference_313(fw_ref: ForwardRef, namespace: dict[str, type]) -> type:  # pragma: no cover
    """
    Extract the original type from a forward reference string.

    Variant for python 3.13
    """
    return t.cast(
        type,
        fw_ref._evaluate(
            localns=locals(),
            globalns=globals() | namespace,
            recursive_guard=frozenset(),
            type_params=(),  # suggested since 3.13 (warning) and not supported before. Mandatory after 1.15!
        ),
    )


def evaluate_forward_reference_314(fw_ref: ForwardRef, namespace: dict[str, type]) -> type:  # pragma: no cover
    """
    Extract the original type from a forward reference string.

    Variant for python 3.14 (and hopefully above)
    """
    return t.cast(
        type,
        fw_ref.evaluate(
            locals=locals(),
            globals=globals() | namespace,
            type_params=(),
        ),
    )


def evaluate_forward_reference(
    fw_ref: ForwardRef,
    namespace: dict[str, type] | None = None,
) -> type:  # pragma: no cover
    """
    Extract the original type from a forward reference string.

    Automatically chooses strategy based on current Python version.
    """
    if sys.version_info.minor < 13:
        return evaluate_forward_reference_312(fw_ref, namespace=namespace or {})
    elif sys.version_info.minor == 13:
        return evaluate_forward_reference_313(fw_ref, namespace=namespace or {})
    else:
        return evaluate_forward_reference_314(fw_ref, namespace=namespace or {})


def resolve_annotation_313(ftype: str) -> type:  # pragma: no cover
    """
    Resolve an annotation that's in string representation.

    Variant for Python 3.13
    """
    fw_ref: ForwardRef = t.get_args(t.Type[ftype])[0]
    return evaluate_forward_reference(fw_ref)


def resolve_annotation_314(ftype: str) -> type:  # pragma: no cover
    """
    Resolve an annotation that's in string representation.

    Variant for Python 3.14 + using annotationlib
    """
    fw_ref = ForwardRef(ftype)
    return evaluate_forward_reference(fw_ref)


def resolve_annotation(ftype: str) -> type:  # pragma: no cover
    """
    Resolve an annotation that's in string representation.

    Automatically chooses strategy based on current Python version.
    """
    if sys.version_info.major != 3:
        raise EnvironmentError("Only python 3 is supported.")
    elif sys.version_info.minor <= 13:
        return resolve_annotation_313(ftype)
    else:
        return resolve_annotation_314(ftype)


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
        after_connection: t.Callable[..., t.Any] = None,
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

    def try_define(self, model: t.Type[T], verbose: bool = False) -> t.Type[T]:
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

    default_kwargs: t.ClassVar[AnyDict] = {
        # fields are 'required' (notnull) by default:
        "notnull": True,
    }

    @t.overload
    def define(self, maybe_cls: None = None, **kwargs: t.Any) -> t.Callable[[t.Type[T]], t.Type[T]]:
        """
        Typing Overload for define without a class.

        @db.define()
        class MyTable(TypedTable): ...
        """

    @t.overload
    def define(self, maybe_cls: t.Type[T], **kwargs: t.Any) -> t.Type[T]:
        """
        Typing Overload for define with a class.

        @db.define
        class MyTable(TypedTable): ...
        """

    def define(
        self,
        maybe_cls: t.Type[T] | None = None,
        **kwargs: t.Any,
    ) -> t.Type[T] | t.Callable[[t.Type[T]], t.Type[T]]:
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

        def wrapper(cls: t.Type[T]) -> t.Type[T]:
            return self._builder.define(cls, **kwargs)

        if maybe_cls:
            return wrapper(maybe_cls)

        return wrapper

    def __call__(self, *_args: T_Query, **kwargs: t.Any) -> "TypedSet":
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
                _cls: t.Type[TypedTable] = cls
                args[0] = _cls.id != None

        _set = super().__call__(*args, **kwargs)
        return t.cast(TypedSet, _set)

    def __getitem__(self, key: str) -> "Table":
        """
        Allows dynamically accessing a table by its name as a string.

        If you need the TypedTable class instead of the pydal table, use find_model instead.

        Example:
            db['users'] -> user
        """
        return t.cast(Table, super().__getitem__(str(key)))

    def find_model(self, table_name: str) -> t.Type["TypedTable"] | None:
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
    def _class_map(self) -> dict[str, t.Type["TypedTable"]]:
        # alias for backward-compatibility
        return self._builder.class_map

    @staticmethod
    def to_snake(camel: str) -> str:
        """
        Moved to helpers, kept as a static method for legacy reasons.
        """
        return to_snake(camel)

    def executesql(
        self,
        query: str | Template,
        placeholders: t.Iterable[str] | dict[str, str] | None = None,
        as_dict: bool = False,
        fields: t.Iterable[Field | TypedField[t.Any]] | None = None,
        colnames: t.Iterable[str] | None = None,
        as_ordered_dict: bool = False,
    ) -> list[t.Any]:
        """
        Executes a raw SQL statement or a TypeDAL template query.

        If `query` is provided as a `Template` and the system supports template
        rendering, it will be processed with `sql_escape_template` before being
        executed. Otherwise, the query is passed to the underlying DAL as-is.

        Args:
            query (str | Template): The SQL query to execute, either a plain
                string or a `Template` (created via the `t""` syntax).
            placeholders (Iterable[str] | dict[str, str] | None, optional):
                Parameters to substitute into the SQL statement. Can be a sequence
                (for positional parameters) or a dictionary (for named parameters).
                Usually not applicable when using a t-string, since template
                expressions handle interpolation directly.
            as_dict (bool, optional): If True, return rows as dictionaries keyed by
                column name. Defaults to False.
            fields (Iterable[Field | TypedField] | None, optional): Explicit set of
                fields to map results onto. Defaults to None.
            colnames (Iterable[str] | None, optional): Explicit column names to use
                in the result set. Defaults to None.
            as_ordered_dict (bool, optional): If True, return rows as `OrderedDict`s
                preserving column order. Defaults to False.

        Returns:
            list[t.Any]: The query result set. Typically a list of tuples if
            `as_dict` and `as_ordered_dict` are False, or a list of dict-like
            objects if those flags are enabled.
        """
        if SYSTEM_SUPPORTS_TEMPLATES and isinstance(query, Template):  # pragma: no cover
            query = sql_escape_template(self, query)

        rows: list[t.Any] = super().executesql(
            query,
            placeholders=placeholders,
            as_dict=as_dict,
            fields=fields,
            colnames=colnames,
            as_ordered_dict=as_ordered_dict,
        )

        return rows

    def sql_expression(
        self,
        sql_fragment: str | Template,
        *raw_args: t.Any,
        output_type: str | None = None,
        **raw_kwargs: t.Any,
    ) -> Expression:
        """
        Creates a pydal Expression object representing a raw SQL fragment.

        Args:
            sql_fragment: The raw SQL fragment.
                In python 3.14+, this can also be a t-string. In that case, don't pass other args or kwargs.
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
