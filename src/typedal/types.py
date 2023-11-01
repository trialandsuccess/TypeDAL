"""
Stuff to make mypy happy.
"""
from typing import Any, Optional, TypedDict

from pydal.objects import Expression as _Expression
from pydal.objects import Field as _Field
from pydal.objects import Query as _Query


class Query(_Query):  # type: ignore
    """
    Pydal Query object.

    Makes mypy happy.
    """


class Expression(_Expression):  # type: ignore
    """
    Pydal Expression object.

    Make mypy happy.
    """


class Field(_Field):
    """
    Pydal Field object.

    Make mypy happy.
    """


class _Types:
    """
    Internal type storage for stuff that mypy otherwise won't understand.
    """

    NONETYPE = type(None)


class Pagination(TypedDict):
    """
    Pagination key of a paginate dict has these items.
    """

    total_items: int
    current_page: int
    per_page: int
    total_pages: int
    has_next_page: bool
    has_prev_page: bool
    next_page: Optional[int]
    prev_page: Optional[int]


class PaginateDict(TypedDict):
    """
    Result of PaginatedRows.as_dict().
    """

    data: dict[int, dict[str, Any]]
    pagination: Pagination
