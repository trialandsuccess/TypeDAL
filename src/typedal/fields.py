"""
This file contains available Field types.
"""

import datetime as dt
import decimal
import types
import typing

from pydal.objects import Table

from .core import TypeDAL, TypedFieldType, TypedTable

T = typing.TypeVar("T", bound=typing.Type[typing.Any])


## general


def TypedField(
    _type: T | types.UnionType,
    **kwargs: typing.Any,
) -> T:
    """
    sneaky: its a function and not a class, because there's a return type.

    and the return type (T) is the input type in _type

    Example:
        age: TypedField(int, default=18)
    """
    return TypedFieldType(_type, **kwargs)


TYPE_STR = typing.Type[str]
TYPE_INT = typing.Type[int]
TYPE_LIST_OF_INT = typing.Type[list[int]]


## specific
def StringField(**kw: typing.Any) -> TYPE_STR:
    """
    Pydal type is string, Python type is str.
    """
    kw["type"] = "string"
    return TypedField(str, **kw)


String = StringField


def TextField(**kw: typing.Any) -> TYPE_STR:
    """
    Pydal type is text, Python type is str.
    """
    kw["type"] = "text"
    return TypedField(str, **kw)


Text = TextField


def BlobField(**kw: typing.Any) -> typing.Type[bytes]:
    """
    Pydal type is blob, Python type is bytes.
    """
    kw["type"] = "blob"
    return TypedField(bytes, **kw)


Blob = BlobField


def BooleanField(**kw: typing.Any) -> typing.Type[bool]:
    """
    Pydal type is boolean, Python type is bool.
    """
    kw["type"] = "boolean"
    return TypedField(bool, **kw)


Boolean = BooleanField


def IntegerField(**kw: typing.Any) -> typing.Type[int]:
    """
    Pydal type is integer, Python type is int.
    """
    kw["type"] = "integer"
    return TypedField(int, **kw)


Integer = IntegerField


def DoubleField(**kw: typing.Any) -> typing.Type[float]:
    """
    Pydal type is double, Python type is float.
    """
    kw["type"] = "double"
    return TypedField(float, **kw)


Double = DoubleField


def DecimalField(n: int, m: int, **kw: typing.Any) -> typing.Type[decimal.Decimal]:
    """
    Pydal type is decimal, Python type is Decimal.
    """
    kw["type"] = f"decimal({n}, {m})"
    return TypedField(decimal.Decimal, **kw)


Decimal = DecimalField


def DateField(**kw: typing.Any) -> typing.Type[dt.date]:
    """
    Pydal type is date, Python type is datetime.date.
    """
    kw["type"] = "date"
    return TypedField(dt.date, **kw)


Date = DateField


def TimeField(**kw: typing.Any) -> typing.Type[dt.time]:
    """
    Pydal type is time, Python type is datetime.time.
    """
    kw["type"] = "time"
    return TypedField(dt.time, **kw)


Time = TimeField


def DatetimeField(**kw: typing.Any) -> typing.Type[dt.datetime]:
    """
    Pydal type is datetime, Python type is datetime.datetime.
    """
    kw["type"] = "datetime"
    return TypedField(dt.datetime, **kw)


Datetime = DatetimeField


def PasswordField(**kw: typing.Any) -> TYPE_STR:
    """
    Pydal type is password, Python type is str.
    """
    kw["type"] = "password"
    return TypedField(str, **kw)


Password = PasswordField


def UploadField(**kw: typing.Any) -> TYPE_STR:
    """
    Pydal type is upload, Python type is str.
    """
    kw["type"] = "upload"
    return TypedField(str, **kw)


Upload = UploadField

T_subclass = typing.TypeVar("T_subclass", TypedTable, Table)


def ReferenceField(other_table: str | TypedTable | Table | T_subclass, **kw: typing.Any) -> TYPE_INT:
    """
    Pydal type is reference, Python type is int (id).
    """
    if isinstance(other_table, str):
        kw["type"] = f"reference {other_table}"
    elif isinstance(other_table, Table):
        # db.table
        kw["type"] = f"reference {other_table._tablename}"
    elif issubclass(type(other_table), type) and issubclass(other_table, TypedTable):
        # SomeTable
        snakename = TypeDAL._to_snake(other_table.__name__)
        kw["type"] = f"reference {snakename}"
    else:
        raise ValueError(f"Don't know what to do with {type(other_table)}")

    return TypedField(int, **kw)


Reference = ReferenceField


def ListStringField(**kw: typing.Any) -> typing.Type[list[str]]:
    """
    Pydal type is list:string, Python type is list of str.
    """
    kw["type"] = "list:string"
    return TypedField(list[str], **kw)


ListString = ListStringField


def ListIntegerField(**kw: typing.Any) -> TYPE_LIST_OF_INT:
    """
    Pydal type is list:integer, Python type is list of int.
    """
    kw["type"] = "list:integer"
    return TypedField(list[int], **kw)


ListInteger = ListIntegerField


def ListReferenceField(other_table: str, **kw: typing.Any) -> TYPE_LIST_OF_INT:
    """
    Pydal type is list:reference, Python type is list of int (id).
    """
    kw["type"] = f"list:reference {other_table}"
    return TypedField(list[int], **kw)


ListReference = ListReferenceField


def JSONField(**kw: typing.Any) -> typing.Type[object]:
    """
    Pydal type is json, Python type is object (can be anything JSON-encodable).
    """
    kw["type"] = "json"
    return TypedField(object, **kw)


def BigintField(**kw: typing.Any) -> TYPE_INT:
    """
    Pydal type is bigint, Python type is int.
    """
    kw["type"] = "bigint"
    return TypedField(int, **kw)


Bigint = BigintField