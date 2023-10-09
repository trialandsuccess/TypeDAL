import types
import typing
from collections import ChainMap
from typing import Any

T = typing.TypeVar("T")


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


def instanciate(cls: typing.Type[T] | T) -> T:
    if inner_cls := typing.get_origin(cls):
        args = typing.get_args(cls)
        return typing.cast(T, inner_cls(*args))

    if isinstance(cls, type):
        return typing.cast(T, cls())

    return cls


def origin_is_subclass(obj: Any, _type: type) -> bool:
    return bool(
        typing.get_origin(obj)
        and isinstance(typing.get_origin(obj), type)
        and issubclass(typing.get_origin(obj), _type)
    )
