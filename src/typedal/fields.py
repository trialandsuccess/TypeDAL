"""
This file contains available Field types.
"""

import ast
import datetime as dt
import decimal
import typing
import uuid

from pydal.helpers.classes import SQLCustomType
from pydal.objects import Table
from typing_extensions import Unpack

from .core import TypeDAL, TypedField, TypedTable
from .types import FieldSettings

T = typing.TypeVar("T", bound=typing.Any)


## general


## specific
def StringField(**kw: Unpack[FieldSettings]) -> TypedField[str]:
    """
    Pydal type is string, Python type is str.
    """
    kw["type"] = "string"
    return TypedField(str, **kw)


String = StringField


def TextField(**kw: Unpack[FieldSettings]) -> TypedField[str]:
    """
    Pydal type is text, Python type is str.
    """
    kw["type"] = "text"
    return TypedField(str, **kw)


Text = TextField


def BlobField(**kw: Unpack[FieldSettings]) -> TypedField[bytes]:
    """
    Pydal type is blob, Python type is bytes.
    """
    kw["type"] = "blob"
    return TypedField(bytes, **kw)


Blob = BlobField


def BooleanField(**kw: Unpack[FieldSettings]) -> TypedField[bool]:
    """
    Pydal type is boolean, Python type is bool.
    """
    kw["type"] = "boolean"
    return TypedField(bool, **kw)


Boolean = BooleanField


def IntegerField(**kw: Unpack[FieldSettings]) -> TypedField[int]:
    """
    Pydal type is integer, Python type is int.
    """
    kw["type"] = "integer"
    return TypedField(int, **kw)


Integer = IntegerField


def DoubleField(**kw: Unpack[FieldSettings]) -> TypedField[float]:
    """
    Pydal type is double, Python type is float.
    """
    kw["type"] = "double"
    return TypedField(float, **kw)


Double = DoubleField


def DecimalField(n: int, m: int, **kw: Unpack[FieldSettings]) -> TypedField[decimal.Decimal]:
    """
    Pydal type is decimal, Python type is Decimal.
    """
    kw["type"] = f"decimal({n}, {m})"
    return TypedField(decimal.Decimal, **kw)


Decimal = DecimalField


def DateField(**kw: Unpack[FieldSettings]) -> TypedField[dt.date]:
    """
    Pydal type is date, Python type is datetime.date.
    """
    kw["type"] = "date"
    return TypedField(dt.date, **kw)


Date = DateField


def TimeField(**kw: Unpack[FieldSettings]) -> TypedField[dt.time]:
    """
    Pydal type is time, Python type is datetime.time.
    """
    kw["type"] = "time"
    return TypedField(dt.time, **kw)


Time = TimeField


def DatetimeField(**kw: Unpack[FieldSettings]) -> TypedField[dt.datetime]:
    """
    Pydal type is datetime, Python type is datetime.datetime.
    """
    kw["type"] = "datetime"
    return TypedField(dt.datetime, **kw)


Datetime = DatetimeField


def PasswordField(**kw: Unpack[FieldSettings]) -> TypedField[str]:
    """
    Pydal type is password, Python type is str.
    """
    kw["type"] = "password"
    return TypedField(str, **kw)


Password = PasswordField


def UploadField(**kw: Unpack[FieldSettings]) -> TypedField[str]:
    """
    Pydal type is upload, Python type is str.
    """
    kw["type"] = "upload"
    return TypedField(str, **kw)


Upload = UploadField

T_subclass = typing.TypeVar("T_subclass", TypedTable, Table)


def ReferenceField(
    other_table: str | typing.Type[TypedTable] | TypedTable | Table | T_subclass, **kw: Unpack[FieldSettings]
) -> TypedField[int]:
    """
    Pydal type is reference, Python type is int (id).
    """
    if isinstance(other_table, str):
        kw["type"] = f"reference {other_table}"
    elif isinstance(other_table, Table):
        # db.table
        kw["type"] = f"reference {other_table._tablename}"
    elif isinstance(other_table, type) and issubclass(other_table, TypedTable):
        # SomeTable
        snakename = TypeDAL.to_snake(other_table.__name__)
        kw["type"] = f"reference {snakename}"
    else:
        raise ValueError(f"Don't know what to do with {type(other_table)}")

    return TypedField(int, **kw)


Reference = ReferenceField


def ListStringField(**kw: Unpack[FieldSettings]) -> TypedField[list[str]]:
    """
    Pydal type is list:string, Python type is list of str.
    """
    kw["type"] = "list:string"
    return TypedField(list[str], **kw)


ListString = ListStringField


def ListIntegerField(**kw: Unpack[FieldSettings]) -> TypedField[list[int]]:
    """
    Pydal type is list:integer, Python type is list of int.
    """
    kw["type"] = "list:integer"
    return TypedField(list[int], **kw)


ListInteger = ListIntegerField


def ListReferenceField(other_table: str, **kw: Unpack[FieldSettings]) -> TypedField[list[int]]:
    """
    Pydal type is list:reference, Python type is list of int (id).
    """
    kw["type"] = f"list:reference {other_table}"
    return TypedField(list[int], **kw)


ListReference = ListReferenceField


def JSONField(**kw: Unpack[FieldSettings]) -> TypedField[object]:
    """
    Pydal type is json, Python type is object (can be anything JSON-encodable).
    """
    kw["type"] = "json"
    return TypedField(object, **kw)


def BigintField(**kw: Unpack[FieldSettings]) -> TypedField[int]:
    """
    Pydal type is bigint, Python type is int.
    """
    kw["type"] = "bigint"
    return TypedField(int, **kw)


Bigint = BigintField

## Custom:

NativeTimestampField = SQLCustomType(
    type="datetime",
    native="timestamp",
    encoder=lambda x: f"'{x}'",  # extra quotes
    # decoder=lambda x: x, # already parsed into datetime
)


def TimestampField(**kw: Unpack[FieldSettings]) -> TypedField[dt.datetime]:
    """
    Database type is timestamp, Python type is datetime.

    Advantage over the regular datetime type is that
    a timestamp has millisecond precision (2024-10-11 20:18:24.505194)
    whereas a regular datetime only has precision up to the second (2024-10-11 20:18:24)
    """
    kw["type"] = NativeTimestampField
    return TypedField(
        dt.datetime,
        **kw,
    )


NativePointField = SQLCustomType(
    type="string",
    native="point",
    encoder=str,
    decoder=ast.literal_eval,
)


def PointField(**kw: Unpack[FieldSettings]) -> TypedField[tuple[float, float]]:
    """
    Database type is point, Python type is tuple[float, float].
    """
    kw["type"] = NativePointField
    return TypedField(tuple[float, float], **kw)


NativeUUIDField = SQLCustomType(
    type="string",
    native="uuid",
    encoder=str,
    decoder=lambda value: uuid.UUID(value) if value else None,
)


def UUIDField(**kw: Unpack[FieldSettings]) -> TypedField[uuid.UUID]:
    """
    Database type is uuid, Python type is UUID.
    """
    kw["type"] = NativeUUIDField
    return TypedField(uuid.UUID, **kw)
