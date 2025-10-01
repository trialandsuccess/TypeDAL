"""
Constants values.
"""

import datetime as dt
import typing as t
from decimal import Decimal

from .types import T_annotation

JOIN_OPTIONS = t.Literal["left", "inner", None]
DEFAULT_JOIN_OPTION: JOIN_OPTIONS = "left"

BASIC_MAPPINGS: dict[T_annotation, str] = {
    str: "string",
    int: "integer",
    bool: "boolean",
    bytes: "blob",
    float: "double",
    object: "json",
    Decimal: "decimal(10,2)",
    dt.date: "date",
    dt.time: "time",
    dt.datetime: "datetime",
}
