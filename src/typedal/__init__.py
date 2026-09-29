"""
TypeDAL Library.
"""

from .asynchronous import AsyncSession, BlockingAccessHandler, BlockingDatabaseAccessWarning
from .core import TypeDAL
from .errors import AliasedTableMismatchError, ImplicitCrossJoinError, TypeDALQueryError
from .fields import TypedField
from .helpers import sql_expression
from .query_builder import QueryBuilder
from .relationships import Ref, Relationship, relationship
from .rows import PaginatedRows, TypedRows
from .tables import TypedTable

from . import fields  # isort: skip

try:
    from .for_py4web import DAL as P4W_DAL
except ImportError:  # pragma: no cover
    P4W_DAL = None

__all__ = [
    "AliasedTableMismatchError",
    "AsyncSession",
    "BlockingAccessHandler",
    "BlockingDatabaseAccessWarning",
    "ImplicitCrossJoinError",
    "PaginatedRows",
    "QueryBuilder",
    "Ref",
    "Relationship",
    "TypeDAL",
    "TypeDALQueryError",
    "TypedField",
    "TypedRows",
    "TypedTable",
    "fields",
    "relationship",
    "sql_expression",
]
