"""
Helpers that work independently of core.
"""

import datetime as dt
import fnmatch
import io
import types
import typing
from collections import ChainMap
from typing import Any

from pydal import DAL

from .types import AnyDict, Field, Table

if typing.TYPE_CHECKING:
    from . import TypeDAL, TypedField, TypedTable  # noqa: F401

T = typing.TypeVar("T")


def is_union(some_type: type | types.UnionType) -> bool:
    """
    Check if a type is some type of Union.

    Args:
        some_type: types.UnionType = type(int | str); typing.Union = typing.Union[int, str]

    """
    return typing.get_origin(some_type) in (types.UnionType, typing.Union)


def reversed_mro(cls: type) -> typing.Iterable[type]:
    """
    Get the Method Resolution Order (mro) for a class, in reverse order to be used with ChainMap.
    """
    return reversed(getattr(cls, "__mro__", []))


def _all_annotations(cls: type) -> ChainMap[str, type]:
    """
    Returns a dictionary-like ChainMap that includes annotations for all \
    attributes defined in cls or inherited from superclasses.
    """
    # chainmap reverses the iterable, so reverse again beforehand to keep order normally:

    return ChainMap(*(c.__annotations__ for c in reversed_mro(cls) if "__annotations__" in c.__dict__))


def all_dict(cls: type) -> AnyDict:
    """
    Get the internal data of a class and all it's parents.
    """
    return dict(ChainMap(*(c.__dict__ for c in reversed_mro(cls))))  # type: ignore


def all_annotations(cls: type, _except: typing.Optional[typing.Iterable[str]] = None) -> dict[str, type]:
    """
    Wrapper around `_all_annotations` that filters away any keys in _except.

    It also flattens the ChainMap to a regular dict.
    """
    if _except is None:
        _except = set()

    _all = _all_annotations(cls)
    return {k: v for k, v in _all.items() if k not in _except}


def instanciate(cls: typing.Type[T] | T, with_args: bool = False) -> T:
    """
    Create an instance of T (if it is a class).

    If it already is an instance, return it.
    If it is a generic (list[int)) create an instance  of the 'origin' (-> list()).

    If with_args: spread the generic args into the class creation
    (needed for e.g. TypedField(str), but not for list[str])
    """
    if inner_cls := typing.get_origin(cls):
        if not with_args:
            return typing.cast(T, inner_cls())

        args = typing.get_args(cls)
        return typing.cast(T, inner_cls(*args))

    if isinstance(cls, type):
        return typing.cast(T, cls())

    return cls


def origin_is_subclass(obj: Any, _type: type) -> bool:
    """
    Check if the origin of a generic is a subclass of _type.

    Example:
        origin_is_subclass(list[str], list) -> True
    """
    return bool(
        typing.get_origin(obj)
        and isinstance(typing.get_origin(obj), type)
        and issubclass(typing.get_origin(obj), _type)
    )


def mktable(
    data: dict[Any, Any], header: typing.Optional[typing.Iterable[str] | range] = None, skip_first: bool = True
) -> str:
    """
    Display a table for 'data'.

    See Also:
         https://stackoverflow.com/questions/70937491/python-flexible-way-to-format-string-output-into-a-table-without-using-a-non-st
    """
    # get max col width
    col_widths: list[int] = list(map(max, zip(*(map(lambda x: len(str(x)), (k, *v)) for k, v in data.items()))))

    # default numeric header if missing
    if not header:
        header = range(1, len(col_widths) + 1)

    header_widths = map(lambda x: len(str(x)), header)

    # correct column width if headers are longer
    col_widths = [max(c, h) for c, h in zip(col_widths, header_widths)]

    # create separator line
    line = f"+{'+'.join('-' * (w + 2) for w in col_widths)}+"

    # create formating string
    fmt_str = "| %s |" % " | ".join(f"{{:<{i}}}" for i in col_widths)

    output = io.StringIO()
    # header
    print()
    print(line, file=output)
    print(fmt_str.format(*header), file=output)
    print(line, file=output)

    # data
    for k, v in data.items():
        values = list(v.values())[1:] if skip_first else v.values()
        print(fmt_str.format(k, *values), file=output)

    # footer
    print(line, file=output)

    return output.getvalue()


K = typing.TypeVar("K")
V = typing.TypeVar("V")


def looks_like(v: Any, _type: type[Any]) -> bool:
    """
    Returns true if v or v's class is of type _type, including if it is a generic.

    Examples:
        assert looks_like([], list)
        assert looks_like(list, list)
        assert looks_like(list[str], list)
    """
    return isinstance(v, _type) or (isinstance(v, type) and issubclass(v, _type)) or origin_is_subclass(v, _type)


def filter_out(mut_dict: dict[K, V], _type: type[T]) -> dict[K, type[T]]:
    """
    Split a dictionary into things matching _type and the rest.

    Modifies mut_dict and returns everything of type _type.
    """
    return {k: mut_dict.pop(k) for k, v in list(mut_dict.items()) if looks_like(v, _type)}


def unwrap_type(_type: type) -> type:
    """
    Get the inner type of a generic.

    Example:
        list[list[str]] -> str
    """
    while args := typing.get_args(_type):
        _type = args[0]
    return _type


@typing.overload
def extract_type_optional(annotation: T) -> tuple[T, bool]:
    """
    T -> T is not exactly right because you'll get the inner type, but mypy seems happy with this.
    """


@typing.overload
def extract_type_optional(annotation: None) -> tuple[None, bool]:
    """
    None leads to None, False.
    """


def extract_type_optional(annotation: T | None) -> tuple[T | None, bool]:
    """
    Given an annotation, extract the actual type and whether it is optional.
    """
    if annotation is None:
        return None, False

    if origin := typing.get_origin(annotation):
        args = typing.get_args(annotation)

        if origin in (typing.Union, types.UnionType, typing.Optional) and args:
            # remove None:
            return next(_ for _ in args if _ and _ != types.NoneType and not isinstance(_, types.NoneType)), True

    return annotation, False


def to_snake(camel: str) -> str:
    """
    Convert CamelCase to snake_case.

    See Also:
        https://stackoverflow.com/a/44969381
    """
    return "".join([f"_{c.lower()}" if c.isupper() else c for c in camel]).lstrip("_")


class DummyQuery:
    """
    Placeholder to &= and |= actual query parts.
    """

    def __or__(self, other: T) -> T:
        """
        For 'or': DummyQuery | Other == Other.
        """
        return other

    def __and__(self, other: T) -> T:
        """
        For 'and': DummyQuery & Other == Other.
        """
        return other

    def __bool__(self) -> bool:
        """
        A dummy query is falsey, since it can't actually be used!
        """
        return False


def as_lambda(value: T) -> typing.Callable[..., T]:
    """
    Wrap value in a callable.
    """
    return lambda *_, **__: value


def match_strings(patterns: list[str] | str, string_list: list[str]) -> list[str]:
    """
    Glob but on a list of strings.
    """
    if isinstance(patterns, str):
        patterns = [patterns]

    matches = []
    for pattern in patterns:
        matches.extend([s for s in string_list if fnmatch.fnmatch(s, pattern)])

    return matches


def utcnow() -> dt.datetime:
    """
    Replacement of datetime.utcnow.
    """
    # return dt.datetime.now(dt.UTC)
    return dt.datetime.now(dt.timezone.utc)


def get_db(table: "TypedTable | Table") -> "DAL":
    """
    Get the underlying DAL instance for a pydal or typedal table.
    """
    return typing.cast("DAL", table._db)


def get_table(table: "TypedTable | Table") -> "Table":
    """
    Get the underlying pydal table for a typedal table.
    """
    return typing.cast("Table", table._table)


def get_field(field: "TypedField[typing.Any] | Field") -> "Field":
    """
    Get the underlying pydal field from a typedal field.
    """
    return typing.cast(
        "Field",
        field,  # Table.field already is a Field, but cast to make sure the editor knows this too.
    )


class classproperty:
    """
    Combination of @classmethod and @property.
    """

    def __init__(self, fget: typing.Callable[..., typing.Any]) -> None:
        """
        Initialize the classproperty.

        Args:
            fget: A function that takes the class as an argument and returns a value.
        """
        self.fget = fget

    def __get__(self, obj: typing.Any, owner: typing.Type[T]) -> typing.Any:
        """
        Retrieve the property value.

        Args:
            obj: The instance of the class (unused).
            owner: The class that owns the property.

        Returns:
            The value returned by the function.
        """
        return self.fget(owner)
