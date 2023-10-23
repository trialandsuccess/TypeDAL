import typing
from typing import Optional

import pytest
from pydal.objects import Query

from src.typedal import Relationship, TypeDAL, TypedField, TypedTable, relationship

db = TypeDAL("sqlite:memory")


@db.define()
class TestQueryTable(TypedTable):
    number: TypedField[int]
    other = TypedField(str, default="Something")
    yet_another = TypedField(list[str], default=["something", "and", "other", "things"])

    relations = relationship(
        list["TestRelationship"], condition=lambda self, other: self.id == other.querytable
    )


@db.define()
class TestRelationship(TypedTable):
    name: str

    querytable: TestQueryTable


class Undefined(TypedTable):
    value: int


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

    # delete all numbers above 2
    result = TestQueryTable.where(lambda row: row.number > 2).delete()
    assert len(result) == 2  # -> 3 and 4

    assert TestQueryTable.where(lambda row: row.number > 99).delete() is None  # nothing deleted

    assert TestQueryTable.count() == 3  # 0 - 2

    result = TestQueryTable.where(lambda row: row.number == 0).update(number=5)
    assert TestQueryTable.where(lambda row: row.number == -1).update(number=5) is None  # nothing updated
    assert result == [1]  # id 1 updated

    assert TestQueryTable(1).number == 5

    success = TestQueryTable.select().collect_or_fail()
    assert len(success) == 3

    assert TestQueryTable.where(id=-1).first() is None
    with pytest.raises(ValueError):
        TestQueryTable.where(id=-1).collect_or_fail()

    with pytest.raises(ValueError):
        assert not TestQueryTable.where(id=-1).first_or_fail()

    # try to break stuff:

    with pytest.raises(ValueError):
        # illegal query:
        builder.where(Exception())

    with pytest.raises(EnvironmentError):
        # can't collect before defining on db!
        Undefined.collect()


def test_paginate():
    TestQueryTable.truncate()
    first = TestQueryTable.insert(number=0)
    TestQueryTable.insert(number=1)
    TestQueryTable.insert(number=2)
    TestQueryTable.insert(number=3)
    TestQueryTable.insert(number=4)

    TestRelationship.insert(name="First Relation", querytable=first)
    TestRelationship.insert(name="Second Relation", querytable=first)
    TestRelationship.insert(name="Third Relation", querytable=first)
    TestRelationship.insert(name="Fourth Relation", querytable=first)

    result = TestQueryTable.paginate(limit=1).join().collect()

    assert len(result) == 1
    assert len(result.first().relations) == 4
