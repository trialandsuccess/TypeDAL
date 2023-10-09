"""
Core functionality of TypeDAL.
"""
import contextlib
import csv
import datetime as dt
import types
import typing
import warnings
from decimal import Decimal
from typing import Any, Optional

import pydal
from pydal._globals import DEFAULT
from pydal.objects import Field, Query, Row, Rows
from pydal.objects import Table as _Table

from .helpers import all_annotations, instanciate, is_union, origin_is_subclass

# use typing.cast(type, ...) to make mypy happy with unions
T_annotation = typing.Type[Any] | types.UnionType
T_Query = typing.Union["Table", Query, bool, None, "TypedTable", typing.Type["TypedTable"]]
T_Value = typing.TypeVar("T_Value")  # actual type of the Field (via Generic)
T_MetaInstance = typing.TypeVar("T_MetaInstance", bound="TypedTable")  # bound="TypedTable"; bound="TableMeta"
T = typing.TypeVar("T")

BASIC_MAPPINGS: dict[T_annotation, str] = {
    str: "string",
    int: "integer",
    bool: "boolean",
    bytes: "blob",
    float: "double",
    object: "json",
    Decimal: "decimal(10,2)",
    dt.date: "date",
    dt.time: "time",
    dt.datetime: "datetime",
}


class _Types:
    """
    Internal type storage for stuff that mypy otherwise won't understand.
    """

    NONETYPE = type(None)


def is_typed_field(cls: Any) -> typing.TypeGuard["TypedField[Any]"]:
    return (
        isinstance(cls, TypedField)
        or isinstance(typing.get_origin(cls), type)
        and issubclass(typing.get_origin(cls), TypedField)
    )


class TypeDAL(pydal.DAL):  # type: ignore
    """
    Drop-in replacement for pyDAL with layer to convert class-based table definitions to classical pydal define_tables.
    """

    # dal: Table

    default_kwargs: typing.ClassVar[typing.Dict[str, Any]] = {
        # fields are 'required' (notnull) by default:
        "notnull": True,
    }

    def _define(self, cls: typing.Type[T]) -> typing.Type[T]:
        # when __future__.annotations is implemented, cls.__annotations__ will not work anymore as below.
        # proper way to handle this would be (but gives error right now due to Table implementing magic methods):
        # typing.get_type_hints(cls, globalns=None, localns=None)

        # dirty way (with evil eval):
        # [eval(v) for k, v in cls.__annotations__.items()]
        # this however also stops working when variables outside this scope or even references to other
        # objects are used. So for now, this package will NOT work when from __future__ import annotations is used,
        # and might break in the future, when this annotations behavior is enabled by default.

        # non-annotated variables have to be passed to define_table as kwargs

        tablename = self._to_snake(cls.__name__)
        # grab annotations of cls and it's parents:
        annotations = all_annotations(cls)
        # extend with `prop = TypedField()` 'annotations':
        annotations |= {k: typing.cast(type, v) for k, v in cls.__dict__.items() if is_typed_field(v)}
        # remove internal stuff:
        annotations = {k: v for k, v in annotations.items() if not k.startswith("_")}

        typedfields: dict[str, TypedField[Any]] = {
            k: instanciate(v) for k, v in annotations.items() if is_typed_field(v)
        }

        fields = {fname: self._to_field(fname, ftype) for fname, ftype in annotations.items()}
        other_kwargs = {k: v for k, v in cls.__dict__.items() if k not in annotations and not k.startswith("_")}

        for key in typedfields.keys() - cls.__dict__.keys():
            # typed fields that don't haven't been added to the object yet
            setattr(cls, key, typedfields[key])

        table: Table = self.define_table(tablename, *fields.values(), **other_kwargs)

        for name, typed_field in typedfields.items():
            field = fields[name]
            typed_field.bind(field, table)

        if issubclass(cls, TypedTable):
            cls.__set_internals__(db=self, table=table)
        else:
            warnings.warn("db.define used without inheriting TypedTable. " "This could lead to strange problems!")

        return cls

    @typing.overload
    def define(self, maybe_cls: None = None) -> typing.Callable[[typing.Type[T]], typing.Type[T]]:
        """
        Typing Overload for define without a class.

        @db.define()
        class MyTable(TypedTable): ...
        """

    @typing.overload
    def define(self, maybe_cls: typing.Type[T]) -> typing.Type[T]:
        """
        Typing Overload for define with a class.

        @db.define
        class MyTable(TypedTable): ...
        """

    def define(
        self, maybe_cls: typing.Type[T] | None = None
    ) -> typing.Type[T] | typing.Callable[[typing.Type[T]], typing.Type[T]]:
        """
        Can be used as a decorator on a class that inherits `TypedTable`, \
          or as a regular method if you need to define your classes before you have access to a 'db' instance.


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

        def wrapper(cls: typing.Type[T]) -> typing.Type[T]:
            return self._define(cls)

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
                _cls: typing.Type[TypedTable] = cls
                args[0] = _cls.id != None

        _set = super().__call__(*args, **kwargs)
        return typing.cast(TypedSet, _set)

    @classmethod
    def _build_field(cls, name: str, _type: str, **kw: Any) -> Field:
        return Field(name, _type, **{**cls.default_kwargs, **kw})

    @classmethod
    def _annotation_to_pydal_fieldtype(
        cls, _ftype: T_annotation, mut_kw: typing.MutableMapping[str, Any]
    ) -> Optional[str]:
        # ftype can be a union or type. typing.cast is sometimes used to tell mypy when it's not a union.
        ftype = typing.cast(type, _ftype)  # cast from typing.Type to type to make mypy happy)

        if isinstance(ftype, str):
            # extract type from string
            ftype = typing.get_args(typing.Type[ftype])[0]._evaluate(
                localns=locals(), globalns=globals(), recursive_guard=frozenset()
            )

        if mapping := BASIC_MAPPINGS.get(ftype):
            # basi types
            return mapping
        elif isinstance(ftype, _Table):
            # db.table
            return f"reference {ftype._tablename}"
        elif issubclass(type(ftype), type) and issubclass(ftype, TypedTable):
            # SomeTable
            snakename = cls._to_snake(ftype.__name__)
            return f"reference {snakename}"
        elif isinstance(ftype, TypedField):
            # FieldType(type, ...)
            return ftype._to_field(mut_kw)
        elif origin_is_subclass(ftype, TypedField):
            # TypedField[int]
            return cls._annotation_to_pydal_fieldtype(typing.get_args(ftype)[0], mut_kw)
        elif isinstance(ftype, types.GenericAlias) and typing.get_origin(ftype) in (list, TypedField):
            # list[str] -> str -> string -> list:string
            _child_type = typing.get_args(ftype)[0]
            _child_type = cls._annotation_to_pydal_fieldtype(_child_type, mut_kw)
            return f"list:{_child_type}"
        elif is_union(ftype):
            # str | int -> UnionType
            # typing.Union[str | int] -> typing._UnionGenericAlias

            # Optional[type] == type | None

            match typing.get_args(ftype):
                case (_child_type, _Types.NONETYPE) | (_Types.NONETYPE, _child_type):
                    # good union of Nullable

                    # if a field is optional, it is nullable:
                    mut_kw["notnull"] = False
                    return cls._annotation_to_pydal_fieldtype(_child_type, mut_kw)
                case _:
                    # two types is not supported by the db!
                    return None
        else:
            return None

    @classmethod
    def _to_field(cls, fname: str, ftype: type, **kw: Any) -> Field:
        """
        Convert a annotation into a pydal Field.

        Args:
            fname: name of the property
            ftype: annotation of the property
            kw: when using TypedField or a function returning it (e.g. StringField),
                keyword args can be used to pass any other settings you would normally to a pydal Field

        -> pydal.Field(fname, ftype, **kw)

        Example:
            class MyTable:
                fname: ftype
                id: int
                name: str
                reference: Table
                other: TypedField(str, default="John Doe")  # default will be in kwargs
        """
        fname = cls._to_snake(fname)

        if converted_type := cls._annotation_to_pydal_fieldtype(ftype, kw):
            return cls._build_field(fname, converted_type, **kw)
        else:
            raise NotImplementedError(f"Unsupported type {ftype}/{type(ftype)}")

    @staticmethod
    def _to_snake(camel: str) -> str:
        # https://stackoverflow.com/a/44969381
        return "".join([f"_{c.lower()}" if c.isupper() else c for c in camel]).lstrip("_")


class TableProtocol(typing.Protocol):
    id: int  # noqa: A003

    def __getitem__(self, item: str) -> Field:
        ...


class Table(_Table, TableProtocol):  # type: ignore
    ...


class TableMeta(type):
    # set up by db.define:
    # _db: TypeDAL | None = None
    # _table: Table | None = None
    _db: TypeDAL | None = None
    _table: Table | None = None

    #########################
    # TypeDAL custom logic: #
    #########################

    def __set_internals__(self, db: pydal.DAL, table: Table) -> None:
        """
        Store the related database and pydal table for later usage.
        """
        self._db = db
        self._table = table

    def __getattr__(self, col: str) -> Field:
        """
        Magic method used by TypedTableMeta to get a database field with dot notation on a class.

        Example:
            SomeTypedTable.col -> db.table.col (via TypedTableMeta.__getattr__)

        """
        if self._table:
            return getattr(self._table, col, None)

    def _ensure_defined(self) -> Table:
        if not self._table:
            raise EnvironmentError("@define or db.define is not called on this class yet!")
        return self._table

    def from_row(self: typing.Type[T_MetaInstance], row: pydal.objects.Row) -> T_MetaInstance:
        return self(row)

    def all(self: typing.Type[T_MetaInstance]) -> list[T_MetaInstance]:  # noqa: A003
        # todo: type?
        return list(self.select())

    ##########################
    # TypeDAL Modified Logic #
    ##########################

    def insert(self: typing.Type[T_MetaInstance], **fields: Any) -> T_MetaInstance:
        """
        This is only called when db.define is not used as a decorator.

        cls.__table functions as 'self'

        Args:
            **fields: anything you want to insert in the database

        Returns: the ID of the new row.

        """
        table = self._ensure_defined()

        result = table.insert(**fields)
        # it already is an int but mypy doesn't understand that
        return self(result)

    def bulk_insert(self: typing.Type[T_MetaInstance], items: list[dict[str, Any]]) -> list[T_MetaInstance]:
        # todo: list of instances?
        table = self._ensure_defined()
        result = table.bulk_insert(items)
        return [self(row_id) for row_id in result]

    def update_or_insert(self: typing.Type[T_MetaInstance], query: T_Query = DEFAULT, **values: Any) -> T_MetaInstance:
        table = self._ensure_defined()

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
        self: typing.Type[T_MetaInstance], **fields: Any
    ) -> tuple[Optional[T_MetaInstance], Optional[dict[str, str]]]:
        table = self._ensure_defined()
        result = table.validate_and_insert(**fields)
        if row_id := result.get("id"):
            return self(row_id), None
        else:
            return None, result.get("errors")

    def validate_and_update(
        self: typing.Type[T_MetaInstance], query: Query, **fields: Any
    ) -> tuple[Optional[T_MetaInstance], Optional[dict[str, str]]]:
        table = self._ensure_defined()

        try:
            result = table.validate_and_update(query, **fields)
        except Exception as e:
            result = {"errors": {"exception": str(e)}}

        if errors := result.get("errors"):
            return None, errors
        elif row_id := result.get("id"):
            return self(row_id), None
        else:
            # update on query without result
            return None, None

    def validate_and_update_or_insert(
        self: typing.Type[T_MetaInstance], query: Query, **fields: Any
    ) -> tuple[Optional[T_MetaInstance], Optional[dict[str, str]]]:
        table = self._ensure_defined()
        result = table.validate_and_update_or_insert(query, **fields)

        if errors := result.get("errors"):
            return None, errors
        elif row_id := result.get("id"):
            return self(row_id), None
        else:
            # update on query without result
            return None, None

    def select(self: typing.Type[T_MetaInstance], *a: Any, **kw: Any) -> "QueryBuilder[T_MetaInstance]":
        builder = QueryBuilder(self)
        return builder.select(*a, **kw)

    def where(self: typing.Type[T_MetaInstance], *a: Any, **kw: Any) -> "QueryBuilder[T_MetaInstance]":
        builder = QueryBuilder(self)
        return builder.where(*a, **kw)

    def count(self: typing.Type[T_MetaInstance]) -> int:
        return QueryBuilder(self).count()

    # todo: first, ... (query builder aliases)

    # todo: .belongs etc, check pydal code!

    @property
    def ALL(cls) -> pydal.objects.SQLALL:
        table = cls._ensure_defined()

        return table.ALL

    ##########################
    # TypeDAL Shadowed Logic #
    ##########################
    fields: list[str]

    # sanitize:

    def as_dict(self, flat: bool = False, sanitize: bool = True) -> dict[str, typing.Any]:
        table = self._ensure_defined()
        result = table.as_dict(flat, sanitize)
        return typing.cast(dict[str, typing.Any], result)

    def as_json(self, sanitize: bool = True) -> str:
        table = self._ensure_defined()
        return typing.cast(str, table.as_json(sanitize))

    def as_xml(self, sanitize: bool = True) -> str:
        table = self._ensure_defined()
        return typing.cast(str, table.as_xml(sanitize))

    def as_yaml(self, sanitize: bool = True) -> str:
        table = self._ensure_defined()
        return typing.cast(str, table.as_yaml(sanitize))

    def create_index(self, name: str, *fields: Field | str, **kwargs: Any) -> bool:
        table = self._ensure_defined()
        result = table.create_index(name, *fields, **kwargs)
        return typing.cast(bool, result)

    def drop(self, mode: str = "") -> None:
        table = self._ensure_defined()
        table.drop(mode)

    def drop_index(self, name: str, if_exists: bool = False) -> bool:
        table = self._ensure_defined()
        result = table.drop_index(name, if_exists)
        return typing.cast(bool, result)

    def import_from_csv_file(
        self,
        csvfile: typing.TextIO,
        id_map: dict[str, str] = None,
        null: str = "<NULL>",
        unique: str = "uuid",
        id_offset: dict[str, int] = None,  # id_offset used only when id_map is None
        transform: typing.Callable[[dict[Any, Any]], dict[Any, Any]] = None,
        validate: bool = False,
        encoding: str = "utf-8",
        delimiter: str = ",",
        quotechar: str = '"',
        quoting: int = csv.QUOTE_MINIMAL,
        restore: bool = False,
        **kwargs: Any,
    ) -> None:
        table = self._ensure_defined()
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

    def on(self, query: Query) -> pydal.objects.Expression:
        table = self._ensure_defined()
        return table.on(query)

    def with_alias(self, alias: str) -> _Table:
        table = self._ensure_defined()
        return table.with_alias(alias)

    # @typing.dataclass_transform()


class TypedTable(metaclass=TableMeta):
    # set up by 'new':
    _row: Row | None = None

    id: "TypedField[int]"  # noqa: A003

    def __new__(cls, row_or_id: Row | int | str | None = None, **filters: Any) -> "TypedTable":
        table = cls._ensure_defined()

        if isinstance(row_or_id, pydal.objects.Row):
            row = row_or_id
        elif row_or_id:
            row = table(row_or_id, **filters)
        else:
            row = table(**filters)

        if not row:
            return None  # type: ignore

        inst = super().__new__(cls)
        inst._row = row
        inst.__dict__.update(row)
        return inst

    def __int__(self) -> int:
        return self.id

    def __getattr__(self, item: str) -> Any:
        if self._row:
            return getattr(self._row, item)


# backwards compat:
TypedRow = TypedTable
T_Table = typing.TypeVar("T_Table", bound=TypedTable)


class QueryBuilder(typing.Generic[T_Table]):
    # todo: document select kwargs etc.
    model: typing.Type[T_Table]
    query: Query
    select_args: list[Any]
    select_kwargs: dict[str, Any]

    def __init__(
        self,
        model: typing.Type[T_Table],
        add_query: Optional[Query] = None,
        select_args: Optional[list[Any]] = None,
        select_kwargs: Optional[dict[str, Any]] = None,
    ):
        self.model = model
        table = model._ensure_defined()
        self.query = add_query or table.id > 0
        self.select_args = select_args or []
        self.select_kwargs = select_kwargs or {}

    def select(self, *fields: Any, **options: Any) -> "QueryBuilder[T_Table]":
        return QueryBuilder(self.model, self.query, self.select_args + list(fields), self.select_kwargs | options)

    def where(
        self, query_or_lambda: Query | typing.Callable[[typing.Type[T_Table]], Query], **filters: Any
    ) -> "QueryBuilder[T_Table]":
        new_query = self.query
        table = self.model._ensure_defined()

        for field, value in filters.items():
            new_query &= table[field] == value

        if not query_or_lambda:
            # okay
            pass
        elif isinstance(query_or_lambda, Query):
            new_query &= query_or_lambda
        elif callable(query_or_lambda):
            if result := query_or_lambda(self.model):
                new_query &= result
        elif isinstance(query_or_lambda, Field) or is_typed_field(query_or_lambda):
            new_query &= query_or_lambda != None
        else:
            raise ValueError(f"Unexpected query type ({type(query_or_lambda)}).")

        return QueryBuilder(
            self.model,
            new_query,
            self.select_args,
            self.select_kwargs,
        )

    def _get_db(self) -> TypeDAL:
        if db := self.model._db:
            return db
        else:
            raise EnvironmentError("@define or db.define is not called on this class yet!")

    def _build(self) -> "TypedRows[T_Table]":
        # todo: should maybe be renamed to .execute or something
        db = self._get_db()

        # print(db, self.query, self.select_args, self.select_kwargs)
        # print(db(self.query)._select(*self.select_args, **self.select_kwargs))
        rows: TypedRows[T_Table] = db(self.query).select(*self.select_args, **self.select_kwargs)

        return rows

    def __iter__(self) -> typing.Generator[T_Table, None, None]:
        yield from self._build()

    def count(self) -> int:
        db = self._get_db()
        return db(self.query).count()

    def first(self) -> T_Table | None:
        # todo: limitby
        row = self._build()[0]

        return self.model.from_row(row)


class TypedField(typing.Generic[T_Value]):
    """
    Typed version of pydal.Field, which will be converted to a normal Field in the background.
    """

    # will be set by .bind on db.define
    name = ""
    _db: Optional[pydal.DAL] = None
    _rname: Optional[str] = None
    _table: Optional[Table] = None
    _field: Optional[Field] = None

    _type: T_annotation
    kwargs: Any

    def __init__(self, _type: typing.Type[T_Value] = str, /, **settings: Any) -> None:  # type: ignore
        """
        A TypedFieldType should not be inited manually, but TypedField (from `fields.py`) should be used!
        """
        self._type = _type
        self.kwargs = settings
        super().__init__()

    @typing.overload
    def __get__(self, instance: T_Table, owner: typing.Type[T_Table]) -> T_Value:
        ...

    @typing.overload
    def __get__(self, instance: None, owner: typing.Type[TypedTable]) -> "TypedField[T_Value]":
        ...

    def __get__(
        self, instance: T_Table | None, owner: typing.Type[T_Table]
    ) -> typing.Union[T_Value, "TypedField[T_Value]"]:
        if instance:
            # never actually reached because a value was already stored in owner!
            return typing.cast(T_Value, instance)
        else:
            return self

    def __str__(self) -> str:
        """
        String representation of a Typed Field.

        If `type` is set explicitly (e.g. TypedField(str, type="text")), that type is used: `TypedField.text`,
        otherwise the type annotation is used (e.g. TypedField(str) -> TypedField.str)
        """

        return str(self._field) if self._field else ""

    def __repr__(self) -> str:
        """
        More detailed string representation of a Typed Field.

        Uses __str__ and adds the provided extra options (kwargs) in the representation.
        """
        s = self.__str__()

        if "type" in self.kwargs:
            # manual type in kwargs supplied
            t = self.kwargs["type"]
        elif issubclass(type, type(self._type)):
            # normal type, str.__name__ = 'str'
            t = getattr(self._type, "__name__", str(self._type))
        elif t_args := typing.get_args(self._type):
            # list[str] -> 'str'
            t = t_args[0].__name__
        else:  # pragma: no cover
            # fallback - something else, may not even happen, I'm not sure
            t = self._type

        s = f"TypedField[{t}].{s}" if s else f"TypedField[{t}]"

        kw = self.kwargs.copy()
        kw.pop("type", None)
        return f"<{s} with options {kw}>"

    def _to_field(self, extra_kwargs: typing.MutableMapping[str, Any]) -> Optional[str]:
        """
        Convert a Typed Field instance to a pydal.Field.
        """
        other_kwargs = self.kwargs.copy()
        extra_kwargs.update(other_kwargs)
        return extra_kwargs.pop("type", False) or TypeDAL._annotation_to_pydal_fieldtype(self._type, extra_kwargs)

    def bind(self, field: pydal.objects.Field, table: pydal.objects.Table) -> None:
        """
        Bind the right db/table/field info to this class, so queries can be made using `Class.field == ...`.
        """
        self._table = table
        self._field = field

    def __getattr__(self, key: str) -> Any:
        """
        If the regular getattribute does not work, try to get info from the related Field.
        """
        with contextlib.suppress(AttributeError):
            return super().__getattribute__(key)

        # try on actual field:
        return getattr(self._field, key)

    def __eq__(self, other: Any) -> Query:
        return self._field == other

    def __gt__(self, other: Any) -> Query:
        return self._field > other

    def __lt__(self, other: Any) -> Query:
        return self._field < other

    def __hash__(self) -> int:
        return hash(self._field)


S = typing.TypeVar("S")


class TypedRows(typing.Collection[S], Rows):  # type: ignore
    """
    Can be used as the return type of a .select().

    Example:
        people: TypedRows[Person] = db(Person).select()
    """

    def first(self) -> S | None:
        return typing.cast(S, super().first())


class TypedSet(pydal.objects.Set):  # type: ignore # pragma: no cover
    """
    Used to make pydal Set more typed.

    This class is not actually used, only 'cast' by TypeDAL.__call__
    """

    def count(self, distinct: bool = None, cache: dict[str, Any] = None) -> int:
        """
        Count returns an int.
        """
        result = super().count(distinct, cache)
        return typing.cast(int, result)

    def select(self, *fields: Any, **attributes: Any) -> TypedRows[T_Table]:
        """
        Select returns a TypedRows of a user defined table.

        Example:
            result: TypedRows[MyTable] = db(MyTable.id > 0).select()

            for row in result:
                typing.reveal_type(row)  # MyTable
        """
        rows = super().select(*fields, **attributes)
        return typing.cast(TypedRows[T_Table], rows)
