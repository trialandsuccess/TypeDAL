"""Exceptions raised by TypeDAL operations."""


class TypeDALError(Exception):
    """Base class for all TypeDAL exceptions."""


class TypeDALQueryError(TypeDALError, ValueError):
    """A query cannot be compiled safely."""


class ImplicitCrossJoinError(TypeDALQueryError):
    """A query includes an unrelated table without an explicit cross join."""


class AliasedTableMismatchError(ImplicitCrossJoinError):
    """A filter refers to an unaliased copy of an already joined table."""


class UpsertKeyError(TypeDALError, ValueError):
    """The upsert key is invalid or has no usable unique constraint."""


class UpsertAmbiguityError(UpsertKeyError):
    """The Python upsert path found multiple rows matching the key."""


class UpsertHookError(TypeDALError, RuntimeError):
    """A before-hook registration explicitly forbids upsert."""
