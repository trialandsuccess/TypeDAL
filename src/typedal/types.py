"""
Stuff to make mypy happy.
"""
from datetime import datetime
from typing import Any, Optional, TypedDict

from pydal.objects import Expression as _Expression
from pydal.objects import Field as _Field
from pydal.objects import Query as _Query
from typing_extensions import NotRequired


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


class CacheMetadata(TypedDict):
    """
    Used by query builder metadata in the 'cache' key.
    """

    enabled: bool
    depends_on: list[Any]
    key: NotRequired[str | None]
    status: NotRequired[str | None]
    expires_at: NotRequired[datetime | None]
    cached_at: NotRequired[datetime | None]


class PaginationMetadata(TypedDict):
    """
    Used by query builder metadata in the 'pagination' key.
    """

    limit: int
    current_page: int
    max_page: int
    rows: int
    min_max: tuple[int, int]


class Metadata(TypedDict):
    """
    Loosely structured metadata used by Query Builder.
    """

    cache: NotRequired[CacheMetadata]
    pagination: NotRequired[PaginationMetadata]

    query: NotRequired[Query | str | None]
    ids: NotRequired[str]

    final_query: NotRequired[Query | str | None]
    final_args: NotRequired[list[Any]]
    final_kwargs: NotRequired[dict[str, Any]]
    relationships: NotRequired[set[str]]

    sql: NotRequired[str]
