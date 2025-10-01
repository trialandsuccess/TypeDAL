"""
This file contains available Field types.
"""

from __future__ import annotations

import ast
import contextlib
import datetime as dt
import decimal
import types
import typing as t
import uuid

import pydal
from pydal.helpers.classes import SQLCustomType
from pydal.objects import Table

from .core import TypeDAL
from .types import (
    Expression,
    Field,
    FieldSettings,
    Query,
    T_annotation,
    T_MetaInstance,
    T_subclass,
    T_Value,
    Validator,
)

if t.TYPE_CHECKING:
    # will be imported for real later:
    from .tables import TypedTable


## general


class TypedField(Expression, t.Generic[T_Value]):  # pragma: no cover
    """
    Typed version of pydal.Field, which will be converted to a normal Field in the background.
    """

    # will be set by .bind on db.define
    name = ""
    _db: t.Optional[pydal.DAL] = None
    _rname: t.Optional[str] = None
    _table: t.Optional[Table] = None
    _field: t.Optional[Field] = None

    _type: T_annotation
    kwargs: t.Any

    requires: Validator | t.Iterable[Validator]

    # NOTE: for the logic of converting a TypedField into a pydal Field, see TypeDAL._to_field

    def __init__(
        self,
        _type: t.Type[T_Value] | types.UnionType = str,  # type: ignore
        /,
        **settings: t.Unpack[FieldSettings],
    ) -> None:
        """
        Typed version of pydal.Field, which will be converted to a normal Field in the background.

        Provide the Python type for this field as the first positional argument
        and any other settings to Field() as keyword parameters.
        """
        self._type = _type
        self.kwargs = settings
        # super().__init__()

    @t.overload
    def __get__(self, instance: T_MetaInstance, owner: t.Type[T_MetaInstance]) -> T_Value:  # pragma: no cover
        """
        row.field -> (actual data).
        """

    @t.overload
    def __get__(self, instance: None, owner: "t.Type[TypedTable]") -> "TypedField[T_Value]":  # pragma: no cover
        """
        Table.field -> Field.
        """

    def __get__(
        self,
        instance: T_MetaInstance | None,
        owner: t.Type[T_MetaInstance],
    ) -> t.Union[T_Value, "TypedField[T_Value]"]:
        """
        Since this class is a Descriptor field, \
            it returns something else depending on if it's called on a class or instance.

        (this is mostly for mypy/typing)
        """
        if instance:
            # this is only reached in a very specific case:
            # an instance of the object was created with a specific set of fields selected (excluding the current one)
            # in that case, no value was stored in the owner -> return None (since the field was not selected)
            return t.cast(T_Value, None)  # cast as T_Value so mypy understands it for selected fields
        else:
            # getting as class -> return actual field so pydal understands it when using in query etc.
            return t.cast(TypedField[T_Value], self._field)  # pretend it's still typed for IDE support

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
        string_value = self.__str__()

        if "type" in self.kwargs:
            # manual type in kwargs supplied
            typename = self.kwargs["type"]
        elif issubclass(type, type(self._type)):
            # normal type, str.__name__ = 'str'
            typename = getattr(self._type, "__name__", str(self._type))
        elif t_args := t.get_args(self._type):
            # list[str] -> 'str'
            typename = t_args[0].__name__
        else:  # pragma: no cover
            # fallback - something else, may not even happen, I'm not sure
            typename = self._type

        string_value = f"TypedField[{typename}].{string_value}" if string_value else f"TypedField[{typename}]"

        kw = self.kwargs.copy()
        kw.pop("type", None)
        return f"<{string_value} with options {kw}>"

    def _to_field(self, extra_kwargs: t.MutableMapping[str, t.Any], builder: TableDefinitionBuilder) -> t.Optional[str]:
        """
        Convert a Typed Field instance to a pydal.Field.

        Actual logic in TypeDAL._to_field but this function creates the pydal type name and updates the kwarg settings.
        """
        other_kwargs = self.kwargs.copy()
        extra_kwargs.update(other_kwargs)  # <- modifies and overwrites the default kwargs with user-specified ones
        return extra_kwargs.pop("type", False) or builder.annotation_to_pydal_fieldtype(
            self._type,
            extra_kwargs,
        )

    def bind(self, field: pydal.objects.Field, table: pydal.objects.Table) -> None:
        """
        Bind the right db/table/field info to this class, so queries can be made using `Class.field == ...`.
        """
        self._table = table
        self._field = field

    def __getattr__(self, key: str) -> t.Any:
        """
        If the regular getattribute does not work, try to get info from the related Field.
        """
        with contextlib.suppress(AttributeError):
            return super().__getattribute__(key)

        # try on actual field:
        return getattr(self._field, key)

    def __eq__(self, other: t.Any) -> Query:
        """
        Performing == on a Field will result in a Query.
        """
        return t.cast(Query, self._field == other)

    def __ne__(self, other: t.Any) -> Query:
        """
        Performing != on a Field will result in a Query.
        """
        return t.cast(Query, self._field != other)

    def __gt__(self, other: t.Any) -> Query:
        """
        Performing > on a Field will result in a Query.
        """
        return t.cast(Query, self._field > other)

    def __lt__(self, other: t.Any) -> Query:
        """
        Performing < on a Field will result in a Query.
        """
        return t.cast(Query, self._field < other)

    def __ge__(self, other: t.Any) -> Query:
        """
        Performing >= on a Field will result in a Query.
        """
        return t.cast(Query, self._field >= other)

    def __le__(self, other: t.Any) -> Query:
        """
        Performing <= on a Field will result in a Query.
        """
        return t.cast(Query, self._field <= other)

    def __hash__(self) -> int:
        """
        Shadow Field.__hash__.
        """
        return hash(self._field)

    def __invert__(self) -> Expression:
        """
        Performing ~ on a Field will result in an Expression.
        """
        if not self._field:  # pragma: no cover
            raise ValueError("Unbound Field can not be inverted!")

        return t.cast(Expression, ~self._field)

    def lower(self) -> Expression:
        """
        For string-fields: compare lowercased values.
        """
        if not self._field:  # pragma: no cover
            raise ValueError("Unbound Field can not be lowered!")

        return t.cast(Expression, self._field.lower())


def is_typed_field(cls: t.Any) -> t.TypeGuard["TypedField[t.Any]"]:
    """
    Is `cls` an instance or subclass of TypedField?

    Deprecated
    """
    return isinstance(cls, TypedField) or (
        isinstance(t.get_origin(cls), type) and issubclass(t.get_origin(cls), TypedField)
    )


## specific
def StringField(**kw: t.Unpack[FieldSettings]) -> TypedField[str]:
    """
    Pydal type is string, Python type is str.
    """
    kw["type"] = "string"
    return TypedField(str, **kw)


String = StringField


def TextField(**kw: t.Unpack[FieldSettings]) -> TypedField[str]:
    """
    Pydal type is text, Python type is str.
    """
    kw["type"] = "text"
    return TypedField(str, **kw)


Text = TextField


def BlobField(**kw: t.Unpack[FieldSettings]) -> TypedField[bytes]:
    """
    Pydal type is blob, Python type is bytes.
    """
    kw["type"] = "blob"
    return TypedField(bytes, **kw)


Blob = BlobField


def BooleanField(**kw: t.Unpack[FieldSettings]) -> TypedField[bool]:
    """
    Pydal type is boolean, Python type is bool.
    """
    kw["type"] = "boolean"
    return TypedField(bool, **kw)


Boolean = BooleanField


def IntegerField(**kw: t.Unpack[FieldSettings]) -> TypedField[int]:
    """
    Pydal type is integer, Python type is int.
    """
    kw["type"] = "integer"
    return TypedField(int, **kw)


Integer = IntegerField


def DoubleField(**kw: t.Unpack[FieldSettings]) -> TypedField[float]:
    """
    Pydal type is double, Python type is float.
    """
    kw["type"] = "double"
    return TypedField(float, **kw)


Double = DoubleField


def DecimalField(n: int, m: int, **kw: t.Unpack[FieldSettings]) -> TypedField[decimal.Decimal]:
    """
    Pydal type is decimal, Python type is Decimal.
    """
    kw["type"] = f"decimal({n}, {m})"
    return TypedField(decimal.Decimal, **kw)


Decimal = DecimalField


def DateField(**kw: t.Unpack[FieldSettings]) -> TypedField[dt.date]:
    """
    Pydal type is date, Python type is datetime.date.
    """
    kw["type"] = "date"
    return TypedField(dt.date, **kw)


Date = DateField


def TimeField(**kw: t.Unpack[FieldSettings]) -> TypedField[dt.time]:
    """
    Pydal type is time, Python type is datetime.time.
    """
    kw["type"] = "time"
    return TypedField(dt.time, **kw)


Time = TimeField


def DatetimeField(**kw: t.Unpack[FieldSettings]) -> TypedField[dt.datetime]:
    """
    Pydal type is datetime, Python type is datetime.datetime.
    """
    kw["type"] = "datetime"
    return TypedField(dt.datetime, **kw)


Datetime = DatetimeField


def PasswordField(**kw: t.Unpack[FieldSettings]) -> TypedField[str]:
    """
    Pydal type is password, Python type is str.
    """
    kw["type"] = "password"
    return TypedField(str, **kw)


Password = PasswordField


def UploadField(**kw: t.Unpack[FieldSettings]) -> TypedField[str]:
    """
    Pydal type is upload, Python type is str.
    """
    kw["type"] = "upload"
    return TypedField(str, **kw)


Upload = UploadField


def ReferenceField(
    other_table: str | t.Type[TypedTable] | TypedTable | Table | T_subclass,
    **kw: t.Unpack[FieldSettings],
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


def ListStringField(**kw: t.Unpack[FieldSettings]) -> TypedField[list[str]]:
    """
    Pydal type is list:string, Python type is list of str.
    """
    kw["type"] = "list:string"
    return TypedField(list[str], **kw)


ListString = ListStringField


def ListIntegerField(**kw: t.Unpack[FieldSettings]) -> TypedField[list[int]]:
    """
    Pydal type is list:integer, Python type is list of int.
    """
    kw["type"] = "list:integer"
    return TypedField(list[int], **kw)


ListInteger = ListIntegerField


def ListReferenceField(other_table: str, **kw: t.Unpack[FieldSettings]) -> TypedField[list[int]]:
    """
    Pydal type is list:reference, Python type is list of int (id).
    """
    kw["type"] = f"list:reference {other_table}"
    return TypedField(list[int], **kw)


ListReference = ListReferenceField


def JSONField(**kw: t.Unpack[FieldSettings]) -> TypedField[object]:
    """
    Pydal type is json, Python type is object (can be anything JSON-encodable).
    """
    kw["type"] = "json"
    return TypedField(object, **kw)


def BigintField(**kw: t.Unpack[FieldSettings]) -> TypedField[int]:
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


def TimestampField(**kw: t.Unpack[FieldSettings]) -> TypedField[dt.datetime]:
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


def safe_decode_native_point(value: str | None) -> tuple[float, ...]:
    """
    Safely decode a string into a tuple of floats.

    The function attempts to parse the input string using `ast.literal_eval`.
    If the parsing is successful, the function casts the parsed value to a tuple of floats and returns it.
    Otherwise, the function returns an empty tuple.

    Args:
        value: The string to decode.

    Returns:
        A tuple of floats.
    """
    if not value:
        return ()

    try:
        parsed = ast.literal_eval(value)
        return t.cast(tuple[float, ...], parsed)
    except ValueError:  # pragma: no cover
        # should not happen when inserted with `safe_encode_native_point` but you never know
        return ()


def safe_encode_native_point(value: tuple[str, str] | tuple[float, float] | str) -> str:
    """

    Safe encodes a point value.

    The function takes a point value as input.
    It can be a string in the format "x,y" or a tuple of two numbers.
    The function converts the string to a tuple if necessary, validates the tuple,
    and formats it into the expected string format.

    Args:
        value: The point value to be encoded.

    Returns:
        The encoded point value as a string in the format "x,y".

    Raises:
        ValueError: If the input value is not a valid point.
    """
    if not value:
        return ""

    # Convert string to tuple if needed
    if isinstance(value, str):
        value = value.strip("() ")
        if not value:
            return ""
        value_tup = tuple(float(x.strip()) for x in value.split(","))
    else:
        value_tup = value  # type: ignore

    # Validate and format
    if len(value_tup) != 2:
        raise ValueError("Point must have exactly 2 coordinates")

    x, y = value_tup
    return f"({x},{y})"


NativePointField = SQLCustomType(
    type="string",
    native="point",
    encoder=safe_encode_native_point,
    decoder=safe_decode_native_point,
)


def PointField(**kw: t.Unpack[FieldSettings]) -> TypedField[tuple[float, float]]:
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


def UUIDField(**kw: t.Unpack[FieldSettings]) -> TypedField[uuid.UUID]:
    """
    Database type is uuid, Python type is UUID.
    """
    kw["type"] = NativeUUIDField
    return TypedField(uuid.UUID, **kw)


# note: import at the end to prevent circular imports:
from .define import TableDefinitionBuilder  # noqa: E402
from .tables import TypedTable  # noqa: E402
