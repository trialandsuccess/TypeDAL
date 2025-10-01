"""
Stuff to make mypy happy.
"""

# ---------------------------------------------------------------------------
# Imports
# ---------------------------------------------------------------------------

# Standard library
import datetime as dt
import types
import typing as t

import pydal.objects

# Third-party
from pydal.adapters.base import BaseAdapter
from pydal.helpers.classes import OpRow as _OpRow
from pydal.helpers.classes import Reference as _Reference
from pydal.helpers.classes import SQLCustomType
from pydal.objects import Expression as _Expression
from pydal.objects import Field as _Field
from pydal.objects import Query as _Query
from pydal.objects import Row as _Row
from pydal.objects import Rows as _Rows
from pydal.objects import Set as _Set
from pydal.objects import Table as _Table
from pydal.validators import Validator as _Validator

try:
    from string.templatelib import Template
except ImportError:
    Template: t.TypeAlias = str  # type: ignore

# Internal references
if t.TYPE_CHECKING:
    from .fields import TypedField
    from .tables import TypedTable

# ---------------------------------------------------------------------------
# Aliases
# ---------------------------------------------------------------------------

AnyCallable: t.TypeAlias = t.Callable[..., t.Any]
AnyDict: t.TypeAlias = dict[str, t.Any]


# ---------------------------------------------------------------------------
# Protocols
# ---------------------------------------------------------------------------


class TableProtocol(t.Protocol):  # pragma: no cover
    """Protocol to make mypy happy for Tables."""

    id: "TypedField[int]"

    def __getitem__(self, item: str) -> "Field":
        """
        Tables have table[field] syntax.
        """


class CacheFn(t.Protocol):
    """
    The cache model (e.g. cache.ram) accepts these parameters (all filled by default).
    """

    def __call__(
        self: BaseAdapter,
        sql: str = "",
        fields: t.Iterable[str] = (),
        attributes: t.Iterable[str] = (),
        colnames: t.Iterable[str] = (),
    ) -> "Rows":
        """Signature for calling this object."""


class FileSystemLike(t.Protocol):  # pragma: no cover
    """Protocol for any class that has an 'open' function (e.g. OSFS)."""

    def open(self, file: str, mode: str = "r") -> t.IO[t.Any]:
        """We assume every object with an open function this shape, is basically a file."""


# ---------------------------------------------------------------------------
# pydal Wrappers (to help mypy understand these classes)
# ---------------------------------------------------------------------------


class Query(_Query):  # type: ignore
    """Pydal Query object. Makes mypy happy."""


class Expression(_Expression):  # type: ignore
    """Pydal Expression object. Make mypy happy."""


class Set(_Set):  # type: ignore
    """Pydal Set object. Make mypy happy."""


if t.TYPE_CHECKING:

    class OpRow:
        """
        Pydal OpRow object for typing (otherwise mypy thinks it's Any).
        """

        def __getitem__(self, item: str) -> t.Any:
            """row.item syntax."""

        def __setitem__(self, key: str, value: t.Any) -> None:
            """row.item = key syntax."""

        # more methods could be added

else:

    class OpRow(_OpRow):  # type: ignore
        """Runtime OpRow, using pydal's version."""


class Reference(_Reference):  # type: ignore
    """Pydal Reference object. Make mypy happy."""


class Field(_Field):  # type: ignore
    """Pydal Field object. Make mypy happy."""


class Rows(_Rows):  # type: ignore
    """Pydal Rows object. Make mypy happy."""

    def column(self, column: t.Any = None) -> list[t.Any]:
        """
        Get a list of all values in a specific column.

        Example:
            rows.column('name') -> ['Name 1', 'Name 2', ...]
        """
        return [r[str(column) if column else self.colnames[0]] for r in self]


class Row(_Row):
    """Pydal Row object. Make mypy happy."""


class Validator(_Validator):  # type: ignore
    """Pydal Validator object. Make mypy happy."""


class Table(_Table, TableProtocol):  # type: ignore
    """Table with protocol support. Make mypy happy."""


# ---------------------------------------------------------------------------
# Utility Types
# ---------------------------------------------------------------------------


class _Types:
    """Internal type storage for stuff mypy otherwise won't understand."""

    NONETYPE = type(None)


# ---------------------------------------------------------------------------
# TypedDicts
# ---------------------------------------------------------------------------


class Pagination(t.TypedDict):
    """Pagination key of a paginate dict has these items."""

    total_items: int
    current_page: int
    per_page: int
    total_pages: int
    has_next_page: bool
    has_prev_page: bool
    next_page: t.Optional[int]
    prev_page: t.Optional[int]


class PaginateDict(t.TypedDict):
    """Result of PaginatedRows.as_dict()."""

    data: dict[int, AnyDict]
    pagination: Pagination


class CacheMetadata(t.TypedDict):
    """Used by query builder metadata in the 'cache' key."""

    enabled: bool
    depends_on: list[t.Any]
    key: t.NotRequired[str | None]
    status: t.NotRequired[str | None]
    expires_at: t.NotRequired[dt.datetime | None]
    cached_at: t.NotRequired[dt.datetime | None]


class PaginationMetadata(t.TypedDict):
    """Used by query builder metadata in the 'pagination' key."""

    limit: int
    current_page: int
    max_page: int
    rows: int
    min_max: tuple[int, int]


class SelectKwargs(t.TypedDict, total=False):
    """Possible keyword arguments for .select()."""

    join: t.Optional[list[Expression]]
    left: t.Optional[list[Expression]]
    orderby: "OrderBy | t.Iterable[OrderBy] | None"
    limitby: t.Optional[tuple[int, int]]
    distinct: bool | Field | Expression
    orderby_on_limitby: bool
    cacheable: bool
    cache: "CacheTuple"


class Metadata(t.TypedDict):
    """Loosely structured metadata used by Query Builder."""

    cache: t.NotRequired[CacheMetadata]
    pagination: t.NotRequired[PaginationMetadata]
    query: t.NotRequired[Query | str | None]
    ids: t.NotRequired[str]
    final_query: t.NotRequired[Query | str | None]
    final_args: t.NotRequired[list[t.Any]]
    final_kwargs: t.NotRequired[SelectKwargs]
    relationships: t.NotRequired[set[str]]
    sql: t.NotRequired[str]


class FieldSettings(t.TypedDict, total=False):
    """
    The supported keyword arguments for `pydal.Field()`.

    Other arguments can be passed.
    """

    type: str | type | SQLCustomType
    length: int
    default: t.Any
    required: bool
    requires: list[AnyCallable | t.Any | Validator] | Validator | AnyCallable
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
    options: list[t.Any] | AnyCallable
    update: t.Any
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
    custom_qualifier: t.Any
    map_none: t.Any
    rname: str


# ---------------------------------------------------------------------------
# Generics & Query Helpers
# ---------------------------------------------------------------------------

T = t.TypeVar("T", bound=t.Any)
P = t.ParamSpec("P")
R = t.TypeVar("R")

T_MetaInstance = t.TypeVar("T_MetaInstance", bound="TypedTable")
T_Query = t.Union[
    "Table",
    Query,
    bool,
    None,
    "TypedTable",
    t.Type["TypedTable"],
    Expression,
]

T_subclass = t.TypeVar("T_subclass", "TypedTable", Table)
T_Field: t.TypeAlias = t.Union["TypedField[t.Any]", "Table", t.Type["TypedTable"]]

# use typing.cast(type, ...) to make mypy happy with unions
T_Value = t.TypeVar("T_Value")  # actual type of the Field (via Generic)

# table-ish parameter:
P_Table = t.Union[t.Type["TypedTable"], pydal.objects.Table]

Condition: t.TypeAlias = t.Optional[t.Callable[[P_Table, P_Table], Query | bool]]

OnQuery: t.TypeAlias = t.Optional[t.Callable[[P_Table, P_Table], list[Expression]]]

CacheModel = t.Callable[[str, CacheFn, int], Rows]
CacheTuple = tuple[CacheModel, int]

OrderBy: t.TypeAlias = str | Expression

T_annotation = t.Type[t.Any] | types.UnionType
