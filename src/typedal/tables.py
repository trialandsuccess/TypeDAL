"""
Contains base functionality related to Tables.
"""

from __future__ import annotations

import copy
import csv
import functools
import json
import typing as t
import uuid

import pydal.objects
from pydal._globals import DEFAULT

from .constants import JOIN_OPTIONS
from .core import TypeDAL
from .helpers import classproperty, throw
from .serializers import as_json
from .types import (
    AnyDict,
    Condition,
    Expression,
    Field,
    OnQuery,
    OpRow,
    OrderBy,
    P,
    Query,
    R,
    Reference,
    Row,
    SelectKwargs,
    Set,
    T,
    T_MetaInstance,
    T_Query,
    Table,
)

if t.TYPE_CHECKING:
    from .relationships import Relationship
    from .rows import PaginatedRows, TypedRows


def reorder_fields(
    table: pydal.objects.Table,
    fields: t.Iterable[str | TypedField[t.Any] | Field],
    keep_others: bool = True,
) -> None:
    """
    Reorder fields of a pydal table.

    Args:
        table: The pydal table object (e.g., db.mytable).
        fields: List of field names (str) or Field objects in desired order.
        keep_others (bool):
            - True (default): keep other fields at the end, in their original order.
            - False: remove other fields (only keep what's specified).
    """
    # Normalize input to field names
    desired = [f.name if isinstance(f, (TypedField, Field, pydal.objects.Field)) else str(f) for f in fields]

    new_order = [f for f in desired if f in table._fields]

    if keep_others:
        # Start with desired fields, then append the rest
        new_order.extend(f for f in table._fields if f not in desired)

    table._fields = new_order


class TableMeta(type):
    """
    This metaclass contains functionality on table classes, that doesn't exist on its instances.

    Example:
        class MyTable(TypedTable):
            some_field: TypedField[int]

    MyTable.update_or_insert(...) # should work

    MyTable.some_field # -> Field, can be used to query etc.

    row = MyTable.first() # returns instance of MyTable

    # row.update_or_insert(...) # shouldn't work!

    row.some_field # -> int, with actual data

    """

    # set up by db.define:
    # _db: TypeDAL | None = None
    # _table: Table | None = None
    _db: TypeDAL | None = None
    _table: Table | None = None
    _relationships: dict[str, Relationship[t.Any]] | None = None

    #########################
    # TypeDAL custom logic: #
    #########################

    def __set_internals__(self, db: pydal.DAL, table: Table, relationships: dict[str, Relationship[t.Any]]) -> None:
        """
        Store the related database and pydal table for later usage.
        """
        self._db = db
        self._table = table
        self._relationships = relationships

    def __getattr__(self, col: str) -> t.Optional[Field]:
        """
        Magic method used by TypedTableMeta to get a database field with dot notation on a class.

        Example:
            SomeTypedTable.col -> db.table.col (via TypedTableMeta.__getattr__)

        """
        if self._table:
            return getattr(self._table, col, None)

        return None

    def _ensure_table_defined(self) -> Table:
        if not self._table:
            raise EnvironmentError("@define or db.define is not called on this class yet!")
        return self._table

    def __iter__(self) -> t.Generator[Field, None, None]:
        """
        Loop through the columns of this model.
        """
        table = self._ensure_table_defined()
        yield from iter(table)

    def __getitem__(self, item: str) -> Field:
        """
        Allow dict notation to get a column of this table (-> Field instance).
        """
        table = self._ensure_table_defined()
        return table[item]

    def __str__(self) -> str:
        """
        Normally, just returns the underlying table name, but with a fallback if the model is unbound.
        """
        if self._table:
            return str(self._table)
        else:
            return f"<unbound table {self.__name__}>"

    def from_row(self: t.Type[T_MetaInstance], row: pydal.objects.Row) -> T_MetaInstance:
        """
        Create a model instance from a pydal row.
        """
        return self(row)

    def all(self: t.Type[T_MetaInstance]) -> "TypedRows[T_MetaInstance]":
        """
        Return all rows for this model.
        """
        return self.collect()

    def get_relationships(self) -> dict[str, Relationship[t.Any]]:
        """
        Return the registered relationships of the current model.
        """
        return self._relationships or {}

    ##########################
    # TypeDAL Modified Logic #
    ##########################

    def insert(self: t.Type[T_MetaInstance], **fields: t.Any) -> T_MetaInstance:
        """
        This is only called when db.define is not used as a decorator.

        cls.__table functions as 'self'

        Args:
            **fields: t.Anything you want to insert in the database

        Returns: the ID of the new row.

        """
        table = self._ensure_table_defined()

        result = table.insert(**fields)
        # it already is an int but mypy doesn't understand that
        return self(result)

    def _insert(self, **fields: t.Any) -> str:
        table = self._ensure_table_defined()

        return str(table._insert(**fields))

    def bulk_insert(self: t.Type[T_MetaInstance], items: list[AnyDict]) -> "TypedRows[T_MetaInstance]":
        """
        Insert multiple rows, returns a TypedRows set of new instances.
        """
        table = self._ensure_table_defined()
        result = table.bulk_insert(items)
        return self.where(lambda row: row.id.belongs(result)).collect()

    def update_or_insert(
        self: t.Type[T_MetaInstance],
        query: T_Query | AnyDict = DEFAULT,
        **values: t.Any,
    ) -> T_MetaInstance:
        """
        Update a row if query matches, else insert a new one.

        Returns the created or updated instance.
        """
        table = self._ensure_table_defined()

        if query is DEFAULT:
            record = table(**values)
        elif isinstance(query, dict):
            record = table(**query)
        else:
            record = table(query)

        if not record:
            return self.insert(**values)

        record.update_record(**values)
        return self(record)

    def validate_and_insert(
        self: t.Type[T_MetaInstance],
        **fields: t.Any,
    ) -> tuple[t.Optional[T_MetaInstance], t.Optional[dict[str, str]]]:
        """
        Validate input data and then insert a row.

        Returns a tuple of (the created instance, a dict of errors).
        """
        table = self._ensure_table_defined()
        result = table.validate_and_insert(**fields)
        if row_id := result.get("id"):
            return self(row_id), None
        else:
            return None, result.get("errors")

    def validate_and_update(
        self: t.Type[T_MetaInstance],
        query: Query,
        **fields: t.Any,
    ) -> tuple[t.Optional[T_MetaInstance], t.Optional[dict[str, str]]]:
        """
        Validate input data and then update max 1 row.

        Returns a tuple of (the updated instance, a dict of errors).
        """
        table = self._ensure_table_defined()

        result = table.validate_and_update(query, **fields)

        if errors := result.get("errors"):
            return None, errors
        elif row_id := result.get("id"):
            return self(row_id), None
        else:  # pragma: no cover
            # update on query without result (shouldnt happen)
            return None, None

    def validate_and_update_or_insert(
        self: t.Type[T_MetaInstance],
        query: Query,
        **fields: t.Any,
    ) -> tuple[t.Optional[T_MetaInstance], t.Optional[dict[str, str]]]:
        """
        Validate input data and then update_and_insert (on max 1 row).

        Returns a tuple of (the updated/created instance, a dict of errors).
        """
        table = self._ensure_table_defined()
        result = table.validate_and_update_or_insert(query, **fields)

        if errors := result.get("errors"):
            return None, errors
        elif row_id := result.get("id"):
            return self(row_id), None
        else:  # pragma: no cover
            # update on query without result (shouldnt happen)
            return None, None

    def select(self: t.Type[T_MetaInstance], *a: t.Any, **kw: t.Any) -> "QueryBuilder[T_MetaInstance]":
        """
        See QueryBuilder.select!
        """
        return QueryBuilder(self).select(*a, **kw)

    def column(self: t.Type[T_MetaInstance], field: T | TypedField[T], **options: t.Unpack[SelectKwargs]) -> list[T]:
        """
        Get all values in a specific column.

        Shortcut for `.select(field).execute().column(field)`.
        """
        return QueryBuilder(self).select(field, **options).execute().column(field)

    def paginate(self: t.Type[T_MetaInstance], limit: int, page: int = 1) -> "PaginatedRows[T_MetaInstance]":
        """
        See QueryBuilder.paginate!
        """
        return QueryBuilder(self).paginate(limit=limit, page=page)

    def chunk(self: t.Type[T_MetaInstance], chunk_size: int) -> t.Generator["TypedRows[T_MetaInstance]", t.Any, None]:
        """
        See QueryBuilder.chunk!
        """
        return QueryBuilder(self).chunk(chunk_size)

    def where(self: t.Type[T_MetaInstance], *a: t.Any, **kw: t.Any) -> "QueryBuilder[T_MetaInstance]":
        """
        See QueryBuilder.where!
        """
        return QueryBuilder(self).where(*a, **kw)

    def orderby(self: t.Type[T_MetaInstance], *fields: OrderBy) -> "QueryBuilder[T_MetaInstance]":
        """
        See QueryBuilder.orderby!
        """
        return QueryBuilder(self).orderby(*fields)

    def cache(self: t.Type[T_MetaInstance], *deps: t.Any, **kwargs: t.Any) -> "QueryBuilder[T_MetaInstance]":
        """
        See QueryBuilder.cache!
        """
        return QueryBuilder(self).cache(*deps, **kwargs)

    def count(self: t.Type[T_MetaInstance]) -> int:
        """
        See QueryBuilder.count!
        """
        return QueryBuilder(self).count()

    def exists(self: t.Type[T_MetaInstance]) -> bool:
        """
        See QueryBuilder.exists!
        """
        return QueryBuilder(self).exists()

    def first(self: t.Type[T_MetaInstance]) -> T_MetaInstance | None:
        """
        See QueryBuilder.first!
        """
        return QueryBuilder(self).first()

    def first_or_fail(self: t.Type[T_MetaInstance]) -> T_MetaInstance:
        """
        See QueryBuilder.first_or_fail!
        """
        return QueryBuilder(self).first_or_fail()

    def join(
        self: t.Type[T_MetaInstance],
        *fields: str | t.Type["TypedTable"],
        method: JOIN_OPTIONS = None,
        on: OnQuery | list[Expression] | Expression = None,
        condition: Condition = None,
        condition_and: Condition = None,
    ) -> "QueryBuilder[T_MetaInstance]":
        """
        See QueryBuilder.join!
        """
        return QueryBuilder(self).join(*fields, on=on, condition=condition, method=method, condition_and=condition_and)

    def collect(self: t.Type[T_MetaInstance], verbose: bool = False) -> "TypedRows[T_MetaInstance]":
        """
        See QueryBuilder.collect!
        """
        return QueryBuilder(self).collect(verbose=verbose)

    @property
    def ALL(cls) -> pydal.objects.SQLALL:
        """
        Select all fields for this table.
        """
        table = cls._ensure_table_defined()

        return table.ALL

    ##########################
    # TypeDAL Shadowed Logic #
    ##########################
    fields: list[str]

    # other table methods:

    def truncate(self, mode: str = "") -> None:
        """
        Remove all data and reset index.
        """
        table = self._ensure_table_defined()
        table.truncate(mode)

    def drop(self, mode: str = "") -> None:
        """
        Remove the underlying table.
        """
        table = self._ensure_table_defined()
        table.drop(mode)

    def create_index(self, name: str, *fields: str | Field, **kwargs: t.Any) -> bool:
        """
        Add an index on some columns of this table.
        """
        table = self._ensure_table_defined()
        result = table.create_index(name, *fields, **kwargs)
        return t.cast(bool, result)

    def drop_index(self, name: str, if_exists: bool = False) -> bool:
        """
        Remove an index from this table.
        """
        table = self._ensure_table_defined()
        result = table.drop_index(name, if_exists)
        return t.cast(bool, result)

    def import_from_csv_file(
        self,
        csvfile: t.TextIO,
        id_map: dict[str, str] = None,
        null: t.Any = "<NULL>",
        unique: str = "uuid",
        id_offset: dict[str, int] = None,  # id_offset used only when id_map is None
        transform: t.Callable[[dict[t.Any, t.Any]], dict[t.Any, t.Any]] = None,
        validate: bool = False,
        encoding: str = "utf-8",
        delimiter: str = ",",
        quotechar: str = '"',
        quoting: int = csv.QUOTE_MINIMAL,
        restore: bool = False,
        **kwargs: t.Any,
    ) -> None:
        """
        Load a csv file into the database.
        """
        table = self._ensure_table_defined()
        table.import_from_csv_file(
            csvfile,
            id_map=id_map,
            null=null,
            unique=unique,
            id_offset=id_offset,
            transform=transform,
            validate=validate,
            encoding=encoding,
            delimiter=delimiter,
            quotechar=quotechar,
            quoting=quoting,
            restore=restore,
            **kwargs,
        )

    def on(self, query: bool | Query) -> Expression:
        """
        Shadow Table.on.

        Used for joins.

        See Also:
            http://web2py.com/books/default/chapter/29/06/the-database-abstraction-layer?search=export_to_csv_file#One-to-mt.Any-relation
        """
        table = self._ensure_table_defined()
        return t.cast(Expression, table.on(query))

    def with_alias(self: t.Type[T_MetaInstance], alias: str) -> t.Type[T_MetaInstance]:
        """
        Shadow Table.with_alias.

        Useful for joins when joining the same table multiple times.

        See Also:
            http://web2py.com/books/default/chapter/29/06/the-database-abstraction-layer#One-to-mt.Any-relation
        """
        table = self._ensure_table_defined()
        return t.cast(t.Type[T_MetaInstance], table.with_alias(alias))

    def unique_alias(self: t.Type[T_MetaInstance]) -> t.Type[T_MetaInstance]:
        """
        Generates a unique alias for this table.

        Useful for joins when joining the same table multiple times
            and you don't want to keep track of aliases yourself.
        """
        key = f"{self.__name__.lower()}_{hash(uuid.uuid4())}"
        return self.with_alias(key)

    # hooks:
    def _hook_once(
        cls: t.Type[T_MetaInstance],
        hooks: list[t.Callable[P, R]],
        fn: t.Callable[P, R],
    ) -> t.Type[T_MetaInstance]:
        @functools.wraps(fn)
        def wraps(*a: P.args, **kw: P.kwargs) -> R:
            try:
                return fn(*a, **kw)
            finally:
                hooks.remove(wraps)

        hooks.append(wraps)
        return cls

    def before_insert(
        cls: t.Type[T_MetaInstance],
        fn: t.Callable[[T_MetaInstance], t.Optional[bool]] | t.Callable[[OpRow], t.Optional[bool]],
    ) -> t.Type[T_MetaInstance]:
        """
        Add a before insert hook.
        """
        if fn not in cls._before_insert:
            cls._before_insert.append(fn)
        return cls

    def before_insert_once(
        cls: t.Type[T_MetaInstance],
        fn: t.Callable[[T_MetaInstance], t.Optional[bool]] | t.Callable[[OpRow], t.Optional[bool]],
    ) -> t.Type[T_MetaInstance]:
        """
        Add a before insert hook that only fires once and then removes itself.
        """
        return cls._hook_once(cls._before_insert, fn)  # type: ignore

    def after_insert(
        cls: t.Type[T_MetaInstance],
        fn: (
            t.Callable[[T_MetaInstance, Reference], t.Optional[bool]] | t.Callable[[OpRow, Reference], t.Optional[bool]]
        ),
    ) -> t.Type[T_MetaInstance]:
        """
        Add an after insert hook.
        """
        if fn not in cls._after_insert:
            cls._after_insert.append(fn)
        return cls

    def after_insert_once(
        cls: t.Type[T_MetaInstance],
        fn: (
            t.Callable[[T_MetaInstance, Reference], t.Optional[bool]] | t.Callable[[OpRow, Reference], t.Optional[bool]]
        ),
    ) -> t.Type[T_MetaInstance]:
        """
        Add an after insert hook that only fires once and then removes itself.
        """
        return cls._hook_once(cls._after_insert, fn)  # type: ignore

    def before_update(
        cls: t.Type[T_MetaInstance],
        fn: t.Callable[[Set, T_MetaInstance], t.Optional[bool]] | t.Callable[[Set, OpRow], t.Optional[bool]],
    ) -> t.Type[T_MetaInstance]:
        """
        Add a before update hook.
        """
        if fn not in cls._before_update:
            cls._before_update.append(fn)
        return cls

    def before_update_once(
        cls,
        fn: t.Callable[[Set, T_MetaInstance], t.Optional[bool]] | t.Callable[[Set, OpRow], t.Optional[bool]],
    ) -> t.Type[T_MetaInstance]:
        """
        Add a before update hook that only fires once and then removes itself.
        """
        return cls._hook_once(cls._before_update, fn)  # type: ignore

    def after_update(
        cls: t.Type[T_MetaInstance],
        fn: t.Callable[[Set, T_MetaInstance], t.Optional[bool]] | t.Callable[[Set, OpRow], t.Optional[bool]],
    ) -> t.Type[T_MetaInstance]:
        """
        Add an after update hook.
        """
        if fn not in cls._after_update:
            cls._after_update.append(fn)
        return cls

    def after_update_once(
        cls: t.Type[T_MetaInstance],
        fn: t.Callable[[Set, T_MetaInstance], t.Optional[bool]] | t.Callable[[Set, OpRow], t.Optional[bool]],
    ) -> t.Type[T_MetaInstance]:
        """
        Add an after update hook that only fires once and then removes itself.
        """
        return cls._hook_once(cls._after_update, fn)  # type: ignore

    def before_delete(cls: t.Type[T_MetaInstance], fn: t.Callable[[Set], t.Optional[bool]]) -> t.Type[T_MetaInstance]:
        """
        Add a before delete hook.
        """
        if fn not in cls._before_delete:
            cls._before_delete.append(fn)
        return cls

    def before_delete_once(
        cls: t.Type[T_MetaInstance],
        fn: t.Callable[[Set], t.Optional[bool]],
    ) -> t.Type[T_MetaInstance]:
        """
        Add a before delete hook that only fires once and then removes itself.
        """
        return cls._hook_once(cls._before_delete, fn)

    def after_delete(cls: t.Type[T_MetaInstance], fn: t.Callable[[Set], t.Optional[bool]]) -> t.Type[T_MetaInstance]:
        """
        Add an after delete hook.
        """
        if fn not in cls._after_delete:
            cls._after_delete.append(fn)
        return cls

    def after_delete_once(
        cls: t.Type[T_MetaInstance],
        fn: t.Callable[[Set], t.Optional[bool]],
    ) -> t.Type[T_MetaInstance]:
        """
        Add an after delete hook that only fires once and then removes itself.
        """
        return cls._hook_once(cls._after_delete, fn)

    def reorder_fields(cls, *fields: str | Field | TypedField[t.Any], keep_others: bool = True) -> None:
        """
        Reorder fields of a typedal table.

        Args:
            fields: List of field names (str) or Field objects in desired order.
            keep_others (bool):
                - True (default): keep other fields at the end, in their original order.
                - False: remove other fields (only keep what's specified).
        """
        return reorder_fields(cls._table, fields, keep_others=keep_others)


class _TypedTable:
    """
    This class is a final shared parent between TypedTable and Mixins.

    This needs to exist because otherwise the __on_define__ of Mixins are not executed.
    Notably, this class exists at a level ABOVE the `metaclass=TableMeta`,
        because otherwise typing gets confused when Mixins are used and multiple types could satisfy
            generic 'T subclass of TypedTable'
        -> Setting 'TypedTable' as the parent for Mixin does not work at runtime (and works semi at type check time)
    """

    id: "TypedField[int]"

    _before_insert: list[t.Callable[[t.Self], t.Optional[bool]] | t.Callable[[OpRow], t.Optional[bool]]]
    _after_insert: list[
        t.Callable[[t.Self, Reference], t.Optional[bool]] | t.Callable[[OpRow, Reference], t.Optional[bool]]
    ]
    _before_update: list[t.Callable[[Set, t.Self], t.Optional[bool]] | t.Callable[[Set, OpRow], t.Optional[bool]]]
    _after_update: list[t.Callable[[Set, t.Self], t.Optional[bool]] | t.Callable[[Set, OpRow], t.Optional[bool]]]
    _before_delete: list[t.Callable[[Set], t.Optional[bool]]]
    _after_delete: list[t.Callable[[Set], t.Optional[bool]]]

    @classmethod
    def __on_define__(cls, db: TypeDAL) -> None:
        """
        Method that can be implemented by tables to do an action after db.define is completed.

        This can be useful if you need to add something like requires=IS_NOT_IN_DB(db, "table.field"),
        where you need a reference to the current database, which may not exist yet when defining the model.
        """

    @classproperty
    def _hooks(cls) -> dict[str, list[t.Callable[..., t.Optional[bool]]]]:
        return {
            "before_insert": cls._before_insert,
            "after_insert": cls._after_insert,
            "before_update": cls._before_update,
            "after_update": cls._after_update,
            "before_delete": cls._before_delete,
            "after_delete": cls._after_delete,
        }


class TypedTable(_TypedTable, metaclass=TableMeta):
    """
    Enhanded modeling system on top of pydal's Table that adds typing and additional functionality.
    """

    # set up by 'new':
    _row: Row | None = None
    _rows: tuple[Row, ...] = ()

    _with: list[str]

    def _setup_instance_methods(self) -> None:
        self.as_dict = self._as_dict  # type: ignore
        self.__json__ = self.as_json = self._as_json  # type: ignore
        # self.as_yaml = self._as_yaml  # type: ignore
        self.as_xml = self._as_xml  # type: ignore

        self.update = self._update  # type: ignore

        self.delete_record = self._delete_record  # type: ignore
        self.update_record = self._update_record  # type: ignore

    def __new__(
        cls,
        row_or_id: t.Union[Row, Query, pydal.objects.Set, int, str, None, "TypedTable"] = None,
        **filters: t.Any,
    ) -> t.Self:
        """
        Create a Typed Rows model instance from an existing row, ID or query.

        Examples:
            MyTable(1)
            MyTable(id=1)
            MyTable(MyTable.id == 1)
        """
        table = cls._ensure_table_defined()
        inst = super().__new__(cls)

        if isinstance(row_or_id, TypedTable):
            # existing typed table instance!
            return t.cast(t.Self, row_or_id)

        elif isinstance(row_or_id, pydal.objects.Row):
            row = row_or_id
        elif row_or_id is not None:
            row = table(row_or_id, **filters)
        elif filters:
            row = table(**filters)
        else:
            # dummy object
            return inst

        if not row:
            return None  # type: ignore

        inst._row = row

        if hasattr(row, "id"):
            inst.__dict__.update(row)
        else:
            # deal with _extra (and possibly others?)
            # Row <{actual: {}, _extra: ...}>
            inst.__dict__.update(row[str(cls)])

        inst._setup_instance_methods()
        return inst

    def __iter__(self) -> t.Generator[t.Any, None, None]:
        """
        Allows looping through the columns.
        """
        row = self._ensure_matching_row()
        yield from iter(row)

    def __getitem__(self, item: str) -> t.Any:
        """
        Allows dictionary notation to get columns.
        """
        if item in self.__dict__:
            return self.__dict__.get(item)

        # fallback to lookup in row
        if self._row:
            return self._row[item]

        # nothing found!
        raise KeyError(item)

    def __getattr__(self, item: str) -> t.Any:
        """
        Allows dot notation to get columns.
        """
        if value := self.get(item):
            return value

        raise AttributeError(item)

    def keys(self) -> list[str]:
        """
        Return the combination of row + relationship keys.

        Used by dict(row).
        """
        return list(self._row.keys() if self._row else ()) + getattr(self, "_with", [])

    def get(self, item: str, default: t.Any = None) -> t.Any:
        """
        Try to get a column from this instance, else return default.
        """
        try:
            return self.__getitem__(item)
        except KeyError:
            return default

    def __setitem__(self, key: str, value: t.Any) -> None:
        """
        Data can both be updated via dot and dict notation.
        """
        return setattr(self, key, value)

    def __int__(self) -> int:
        """
        Calling int on a model instance will return its id.
        """
        return getattr(self, "id", 0)

    def __bool__(self) -> bool:
        """
        If the instance has an underlying row with data, it is truthy.
        """
        return bool(getattr(self, "_row", False))

    def _ensure_matching_row(self) -> Row:
        row = getattr(self, "_row", None)
        return t.cast(Row, row) or throw(
            EnvironmentError("Trying to access non-existant row. Maybe it was deleted or not yet initialized?")
        )

    def __repr__(self) -> str:
        """
        String representation of the model instance.
        """
        model_name = self.__class__.__name__
        model_data = {}

        if self._row:
            model_data = self._row.as_json()

        details = model_name
        details += f"({model_data})"

        if relationships := getattr(self, "_with", []):
            details += f" + {relationships}"

        return f"<{details}>"

    # serialization
    # underscore variants work for class instances (set up by _setup_instance_methods)

    @classmethod
    def as_dict(cls, flat: bool = False, sanitize: bool = True) -> AnyDict:
        """
        Dump the object to a plain dict.

        Can be used as both a class or instance method:
        - dumps the table info if it's a class
        - dumps the row info if it's an instance (see _as_dict)
        """
        table = cls._ensure_table_defined()
        result = table.as_dict(flat, sanitize)
        return t.cast(AnyDict, result)

    @classmethod
    def as_json(cls, sanitize: bool = True, indent: t.Optional[int] = None, **kwargs: t.Any) -> str:
        """
        Dump the object to json.

        Can be used as both a class or instance method:
        - dumps the table info if it's a class
        - dumps the row info if it's an instance (see _as_json)
        """
        data = cls.as_dict(sanitize=sanitize)
        return as_json.encode(data, indent=indent, **kwargs)

    @classmethod
    def as_xml(cls, sanitize: bool = True) -> str:  # pragma: no cover
        """
        Dump the object to xml.

        Can be used as both a class or instance method:
        - dumps the table info if it's a class
        - dumps the row info if it's an instance (see _as_xml)
        """
        table = cls._ensure_table_defined()
        return t.cast(str, table.as_xml(sanitize))

    @classmethod
    def as_yaml(cls, sanitize: bool = True) -> str:
        """
        Dump the object to yaml.

        Can be used as both a class or instance method:
        - dumps the table info if it's a class
        - dumps the row info if it's an instance (see _as_yaml)
        """
        table = cls._ensure_table_defined()
        return t.cast(str, table.as_yaml(sanitize))

    def _as_dict(
        self,
        datetime_to_str: bool = False,
        custom_types: t.Iterable[type] | type | None = None,
    ) -> AnyDict:
        row = self._ensure_matching_row()

        result = row.as_dict(datetime_to_str=datetime_to_str, custom_types=custom_types)

        def asdict_method(obj: t.Any) -> t.Any:  # pragma: no cover
            if hasattr(obj, "_as_dict"):  # typedal
                return obj._as_dict()
            elif hasattr(obj, "as_dict"):  # pydal
                return obj.as_dict()
            else:  # something else??
                return obj.__dict__

        if _with := getattr(self, "_with", None):
            for relationship in _with:
                data = self.get(relationship)

                if isinstance(data, list):
                    data = [asdict_method(_) for _ in data]
                elif data:
                    data = asdict_method(data)

                result[relationship] = data

        return t.cast(AnyDict, result)

    def _as_json(
        self,
        default: t.Callable[[t.Any], t.Any] = None,
        indent: t.Optional[int] = None,
        **kwargs: t.Any,
    ) -> str:
        data = self._as_dict()
        return as_json.encode(data, default=default, indent=indent, **kwargs)

    def _as_xml(self, sanitize: bool = True) -> str:  # pragma: no cover
        row = self._ensure_matching_row()
        return t.cast(str, row.as_xml(sanitize))

    # def _as_yaml(self, sanitize: bool = True) -> str:
    #     row = self._ensure_matching_row()
    #     return t.cast(str, row.as_yaml(sanitize))

    def __setattr__(self, key: str, value: t.Any) -> None:
        """
        When setting a property on a Typed Table model instance, also update the underlying row.
        """
        if self._row and key in self._row.__dict__ and not callable(value):
            # enables `row.key = value; row.update_record()`
            self._row[key] = value

        super().__setattr__(key, value)

    @classmethod
    def update(cls: t.Type[T_MetaInstance], query: Query, **fields: t.Any) -> T_MetaInstance | None:
        """
        Update one record.

        Example:
            MyTable.update(MyTable.id == 1, name="NewName") -> MyTable
        """
        # todo: update multiple?
        if record := cls(query):
            return record.update_record(**fields)
        else:
            return None

    def _update(self: T_MetaInstance, **fields: t.Any) -> T_MetaInstance:
        row = self._ensure_matching_row()
        row.update(**fields)
        self.__dict__.update(**fields)
        return self

    def _update_record(self: T_MetaInstance, **fields: t.Any) -> T_MetaInstance:
        row = self._ensure_matching_row()
        new_row = row.update_record(**fields)
        self.update(**new_row)
        return self

    def update_record(self: T_MetaInstance, **fields: t.Any) -> T_MetaInstance:  # pragma: no cover
        """
        Here as a placeholder for _update_record.

        Will be replaced on instance creation!
        """
        return self._update_record(**fields)

    def _delete_record(self) -> int:
        """
        Actual logic in `pydal.helpers.classes.RecordDeleter`.
        """
        row = self._ensure_matching_row()
        result = row.delete_record()
        self.__dict__ = {}  # empty self, since row is no more.
        self._row = None  # just to be sure
        self._setup_instance_methods()
        # ^ instance methods might've been deleted by emptying dict,
        # but we still want .as_dict to show an error, not the table's as_dict.
        return t.cast(int, result)

    def delete_record(self) -> int:  # pragma: no cover
        """
        Here as a placeholder for _delete_record.

        Will be replaced on instance creation!
        """
        return self._delete_record()

    # __del__ is also called on the end of a scope so don't remove records on every del!!

    # pickling:

    def __getstate__(self) -> AnyDict:
        """
        State to save when pickling.

        Prevents db connection from being pickled.
        Similar to as_dict but without changing the data of the relationships (dill does that recursively)
        """
        row = self._ensure_matching_row()
        result: AnyDict = row.as_dict()

        if _with := getattr(self, "_with", None):
            result["_with"] = _with
            for relationship in _with:
                data = self.get(relationship)

                result[relationship] = data

        result["_row"] = self._row.as_json() if self._row else ""
        return result

    def __setstate__(self, state: AnyDict) -> None:
        """
        Used by dill when loading from a bytestring.
        """
        # as_dict also includes table info, so dump as json to only get the actual row data
        # then create a new (more empty) row object:
        state["_row"] = Row(json.loads(state["_row"]))
        self.__dict__ |= state

    @classmethod
    def _sql(cls) -> str:
        """
        Generate SQL Schema for this table via pydal2sql (if 'migrations' extra is installed).
        """
        try:
            import pydal2sql
        except ImportError as e:  # pragma: no cover
            raise RuntimeError("Can not generate SQL without the 'migration' extra or `pydal2sql` installed!") from e

        return pydal2sql.generate_sql(cls)

    def render(self, fields: list[Field] = None, compact: bool = False) -> t.Self:
        """
        Renders a copy of the object with potentially modified values.

        Args:
            fields: A list of fields to render. Defaults to all representable fields in the table.
            compact: Whether to return only the value of the first field if there is only one field.

        Returns:
            A copy of the object with potentially modified values.
        """
        row = copy.deepcopy(self)
        keys = list(row)
        if not fields:
            fields = [self._table[f] for f in self._table._fields]
            fields = [f for f in fields if isinstance(f, Field) and f.represent]

        for field in fields:
            if field._table == self._table:
                row[field.name] = self._db.represent(
                    "rows_render",
                    field,
                    row[field.name],
                    row,
                )
            # else: relationship, different logic:

        for relation_name in getattr(row, "_with", []):
            if relation := self._relationships.get(relation_name):
                relation_table = relation.table
                if isinstance(relation_table, str):
                    relation_table = self._db[relation_table]

                relation_row = row[relation_name]

                if isinstance(relation_row, list):
                    # list of rows
                    combined = []

                    for related_og in relation_row:
                        related = copy.deepcopy(related_og)
                        for fieldname in related:
                            field = relation_table[fieldname]
                            related[field.name] = self._db.represent(
                                "rows_render",
                                field,
                                related[field.name],
                                related,
                            )
                        combined.append(related)

                    row[relation_name] = combined
                else:
                    # 1 row
                    for fieldname in relation_row:
                        field = relation_table[fieldname]
                        row[relation_name][fieldname] = self._db.represent(
                            "rows_render",
                            field,
                            relation_row[field.name],
                            relation_row,
                        )

        if compact and len(keys) == 1 and keys[0] != "_extra":  # pragma: no cover
            return t.cast(t.Self, row[keys[0]])
        return row


# backwards compat:
TypedRow = TypedTable

# note: at the bottom to prevent circular import issues:
from .fields import TypedField  # noqa: E402
from .query_builder import QueryBuilder  # noqa: E402
