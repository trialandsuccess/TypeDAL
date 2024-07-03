import pytest

from src.typedal import TypeDAL, TypedTable
from src.typedal.caching import calculate_stats, humanize_bytes, row_stats, table_stats


class SomeCachedTable(TypedTable):
    key: str
    value: int


@pytest.fixture
def database():
    db = TypeDAL("sqlite:memory")

    db.define(SomeCachedTable)

    SomeCachedTable.bulk_insert(
        [
            {"key": "first", "value": 1},
            {"key": "second", "value": 2},
            {"key": "third", "value": 3},
            {"key": "fourth", "value": 4},
        ]
    )

    yield db


def test_humanize_bytes():
    assert humanize_bytes(0) == humanize_bytes(None) == "0"

    assert "MB" in humanize_bytes(10000000)


def test_stats(database):
    assert len(SomeCachedTable.cache().collect()) == 4
    assert len(SomeCachedTable.cache().collect()) == 4

    generic = calculate_stats(database)
    assert generic["total"]["dependencies"] == generic["valid"]["dependencies"] == 4
    assert generic["expired"]["dependencies"] == 0

    table = table_stats(database, "some_cached_table")
    assert table["total"] == table["valid"]

    row = row_stats(database, "some_cached_table", "1")
    assert row["total"]["Dependent Cache Entries"] == row["valid"]["Dependent Cache Entries"] == 1
