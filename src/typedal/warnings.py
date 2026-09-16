"""Warning types emitted by TypeDAL."""


class UnusedWindowWarning(RuntimeWarning):
    """Warn when a window size is configured but the query is collected directly."""


class NoopQueryWarning(RuntimeWarning):
    """Warn when a query builder method is called without arguments, so it does nothing."""
