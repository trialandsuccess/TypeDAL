"""
Core functionality of TypeDAL.
"""
import datetime as dt
import types
import typing
from collections import ChainMap
from decimal import Decimal

import pydal
from pydal.objects import Field, Query, Row, Rows, Table

# use typing.cast(type, ...) to make mypy happy with unions
T_annotation = typing.Type[typing.Any] | types.UnionType

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


# the input and output of TypeDAL.define
T = typing.TypeVar("T", typing.Type["TypedTable"], typing.Type["Table"])


def is_union(some_type: type) -> bool:
    """
    Check if a type is some type of Union.

    Args:
        some_type: types.UnionType = type(int | str); typing.Union = typing.Union[int, str]

    """
    return typing.get_origin(some_type) in (types.UnionType, typing.Union)


def _all_annotations(cls: type) -> ChainMap[str, type]:
    """
    Returns a dictionary-like ChainMap that includes annotations for all \
    attributes defined in cls or inherited from superclasses.
    """
    return ChainMap(*(c.__annotations__ for c in getattr(cls, "__mro__", []) if "__annotations__" in c.__dict__))


def all_annotations(cls: type, _except: typing.Iterable[str] = None) -> dict[str, type]:
    """
    Wrapper around `_all_annotations` that filters away any keys in _except.

    It also flattens the ChainMap to a regular dict.
    """
    if _except is None:
        _except = set()

    _all = _all_annotations(cls)
    return {k: v for k, v in _all.items() if k not in _except}


class TypeDAL(pydal.DAL):  # type: ignore
    """
    Drop-in replacement for pyDAL with layer to convert class-based table definitions to classical pydal define_tables.
    """

    dal: Table

    default_kwargs: typing.ClassVar[typing.Dict[str, typing.Any]] = {
        # fields are 'required' (notnull) by default:
        "notnull": True,
    }

    def define(self, cls: T) -> Table:
        """
        Can be used as a decorator on a class that inherits `TypedTable`, \
          or as a regular method if you need to define your classes before you have access to a 'db' instance.

        Args:
            cls:

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
        annotations |= {k: v for k, v in cls.__dict__.items() if isinstance(v, TypedFieldType)}
        # remove internal stuff:
        annotations = {k: v for k, v in annotations.items() if not k.startswith("_")}

        typedfields = {k: v for k, v in annotations.items() if isinstance(v, TypedFieldType)}

        fields = {fname: self._to_field(fname, ftype) for fname, ftype in annotations.items()}
        other_kwargs = {k: v for k, v in cls.__dict__.items() if k not in annotations and not k.startswith("_")}

        table: Table = self.define_table(tablename, *fields.values(), **other_kwargs)

        for name, typed_field in typedfields.items():
            field = fields[name]
            typed_field.bind(field, table)

        cls.__set_internals__(db=self, table=table)

        # the ACTUAL output is not TypedTable but rather pydal.Table
        # but telling the editor it is T helps with hinting.
        return table

    def __call__(self, *_args: Query | bool, **kwargs: typing.Any) -> pydal.objects.Set:
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

            if issubclass(type(cls), type) and issubclass(cls, TypedTable):
                # table defined without @db.define decorator!
                args[0] = cls.id != None

        return super().__call__(*args, **kwargs)

    # todo: insert etc shadowen?

    @classmethod
    def _build_field(cls, name: str, _type: str, **kw: typing.Any) -> Field:
        return Field(name, _type, **{**cls.default_kwargs, **kw})

    @classmethod
    def _annotation_to_pydal_fieldtype(
        cls, _ftype: T_annotation, mut_kw: typing.MutableMapping[str, typing.Any]
    ) -> typing.Optional[str]:
        # ftype can be a union or type. typing.cast is sometimes used to tell mypy when it's not a union.
        ftype = typing.cast(type, _ftype)  # cast from typing.Type to type to make mypy happy)

        if mapping := BASIC_MAPPINGS.get(ftype):
            # basi types
            return mapping
        elif isinstance(ftype, Table):
            # db.table
            return f"reference {ftype._tablename}"
        elif issubclass(type(ftype), type) and issubclass(ftype, TypedTable):
            # SomeTable
            snakename = cls._to_snake(ftype.__name__)
            return f"reference {snakename}"
        elif isinstance(ftype, TypedFieldType):
            # FieldType(type, ...)
            return ftype._to_field(mut_kw)
        elif isinstance(ftype, types.GenericAlias) and typing.get_origin(ftype) is list:
            # list[str] -> str -> string -> list:string
            _child_type = typing.get_args(ftype)[0]
            _child_type = cls._annotation_to_pydal_fieldtype(_child_type, mut_kw)
            return f"list:{_child_type}"
        elif is_union(ftype):
            # str | int -> UnionType
            # typing.Union[str | int] -> typing._UnionGenericAlias

            # typing.Optional[type] == type | None

            match typing.get_args(ftype):
                case (_child_type, _Types.NONETYPE):
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
    def _to_field(cls, fname: str, ftype: type, **kw: typing.Any) -> Field:
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


class TypedTableMeta(type):
    """
    Meta class allows getattribute on class variables instead instance variables.

    Used in `class TypedTable(Table, metaclass=TypedTableMeta)`
    """

    def __getattr__(self, key: str) -> Field:
        """
        The getattr method is only called when getattribute can't find something.

        `__get_table_column__` is defined in `TypedTable`
        """
        return self.__get_table_column__(key)


class TypedTable(Table, metaclass=TypedTableMeta):  # type: ignore
    """
    Typed version of pydal.Table, does not really do anything itself but forwards logic to pydal.
    """

    id: int  # noqa: 'id' has to be id since that's the db column

    # set up by db.define:
    __db: TypeDAL | None = None
    __table: Table | None = None

    @classmethod
    def __set_internals__(cls, db: pydal.DAL, table: Table) -> None:
        """
        Store the related database and pydal table for later usage.
        """
        cls.__db = db
        cls.__table = table

    @classmethod
    def __get_table_column__(cls, col: str) -> Field:
        """
        Magic method used by TypedTableMeta to get a database field with dot notation on a class.

        Example:
            SomeTypedTable.col -> db.table.col (via TypedTableMeta.__getattr__)

        """
        #
        if cls.__table:
            return cls.__table[col]

    def __new__(cls, *a: typing.Any, **kw: typing.Any) -> Row:  # or none!
        """
        When e.g. Table(id=0) is called without db.define, \
        this catches it and forwards for proper behavior.

        Args:
            *a: can be for example Table(<id>)
            **kw: can be for example Table(slug=<slug>)
        """
        if not cls.__table:
            raise EnvironmentError("@define or db.define is not called on this class yet!")
        return cls.__table(*a, **kw)

    @classmethod
    def insert(cls, **fields: typing.Any) -> int:
        """
        This is only called when db.define is not used as a decorator.

        cls.__table functions as 'self'

        Args:
            **fields: anything you want to insert in the database

        Returns: the ID of the new row.

        """
        if not cls.__table:
            raise EnvironmentError("@define or db.define is not called on this class yet!")

        result = super().insert(cls.__table, **fields)
        # it already is an int but mypy doesn't understand that
        return typing.cast(int, result)


# backwards compat:
TypedRow = TypedTable


class TypedFieldType(Field):  # type: ignore
    """
    Typed version of pydal.Field, which will be converted to a normal Field in the background.
    """

    # todo: .bind

    # will be set by .bind on db.define
    name = ""
    _db = None
    _rname = None
    _table = None

    _type: T_annotation
    kwargs: typing.Any

    def __init__(self, _type: T_annotation, **kwargs: typing.Any) -> None:
        """
        A TypedFieldType should not be inited manually, but TypedField (from `fields.py`) should be used!
        """
        self._type = _type
        self.kwargs = kwargs

    def __str__(self) -> str:
        """
        String representation of a Typed Field.

        If `type` is set explicitly (e.g. TypedField(str, type="text")), that type is used: `TypedField.text`,
        otherwise the type annotation is used (e.g. TypedField(str) -> TypedField.str)
        """
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
        return f"TypedField.{t}"

    def __repr__(self) -> str:
        """
        More detailed string representation of a Typed Field.

        Uses __str__ and adds the provided extra options (kwargs) in the representation.
        """
        s = self.__str__()
        kw = self.kwargs.copy()
        kw.pop("type", None)
        return f"<{s} with options {kw}>"

    def _to_field(self, extra_kwargs: typing.MutableMapping[str, typing.Any]) -> typing.Optional[str]:
        """
        Convert a Typed Field instance to a pydal.Field.
        """
        other_kwargs = self.kwargs.copy()
        extra_kwargs.update(other_kwargs)
        return extra_kwargs.pop("type", False) or TypeDAL._annotation_to_pydal_fieldtype(self._type, extra_kwargs)

    def bind(self, field: Field, table: Table) -> None:
        """
        Bind the right db/table/field info to this class, so queries can be made using `Class.field == ...`.
        """
        self.name = field.name
        self.type = field.type
        super().bind(table)

    # def __eq__(self, value):
    #     return Query(self.db, self._dialect.eq, self, value)


S = typing.TypeVar("S")


class TypedRows(typing.Collection[S], Rows):  # type: ignore
    """
    Can be used as the return type of a .select().

    Example:
        people: TypedRows[Person] = db(Person).select()
    """
