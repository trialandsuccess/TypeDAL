"""
Helpers that work independently of core.
"""
import io
import types
import typing
from collections import ChainMap
from typing import Any

T = typing.TypeVar("T")


def is_union(some_type: type | types.UnionType) -> bool:
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


def all_dict(cls: type) -> dict[str, Any]:
    return dict(ChainMap(*(c.__dict__ for c in getattr(cls, "__mro__", []))))


def all_annotations(cls: type, _except: typing.Iterable[str] = None) -> dict[str, type]:
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


# https://stackoverflow.com/questions/70937491/python-flexible-way-to-format-string-output-into-a-table-without-using-a-non-st
def mktable(
    data: dict[Any, Any], header: typing.Optional[typing.Iterable[str] | range] = None, skip_first: bool = True
) -> str:
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
    return isinstance(v, _type) or (isinstance(v, type) and issubclass(v, _type)) or origin_is_subclass(v, _type)


def filter_out(mut_dict: dict[K, V], _type: type[T]) -> dict[K, type[T]]:
    return {k: mut_dict.pop(k) for k, v in list(mut_dict.items()) if looks_like(v, _type)}


def unwrap_type(_type: type) -> type:
    while args := typing.get_args(_type):
        _type = args[0]
    return _type
