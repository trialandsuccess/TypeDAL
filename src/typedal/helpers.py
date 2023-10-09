"""
Helpers that work independently of core.
"""

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
