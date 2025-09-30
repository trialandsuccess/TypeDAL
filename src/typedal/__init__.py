"""
TypeDAL Library.
"""

from .core import TypeDAL

from . import fields  # isort: skip
from .fields import TypedField
from .helpers import sql_expression
from .query_builder import QueryBuilder
from .relationships import Relationship, relationship
from .rows import TypedRows
from .tables import TypedTable

try:
    from .for_py4web import DAL as P4W_DAL
except ImportError:  # pragma: no cover
    P4W_DAL = None  # type: ignore

__all__ = [
    "Relationship",
    "TypeDAL",
    "TypedField",
    "TypedRows",
    "TypedTable",
    "QueryBuilder",
    "fields",
    "relationship",
    "sql_expression",
]
