"""
TypeDAL Library.
"""

from . import fields
from .core import (
    Relationship,
    TypeDAL,
    TypedField,
    TypedRows,
    TypedTable,
    relationship,
)
from .helpers import sql_expression

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
    "fields",
    "relationship",
    "sql_expression",
]
