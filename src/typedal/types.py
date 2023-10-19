from pydal.objects import Expression as _Expression
from pydal.objects import Query as _Query


class Query(_Query):  # type: ignore
    ...


class Expression(_Expression):  # type: ignore
    ...


class _Types:
    """
    Internal type storage for stuff that mypy otherwise won't understand.
    """

    NONETYPE = type(None)
