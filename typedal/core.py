import pydal
from pydal.objects import Row, Rows, Field, Table  # *?
import typing
import types
import datetime as dt
from decimal import Decimal

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
    NONETYPE = type(None)


# the input and output of TypeDAL.define
T = typing.TypeVar("T", typing.Type["TypedTable"], typing.Type["Table"])


class TypeDAL(pydal.DAL):
    """@DynamicAttrs"""

    dal: Table

    default_kwargs = {
        # fields are 'required' (notnull) by default:
        "notnull": True,
    }

    def define(self, cls: T) -> T:
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
        fields = [
            self._to_field(fname, ftype) for fname, ftype in cls.__annotations__.items()
        ]
        other_kwargs = {
            k: v
            for k, v in cls.__dict__.items()
            if k not in cls.__annotations__ and not k.startswith("_")
        }

        table = self.define_table(tablename, *fields, **other_kwargs)

        cls.__set_internals__(db=self, table=table)

        # the ACTUAL output is not TypedTable but rather pydal.Table
        # but telling the editor it is T helps with hinting.
        return table

    def __call__(self, *args, **kwargs) -> pydal.objects.Set:
        args = list(args)
        if args:
            cls = args[0]
            if issubclass(type(cls), type) and issubclass(cls, TypedTable):
                # table defined without @db.define decorator!
                args[0] = cls.id != None

        return super().__call__(*args, **kwargs)

    # todo: insert etc shadowen?

    @classmethod
    def _build_field(cls, name, type, **kw):
        return Field(name, type, **{**cls.default_kwargs, **kw})

    @classmethod
    def _to_field(cls, fname: str, ftype: type, **kw):
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
        elif isinstance(ftype, typing._UnionGenericAlias) or isinstance(
            ftype, types.UnionType
        ):
            # typing.Optional[type] == type | None
            match ftype.__args__:
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
        return "".join(["_" + c.lower() if c.isupper() else c for c in camel]).lstrip(
            "_"
        )


class TypedTableMeta(type):
    # meta class allows getattribute on class variables instead instance variables
    def __getattr__(self, key):
        # getattr is only called when getattribute can't find something
        return self.__get_table_column__(key)


class TypedTable(Table, metaclass=TypedTableMeta):
    id: int

    # set up by db.define:
    __db: TypeDAL = None
    __table: Table = None

    @classmethod
    def __set_internals__(cls, db, table):
        cls.__db = db
        cls.__table = table

    @classmethod
    def __get_table_column__(cls, col):
        # db.table.col -> SomeTypedTable.col (via TypedTableMeta.__getattr__)
        if cls.__table:
            return cls.__table[col]

    def __new__(cls, *a, **kw):
        # when e.g. Table(id=0) is called without db.define,
        # this catches it and forwards for proper behavior
        return cls.__table(*a, **kw)

    @classmethod
    def insert(cls, **fields):
        # this is only called when db.define is not used as a decorator
        # cls.__table functions as 'self'
        super().insert(cls.__table, **fields)


# backwards compat:
TypedRow = TypedTable


class TypedFieldType(Field):
    _table = "<any table>"

    def __init__(self, _type, **kwargs):
        self.type = _type
        self.kwargs = kwargs

    def __repr__(self):
        s = self.__str__()
        return f"<{s} with options {self.kwargs}>"

    def __str__(self):
        if "type" in self.kwargs:
            t = self.kwargs["type"]
        else:
            t = self.type.__name__ if issubclass(type(self.type), type) else self.type
        return f"TypedField.{t}"

    def _to_field(self, name: str, **extra_kwargs) -> Field:
        other_kwargs = self.kwargs.copy()
        other_kwargs.update(extra_kwargs)
        if "type" in other_kwargs:
            _type = other_kwargs.pop("type")
        else:
            _type = self._to_field_type(self.type)

        return TypeDAL._build_field(name, _type, **other_kwargs)

    @classmethod
    def _to_field_type(cls, _type: type) -> str:
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
    def _convert_generic_alias_list(cls, ftype):
        # e.g. list[str
        basetype = ftype.__origin__
        # e.g. list
        if basetype is not list:
            raise NotImplementedError("Only parameterized list is currently available.")
        childtype = ftype.__args__[0]

        return cls._to_field_type(childtype)


S = typing.TypeVar("S")


class TypedRows(typing.Collection[S], Rows):
    """
    Can be used as the return type of a .select()
    e.g.

    people: TypedRows[Person] = db(Person).select()
    """
