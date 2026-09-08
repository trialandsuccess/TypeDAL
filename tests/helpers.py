import warnings
from collections.abc import Iterator
from contextlib import contextmanager


@contextmanager
def expect_no_warning(warning_type: type[Warning] | None = None) -> Iterator[None]:
    with warnings.catch_warnings():
        warnings.simplefilter("error", warning_type or Warning)
        yield
