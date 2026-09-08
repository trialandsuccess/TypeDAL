"""Warning types emitted by TypeDAL."""


class UnusedWindowWarning(RuntimeWarning):
    """Warn when a window size is configured but the query is collected directly."""
