from __future__ import annotations

import enum
import typing as t
from dataclasses import dataclass


@dataclass(frozen=True)
class InvalidEnumValue:
    enum_type: type[enum.Enum]
    raw: t.Any
    value: None = None


def enum_value_type(enum_type: type[enum.Enum]) -> type[t.Any]:
    values = [member.value for member in enum_type]
    if not values:  # pragma: no cover
        raise TypeError(f"Enum {enum_type.__name__} has no members.")

    first_type = type(values[0])
    if any(type(value) is not first_type for value in values):
        raise TypeError(
            f"Enum {enum_type.__name__} has mixed value types; all values must share one type for DB fields.",
        )
    return first_type


def parse_enum_value(enum_type: type[enum.Enum], raw: t.Any) -> enum.Enum | InvalidEnumValue | None:
    if raw is None:  # pragma: no cover
        return None

    if isinstance(raw, enum_type):  # pragma: no cover
        return raw

    value_map = {str(member.value): member for member in enum_type}
    return value_map.get(str(raw), InvalidEnumValue(enum_type=enum_type, raw=raw))


def make_enum_filter_out(enum_type: type[enum.Enum]) -> t.Callable[[t.Any], enum.Enum | InvalidEnumValue | None]:
    def _filter_out(raw: t.Any) -> enum.Enum | InvalidEnumValue | None:
        return parse_enum_value(enum_type, raw)

    return _filter_out
