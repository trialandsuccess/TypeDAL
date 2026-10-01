"""Errors raised while building TypeDAL queries."""


class TypeDALQueryError(ValueError):
    """A query cannot be compiled safely."""


class ImplicitCrossJoinError(TypeDALQueryError):
    """A query includes an unrelated table without an explicit cross join."""


class AliasedTableMismatchError(ImplicitCrossJoinError):
    """A filter refers to an unaliased copy of an already joined table."""
