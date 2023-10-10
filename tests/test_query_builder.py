import pytest
from pydal.objects import Query

from src.typedal import TypeDAL, TypedField, TypedTable

db = TypeDAL("sqlite:memory")


@db.define()
class TestQueryTable(TypedTable):
    number: TypedField[int]
    other = TypedField(str, default="Something")
    yet_another = TypedField(list[str], default=["something", "and", "other", "things"])


def test_query_type():
    assert isinstance(TestQueryTable.number > 3, Query)
    assert isinstance(TestQueryTable.number >= 3, Query)
    assert isinstance(TestQueryTable.number == 3, Query)
    assert isinstance(TestQueryTable.number < 3, Query)
    assert isinstance(TestQueryTable.number <= 3, Query)
    assert isinstance(TestQueryTable.number != 3, Query)


def test_where_builder():
    TestQueryTable.insert(number=0)
    TestQueryTable.insert(number=1)
    TestQueryTable.insert(number=2)
    TestQueryTable.insert(number=3)
    TestQueryTable.insert(number=4)

    assert TestQueryTable.first().id == TestQueryTable.select().first().id

    builder = TestQueryTable.where(lambda row: row.number < 3).where(TestQueryTable.number > 1)
    results = builder.collect()
    assert len(results) == 1

    instance = results.first()

    assert instance.number == 2
    assert instance.other == "Something"

    assert isinstance(instance, TestQueryTable)

    results = builder.select(TestQueryTable.id, TestQueryTable.number).first()

    assert "number" in TestQueryTable
    assert "number" in results.__dict__
    assert "number" in results
    assert results.number == results["number"] == 2

    assert "other" in TestQueryTable
    assert "other" not in results.__dict__
    assert "other" not in results
    assert not results.other

    with pytest.raises(KeyError):
        assert not results["other"]

    assert not results.yet_another
