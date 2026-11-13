"""
Instance-local extensions TypeDAL installs on each database's PyDAL adapter.

They patch only that adapter and its dialect, never PyDAL's global registrations.
"""

from decimal import Decimal, InvalidOperation

from pydal.adapters.base import BaseAdapter

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


def install_extensions(adapter: BaseAdapter) -> None:
    """Install the decimal guard and the upsert and affected-ID update extensions on `adapter`."""
    install_decimal_guard(adapter)
    install_upsert(adapter)
    install_update(adapter)
