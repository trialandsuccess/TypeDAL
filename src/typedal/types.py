"""
Stuff to make mypy happy.
"""

import typing
from datetime import datetime
from typing import Any, Callable, Optional, TypedDict

from pydal.adapters.base import BaseAdapter
from pydal.helpers.classes import OpRow as _OpRow
from pydal.helpers.classes import Reference as _Reference
from pydal.helpers.classes import SQLCustomType
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


if typing.TYPE_CHECKING:

    class OpRow:
        """
        Pydal OpRow object for typing (otherwise mypy thinks it's Any).

        Make mypy happy.
        """

        def __getitem__(self, item: str) -> typing.Any:
            """
            Dict [] get notation.
            """

        def __setitem__(self, key: str, value: typing.Any) -> None:
            """
            Dict [] set notation.
            """

        # ... and more methods

else:

    class OpRow(_OpRow):  # type: ignore
        """
        Pydal OpRow object at runtime just uses pydal's version.

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

    def column(self, column: typing.Any = None) -> list[typing.Any]:
        """
        Get a list of all values in a specific column.

        Example:
                rows.column('name') -> ['Name 1', 'Name 2', ...]
        """
        return [r[str(column) if column else self.colnames[0]] for r in self]


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


class SelectKwargs(TypedDict, total=False):
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


class FileSystemLike(typing.Protocol):  # pragma: no cover
    """
    Protocol for any class that has an 'open' function.

    An example of this is OSFS from PyFilesystem2.
    """

    def open(self, file: str, mode: str = "r") -> typing.IO[typing.Any]:
        """
        Opens a file for reading, writing or other modes.
        """
        ...


AnyCallable: typing.TypeAlias = Callable[..., Any]


class FieldSettings(TypedDict, total=False):
    """
    The supported keyword arguments for `pydal.Field()`.

    Other arguments can be passed.
    """

    type: str | type | SQLCustomType
    length: int
    default: Any
    required: bool
    requires: list[AnyCallable | Any | Validator] | Validator | AnyCallable
    ondelete: str
    onupdate: str
    notnull: bool
    unique: bool
    uploadfield: bool | str
    widget: AnyCallable
    label: str
    comment: str
    writable: bool
    readable: bool
    searchable: bool
    listable: bool
    regex: str
    options: list[Any] | AnyCallable
    update: Any
    authorize: AnyCallable
    autodelete: bool
    represent: AnyCallable
    uploadfolder: str
    uploadseparate: bool
    uploadfs: FileSystemLike
    compute: AnyCallable
    custom_store: AnyCallable
    custom_retrieve: AnyCallable
    custom_retrieve_file_properties: AnyCallable
    custom_delete: AnyCallable
    filter_in: AnyCallable
    filter_out: AnyCallable
    custom_qualifier: Any
    map_none: Any
    rname: str
