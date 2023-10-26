"""
Stuff to make mypy happy.
"""

from pydal.objects import Expression as _Expression
from pydal.objects import Query as _Query


class Query(_Query):  # type: ignore
    """
    Pydal Query object.

    Makes mypy happy.
    """


class Expression(_Expression):  # type: ignore
    """
    Pydal Expression object.

    Make mypy happy.
    """


class _Types:
    """
    Internal type storage for stuff that mypy otherwise won't understand.
    """

    NONETYPE = type(None)
