"""
Replacement for pydal's json serializer.
"""

import json
import typing as t

from configurablejson import ConfigurableJsonEncoder, JSONRule


class SerializedJson(ConfigurableJsonEncoder):
    """
    Custom encoder class with slightly improved defaults.
    """

    def _default(self, o: t.Any) -> t.Any:  # pragma: no cover
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

    @t.overload
    def rules(self, o: t.Any, with_default: t.Literal[False]) -> JSONRule | None:
        """
        If you pass with_default=False, you could get a None result.
        """

    @t.overload
    def rules(self, o: t.Any, with_default: t.Literal[True] = True) -> JSONRule:
        """
        If you don't pass with_default=False, you will always get a JSONRule result.
        """

    def rules(self, o: t.Any, with_default: bool = True) -> JSONRule | None:
        """
        Custom rules, such as set to list and as_dict/__json__ etc. lookups.
        """
        _type = type(o)

        _rules: dict[type[t.Any], JSONRule] = {
            # convert set to list
            set: JSONRule(preprocess=lambda o: list(o)),
        }

        # other rules:
        return _rules.get(_type, JSONRule(transform=self._default) if with_default else None)


def encode(something: t.Any, indent: t.Optional[int] = None, **kw: t.Any) -> str:
    """
    Encode anything to JSON with some improved defaults.
    """
    return json.dumps(something, indent=indent, cls=SerializedJson, **kw)
