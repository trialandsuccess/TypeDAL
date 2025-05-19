"""
Replacement for pydal's json serializer.
"""

import json
import typing
from typing import Any

from configurablejson import ConfigurableJsonEncoder, JSONRule


class SerializedJson(ConfigurableJsonEncoder):
    """
    Custom encoder class with slightly improved defaults.
    """

    def _default(self, o: Any) -> Any:  # pragma: no cover
        if hasattr(o, "as_dict"):
            return o.as_dict()
        elif hasattr(o, "asdict"):
            return o.asdict()
        elif hasattr(o, "_asdict"):
            return o._asdict()
        elif hasattr(o, "_as_dict"):
            return o._as_dict()
        elif hasattr(o, "to_dict"):
            return o.to_dict()
        elif hasattr(o, "todict"):
            return o.todict()
        elif hasattr(o, "_todict"):
            return o._todict()
        elif hasattr(o, "_to_dict"):
            return o._to_dict()
        elif hasattr(o, "__json__"):
            if callable(o.__json__):
                return o.__json__()
            else:
                return o.__json__
        elif hasattr(o, "__dict__"):
            return o.__dict__

        return str(o)

    @typing.overload
    def rules(self, o: Any, with_default: typing.Literal[False]) -> JSONRule | None:
        """
        If you pass with_default=False, you could get a None result.
        """

    @typing.overload
    def rules(self, o: Any, with_default: typing.Literal[True] = True) -> JSONRule:
        """
        If you don't pass with_default=False, you will always get a JSONRule result.
        """

    def rules(self, o: Any, with_default: bool = True) -> JSONRule | None:
        """
        Custom rules, such as set to list and as_dict/__json__ etc. lookups.
        """
        _type = type(o)

        _rules: dict[type[Any], JSONRule] = {
            # convert set to list
            set: JSONRule(preprocess=lambda o: list(o)),
        }

        # other rules:
        return _rules.get(_type, JSONRule(transform=self._default) if with_default else None)


def encode(something: Any, indent: typing.Optional[int] = None, **kw: Any) -> str:
    """
    Encode anything to JSON with some improved defaults.
    """
    return json.dumps(something, indent=indent, cls=SerializedJson, **kw)
