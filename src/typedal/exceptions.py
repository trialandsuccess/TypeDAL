"""Exceptions raised by TypeDAL operations."""


class UpsertKeyError(ValueError):
    """The upsert key is invalid or has no usable unique constraint."""


class UpsertAmbiguityError(UpsertKeyError):
    """The Python upsert path found multiple rows matching the key."""


class UpsertHookError(RuntimeError):
    """A before-hook registration explicitly forbids upsert."""
