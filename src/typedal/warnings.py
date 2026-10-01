"""Warning types emitted by TypeDAL."""


class UnusedWindowWarning(RuntimeWarning):
    """Warn when a window size is configured but the query is collected directly."""


class NoopQueryWarning(RuntimeWarning):
    """Warn when a query builder method is called without arguments, so it does nothing."""


class UpsertFallbackWarning(RuntimeWarning):
    """Warn when upsert uses a non-atomic lookup followed by an update or insert."""


class UpsertHooksWarning(RuntimeWarning):
    """Warn that native upsert does not yet execute insert/update hooks."""
