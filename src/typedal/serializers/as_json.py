"""
Replacement for pydal's json serializer.
"""

import datetime as dt
import json
from json import JSONEncoder
from typing import Any


class SerializedJson(JSONEncoder):
    """
    Custom encoder class with slightly improved defaults.
    """

    def default(self, o: Any) -> Any:
        """
        If no logic exists for a type yet, it is processed by this method.

        It supports sets (turned into list), __json__ methods and will just str() otherwise.
        """
        if isinstance(o, set):
            return list(o)
        elif isinstance(o, dt.date):
            return str(o)
        elif hasattr(o, "__json__"):
            if callable(o.__json__):
                return o.__json__()
            else:
                return o.__json__
        elif hasattr(o, "__dict__"):
            return o.__dict__
        else:
            # warnings.warn(f"Unkown type {type(o)}")
            return str(o)


def encode(something: Any, indent: int = None, **kw: Any) -> str:
    """
    Encode anything to JSON with some improved defaults.
    """
    return json.dumps(something, indent=indent, cls=SerializedJson, **kw)
