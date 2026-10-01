"""
TypeDAL Library.
"""

from .asynchronous import AsyncSession, BlockingAccessHandler, BlockingDatabaseAccessWarning
from .core import TypeDAL
from .exceptions import UpsertAmbiguityError, UpsertHookError, UpsertKeyError
from .fields import TypedField
from .helpers import sql_expression
from .query_builder import QueryBuilder
from .relationships import Ref, Relationship, relationship
from .rows import PaginatedRows, TypedRows
from .tables import TypedTable
from .types import UpsertHookPolicy, UpsertKey, UpsertKeyValue
from .updates import AffectedSet
from .warnings import UpsertHooksWarning

from . import fields  # isort: skip

try:
    from .for_py4web import DAL as P4W_DAL
except ImportError:  # pragma: no cover
    P4W_DAL = None

__all__ = [
    "AffectedSet",
    "AsyncSession",
    "BlockingAccessHandler",
    "BlockingDatabaseAccessWarning",
    "PaginatedRows",
    "QueryBuilder",
    "Ref",
    "Relationship",
    "TypeDAL",
    "TypedField",
    "TypedRows",
    "TypedTable",
    "UpsertAmbiguityError",
    "UpsertHookError",
    "UpsertHookPolicy",
    "UpsertHooksWarning",
    "UpsertKey",
    "UpsertKeyError",
    "UpsertKeyValue",
    "fields",
    "relationship",
    "sql_expression",
]
