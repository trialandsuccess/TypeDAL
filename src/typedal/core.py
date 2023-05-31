import datetime as dt
import types
import typing
from decimal import Decimal

import pydal
from pydal.objects import Field, Query, Row, Rows, Table

BASIC_MAPPINGS = {
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
    Internal type storage for stuff that mypy otherwise won't understand
    """

    NONETYPE = type(None)


# the input and output of TypeDAL.define
T = typing.TypeVar("T", typing.Type["TypedTable"], typing.Type["Table"])


def is_union(some_type: type) -> bool:
    return typing.get_origin(some_type) in (types.UnionType, typing.Union)


class TypeDAL(pydal.DAL):  # type: ignore
    dal: Table

    default_kwargs = {
        # fields are 'required' (notnull) by default:
        "notnull": True,
    }

    def define(self, cls: T) -> Table:
        # when __future__.annotations is implemented, cls.__annotations__ will not work anymore as below.
        # proper way to handle this would be (but gives error right now due to Table implementing magic methods):
        # typing.get_type_hints(cls, globalns=None, localns=None)

        # dirty way (with evil eval):
        # [eval(v) for k, v in cls.__annotations__.items()]
        # this however also stops working when variables outside this scope or even references to other
        # objects are used. So for now, this package will NOT work when from __future__ import annotations is used,
        # and might break in the future, when this annotations behavior is enabled by default.
        # todo: add to caveats

        # non-annotated variables have to be passed to define_table as kwargs

        tablename = self._to_snake(cls.__name__)
        fields = [self._to_field(fname, ftype) for fname, ftype in cls.__annotations__.items()]
        other_kwargs = {k: v for k, v in cls.__dict__.items() if k not in cls.__annotations__ and not k.startswith("_")}

        table: Table = self.define_table(tablename, *fields, **other_kwargs)

        cls.__set_internals__(db=self, table=table)

        # the ACTUAL output is not TypedTable but rather pydal.Table
        # but telling the editor it is T helps with hinting.
        return table

    def __call__(self, *_args: Query, **kwargs: typing.Any) -> pydal.objects.Set:
        """
        A db instance can be called directly to perform a query.

        Usually, only a query is passed

        Example:
            db(query).select()

        """
        args = list(_args)
        if args:
            cls = args[0]
            if issubclass(type(cls), type) and issubclass(cls, TypedTable):
                # table defined without @db.define decorator!
                args[0] = cls.id != None

        return super().__call__(*args, **kwargs)

    # todo: insert etc shadowen?

    @classmethod
    def _build_field(cls, name: str, type: str, **kw: typing.Any) -> Field:
        return Field(name, type, **{**cls.default_kwargs, **kw})

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

        if mapping := BASIC_MAPPINGS.get(ftype):
            # basi types
            return cls._build_field(fname, mapping, **kw)
        elif isinstance(ftype, Table):
            # db.table
            return cls._build_field(fname, f"reference {ftype._tablename}", **kw)
        elif issubclass(type(ftype), type) and issubclass(ftype, TypedTable):
            # SomeTable
            snakename = cls._to_snake(ftype.__name__)
            return cls._build_field(fname, f"reference {snakename}", **kw)
        elif isinstance(ftype, TypedFieldType):
            # FieldType(type, ...)
            return ftype._to_field(fname, **kw)
        elif isinstance(ftype, types.GenericAlias):
            # list[...]
            _childtype = TypedFieldType._convert_generic_alias_list(ftype)
            return cls._build_field(fname, f"list:{_childtype}", **kw)
        elif is_union(ftype):
            # str | int -> UnionType
            # typing.Union[str | int] -> typing._UnionGenericAlias

            # typing.Optional[type] == type | None

            match typing.get_args(ftype):
                case (_child_type, _Types.NONETYPE):
                    # good union

                    # if a field is optional, it is nullable:
                    kw["notnull"] = False
                    return cls._to_field(fname, _child_type, **kw)
                case other:
                    raise NotImplementedError(f"Invalid type union '{other}'")
        else:
            # todo: catch other types
            raise NotImplementedError(f"Unsupported type {ftype}/{type(ftype)}")

    @staticmethod
    def _to_snake(camel: str) -> str:
        # https://stackoverflow.com/a/44969381
        return "".join([f"_{c.lower()}" if c.isupper() else c for c in camel]).lstrip("_")


class TypedTableMeta(type):
    # meta class allows getattribute on class variables instead instance variables
    def __getattr__(self, key: str) -> Field:
        # getattr is only called when getattribute can't find something
        return self.__get_table_column__(key)


class TypedTable(Table, metaclass=TypedTableMeta):  # type: ignore
    id: int

    # set up by db.define:
    __db: TypeDAL | None = None
    __table: Table | None = None

    @classmethod
    def __set_internals__(cls, db: pydal.DAL, table: Table) -> None:
        cls.__db = db
        cls.__table = table

    @classmethod
    def __get_table_column__(cls, col: str) -> Field:
        # db.table.col -> SomeTypedTable.col (via TypedTableMeta.__getattr__)
        if cls.__table:
            return cls.__table[col]

    def __new__(cls, *a: typing.Any, **kw: typing.Any) -> Row:  # or none!
        """
        when e.g. Table(id=0) is called without db.define,
        this catches it and forwards for proper behavior

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
        this is only called when db.define is not used as a decorator
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
    _table = "<any table>"
    type: type
    kwargs: typing.Any

    def __init__(self, _type: typing.Type[typing.Any], **kwargs: typing.Any) -> None:
        self.type = _type
        self.kwargs = kwargs

    def __repr__(self) -> str:
        s = self.__str__()
        return f"<{s} with options {self.kwargs}>"

    def __str__(self) -> str:
        if "type" in self.kwargs:
            t = self.kwargs["type"]
        else:
            t = self.type.__name__ if issubclass(type(self.type), type) else self.type
        return f"TypedField.{t}"

    def _to_field(self, name: str, **extra_kwargs: typing.Any) -> Field:
        other_kwargs = self.kwargs.copy()
        other_kwargs.update(extra_kwargs)
        if "type" in other_kwargs:
            _type = other_kwargs.pop("type")
        else:
            _type = self._to_field_type(self.type)

        return TypeDAL._build_field(name, _type, **other_kwargs)

    @classmethod
    def _to_field_type(cls, _type: typing.Type[typing.Any]) -> str:
        # todo: merge with TypeDAL._to_field (kinda?)
        if mapping := BASIC_MAPPINGS.get(_type):
            # basic types
            return mapping
        elif issubclass(type(_type), type) and issubclass(_type, TypedTable):
            # SomeTable
            snakename = TypeDAL._to_snake(_type.__name__)
            return f"reference {snakename}"
        elif isinstance(_type, Table):
            # db.sometable
            return f"reference {_type._tablename}"
        elif isinstance(_type, types.GenericAlias):
            # list[type]
            _child_type = cls._convert_generic_alias_list(_type)
            return f"list:{_child_type}"
        else:
            raise NotImplementedError(_type)

    @classmethod
    def _convert_generic_alias_list(cls, ftype: types.GenericAlias) -> str:
        """
        Extract the type from a generic alias: list[str] -> str.

        In `_to_field_type`, `list:<field>` will be created.

        Args:
            ftype: a complex annotation

        Returns: the corresponding Field type (as str) for the inner type.

        """
        # e.g. list[str]
        basetype = ftype.__origin__
        # e.g. list
        if basetype is not list:
            raise NotImplementedError("Only parameterized list is currently available.")
        childtype = ftype.__args__[0]

        return cls._to_field_type(childtype)


S = typing.TypeVar("S")


class TypedRows(typing.Collection[S], Rows):  # type: ignore
    """
    Can be used as the return type of a .select()
    e.g.

    people: TypedRows[Person] = db(Person).select()
    """
