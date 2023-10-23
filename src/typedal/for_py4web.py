from typing import Any

import threadsafevariable
from py4web.core import ICECUBE, Fixture

from .core import TypeDAL


class DAL(TypeDAL, Fixture):  # pragma: no cover
    def on_request(self, _: dict[str, Any]) -> None:
        self.get_connection_from_pool_or_new()
        threadsafevariable.ThreadSafeVariable.restore(ICECUBE)

    def on_error(self, _: dict[str, Any]) -> None:
        self.recycle_connection_in_pool_or_close("rollback")

    def on_success(self, _: dict[str, Any]) -> None:
        self.recycle_connection_in_pool_or_close("commit")
