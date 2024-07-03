"""
For some reason, it's best to run these tasks last.
"""

import tempfile
from pathlib import Path

from src.typedal import TypeDAL, TypedTable


class DummyTable(TypedTable): ...


def test_autocreate_folder1():
    # must be run later because it sets a temp folder that is not available later?
    with tempfile.TemporaryDirectory() as folder:
        folder_path = Path(folder)
        database = TypeDAL("sqlite://storage.db", folder=folder)

        assert (folder_path / "storage.db").exists()

        database.define(DummyTable)

        assert any(folder_path.glob("*_dummy_table.table"))
