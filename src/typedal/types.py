"""
Stuff to make mypy happy.
"""

import typing
from datetime import datetime
from typing import Any, Optional, TypedDict

from pydal.adapters.base import BaseAdapter
from pydal.helpers.classes import OpRow as _OpRow
from pydal.helpers.classes import Reference as _Reference
from pydal.objects import Expression as _Expression
from pydal.objects import Field as _Field
from pydal.objects import Query as _Query
from pydal.objects import Rows as _Rows
from pydal.objects import Set as _Set
from pydal.objects import Table as _Table
from pydal.validators import Validator as _Validator
from typing_extensions import NotRequired

if typing.TYPE_CHECKING:
    from .core import TypedField

AnyDict: typing.TypeAlias = dict[str, Any]


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


class Set(_Set):  # type: ignore
    """
    Pydal Set object.

    Make mypy happy.
    """


class OpRow(_OpRow):  # type: ignore
    """
    Pydal OpRow object.

    Make mypy happy.
    """


class Reference(_Reference):  # type: ignore
    """
    Pydal Reference object.

    Make mypy happy.
    """


class Field(_Field):  # type: ignore
    """
    Pydal Field object.

    Make mypy happy.
    """


class Rows(_Rows):  # type: ignore
    """
    Pydal Rows object.

    Make mypy happy.
    """


class Validator(_Validator):  # type: ignore
    """
    Pydal Validator object.

    Make mypy happy.
    """


class _Types:
    """
    Internal type storage for stuff that mypy otherwise won't understand.
    """

    NONETYPE = type(None)


BeforeInsertCallable: typing.TypeAlias = typing.Callable[[OpRow], Any]
AfterInsertCallable: typing.TypeAlias = typing.Callable[[OpRow, Reference], Any]
BeforeUpdateCallable: typing.TypeAlias = typing.Callable[[Set, OpRow], Any]
AfterUpdateCallable: typing.TypeAlias = typing.Callable[[Set, OpRow], Any]
BeforeDeleteCallable: typing.TypeAlias = typing.Callable[[Set], Any]
AfterDeleteCallable: typing.TypeAlias = typing.Callable[[Set], Any]


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

    data: dict[int, AnyDict]
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


class TableProtocol(typing.Protocol):  # pragma: no cover
    """
    Make mypy happy.
    """

    id: "TypedField[int]"

    def __getitem__(self, item: str) -> Field:
        """
        Tell mypy a Table supports dictionary notation for columns.
        """


class Table(_Table, TableProtocol):  # type: ignore
    """
    Make mypy happy.
    """


class CacheFn(typing.Protocol):
    """
    The cache model (e.g. cache.ram) accepts these parameters (all filled by dfeault).
    """

    def __call__(
        self: BaseAdapter,
        sql: str = "",
        fields: typing.Iterable[str] = (),
        attributes: typing.Iterable[str] = (),
        colnames: typing.Iterable[str] = (),
    ) -> Rows:
        """
        Only used for type-hinting.
        """


# CacheFn = typing.Callable[[], Rows]
CacheModel = typing.Callable[[str, CacheFn, int], Rows]
CacheTuple = tuple[CacheModel, int]


class SelectKwargs(typing.TypedDict, total=False):
    """
    Possible keyword arguments for .select().
    """

    join: Optional[list[Expression]]
    left: Optional[list[Expression]]
    orderby: Optional[Expression | str | Table]
    limitby: Optional[tuple[int, int]]
    distinct: bool | Field | Expression
    orderby_on_limitby: bool
    cacheable: bool
    cache: CacheTuple


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
    final_kwargs: NotRequired[SelectKwargs]
    relationships: NotRequired[set[str]]

    sql: NotRequired[str]
