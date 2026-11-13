"""
Instance-local extensions TypeDAL installs on each database's PyDAL adapter.

They patch only that adapter and its dialect, never PyDAL's global registrations.
"""

import typing as t
from decimal import Decimal, InvalidOperation

from .pydal_compat import BaseAdapter
from .updates import install_update
from .upsert import install_upsert


def _coerce_decimal(value: object) -> object:
    # PyDAL's own pre-representer turns "" into NULL for non-string types (an empty optional form field):
    if value is None or value == "":
        return value
    try:
        number = value if isinstance(value, Decimal) else Decimal(str(value).strip())
    except InvalidOperation:
        raise ValueError(f"Invalid decimal value: {str(value)[:32]!r}") from None
    if not number.is_finite():
        raise ValueError(f"Invalid decimal value: {str(value)[:32]!r}")
    return number


def install_decimal_guard(adapter: BaseAdapter) -> None:
    """
    Coerce values for decimal fields before PyDAL renders them into SQL.

    PyDAL's `decimal` representer inlines `str(value)` unquoted, so a string value for a decimal field (from request
    data, for example) ends up in the statement verbatim. Integer and double fields don't have this problem because
    their representers go through int() and float(). Patched on this adapter's representer only.
    """
    representer = getattr(adapter, "representer", None)
    if representer is None or getattr(representer, "_typedal_decimal_guard", False):
        return
    represent = representer.represent

    def guarded(value: object, field_type: object) -> object:
        if isinstance(field_type, str) and field_type.startswith("decimal"):
            value = _coerce_decimal(value)
        return represent(value, field_type)

    representer.represent = guarded
    representer._typedal_decimal_guard = True
    _install_bind_guard(adapter)


# What PyDAL's representers did with numeric values before it started binding them as parameters:
_NUMERIC_CONVERSIONS: dict[str, t.Callable[[t.Any], object]] = {
    "integer": int,
    "bigint": int,
    "double": float,
    "float": float,
}


def _install_bind_guard(adapter: BaseAdapter) -> None:
    """
    Validate numeric values PyDAL binds as parameters, like its representers did when it still inlined them.

    Since PyDAL 20261107.0, the SQLite and PostgreSQL compilers bind values instead of inlining them, and pass integer,
    float and decimal values through unconverted. That closes the decimal injection, but SQLite then stores any string
    in a numeric column (`insert(age="abc")` used to raise and now succeeds) and fails when reading it back.
    """
    compiler = getattr(adapter, "compiler", None)
    # Older PyDAL has no compiler, so this block only runs (and is only measured) on newer versions; CI tests both.
    if compiler is not None:  # pragma: no cover
        adapt_for_bind = compiler._adapt_for_bind

        def guarded(value: object, field_type: str) -> object:
            convert = _NUMERIC_CONVERSIONS.get(field_type)
            if convert is None and not field_type.startswith("decimal"):
                return adapt_for_bind(value, field_type)
            # an empty form field means NULL, as PyDAL's representers make it:
            if value == "":
                return None
            # bind a validated decimal as text, like the representer inlines it (sqlite3 can't bind a Decimal):
            return convert(value) if convert else str(_coerce_decimal(value))

        compiler._adapt_for_bind = guarded


def install_extensions(adapter: BaseAdapter) -> None:
    """Install the decimal guard and the upsert and affected-ID update extensions on `adapter`."""
    install_decimal_guard(adapter)
    install_upsert(adapter)
    install_update(adapter)
