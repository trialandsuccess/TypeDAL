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

class TypeDAL(pydal.DAL):
    """@DynamicAttrs"""

    def define(self, cls):
        tablename = self._to_snake(cls.__name__)
        return self.define_table(
            tablename,
            *[self._to_field(fname, ftype)

              for fname, ftype in cls.__annotations__.items()
              ]
        )

    @classmethod
    def _to_field(cls, fname, ftype):
        fname = cls._to_snake(fname)

        if mapping := BASIC_MAPPINGS.get(ftype):
            # basi types
            return Field(fname, mapping)
        elif isinstance(ftype, Table):
            # db.table
            return Field(fname, f"reference {ftype._tablename}")
        elif type(ftype) is type and issubclass(ftype, TypedRow):
            # SomeTable
            snakename = cls._to_snake(ftype.__name__)
            return Field(fname, f"reference {snakename}")
        elif isinstance(ftype, TypedFieldType):
            # FieldType(type, ...)
            return ftype.to_field(fname)
        elif isinstance(ftype, types.GenericAlias):
            # list[...]
            _childtype = TypedFieldType._convert_generic_alias_list(ftype)
            return Field(fname, f"list:{_childtype}")
        elif isinstance(ftype, typing._UnionGenericAlias) or isinstance(ftype, types.UnionType):
            # typing.Optional[type] == type | None
            match ftype.__args__:
                case (_child_type, _Types.NONETYPE):
                    # good union
                    return cls._to_field(fname, _child_type)
                case other:
                    raise NotImplementedError(f"Invalid type union '{other}'")
        else:
            # todo: catch other types
            raise NotImplementedError(f"Unsupported type {ftype}/{type(ftype)}")

    @staticmethod
    def _to_snake(camel: str) -> str:
        # https://stackoverflow.com/a/44969381
        return ''.join(['_' + c.lower() if c.isupper() else c for c in camel]).lstrip('_')


class TypedRow(Row):
    id: int


class TypedRows(Rows):
    # list of TypedRow
    ...


class TypedFieldType(Field):
    def __init__(self, _type, **kwargs):
        self.type = _type
        self.kwargs = kwargs

    def to_field(self, name: str) -> Field:
        _type = self._to_field_type(self.type)
        if 'type' in self.kwargs:
            _type = self.kwargs.pop('type')
        return Field(name, _type, **self.kwargs)

    @classmethod
    def _to_field_type(cls, _type: type) -> str:
        # todo: merge with TypeDAL._to_field (kinda?)
        if mapping := BASIC_MAPPINGS.get(_type):
            # basic types
            return mapping
        elif type(_type) is type and issubclass(_type, TypedRow):
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


T = typing.TypeVar('T')


def TypedField(_type: T, **kwargs) -> T:
    # sneaky: het is een functie en geen class opdat er een return type is :)
    # en de return type (T) is de input type in _type
    return TypedFieldType(_type, **kwargs)
