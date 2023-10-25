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

    relations = relationship(list["TestRelationship"], condition=lambda self, other: self.id == other.querytable)


@db.define()
class TestRelationship(TypedTable):
    name: TypedField[str]
    value: TypedField[int]

    querytable: TestQueryTable


class Undefined(TypedTable):
    value: int


def test_repr_unbound():
    assert "unbound table Undefined" in str(Undefined)


def test_query_type():
    assert isinstance(TestQueryTable.number > 3, Query)
    assert isinstance(TestQueryTable.number >= 3, Query)
    assert isinstance(TestQueryTable.number == 3, Query)
    assert isinstance(TestQueryTable.number < 3, Query)
    assert isinstance(TestQueryTable.number <= 3, Query)
    assert isinstance(TestQueryTable.number != 3, Query)


def _setup_data():
    TestQueryTable.truncate()
    first = TestQueryTable.insert(number=0)
    second = TestQueryTable.insert(number=1)
    TestQueryTable.insert(number=2)
    TestQueryTable.insert(number=3)
    TestQueryTable.insert(number=4)

    TestRelationship.insert(name="First Relation", querytable=first, value=3)
    TestRelationship.insert(name="Second Relation", querytable=first, value=3)
    TestRelationship.insert(name="Third Relation", querytable=first, value=3)
    TestRelationship.insert(name="Fourth Relation", querytable=first, value=3)

    TestRelationship.insert(name="First Relation", querytable=second, value=33)
    TestRelationship.insert(name="Second Relation", querytable=second, value=33)
    TestRelationship.insert(name="Third Relation", querytable=second, value=33)
    TestRelationship.insert(name="Fourth Relation", querytable=second, value=33)


def test_where_builder():
    _setup_data()

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

    # test OR

    assert TestQueryTable.where(lambda row: row.id == 1, lambda row: row.id == 2).count() == 2
    assert TestQueryTable.where(lambda row: row.id == 1, lambda row: row.id == 99).count() == 1

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


def test_select():
    _setup_data()

    # all:
    full = TestQueryTable.where(lambda row: row.number > 0).join().select().first_or_fail()

    assert full.number
    assert full.other
    assert full.yet_another

    assert full.relations[0].name
    assert full.relations[0].value

    # specific fields:
    partial = (
        TestQueryTable.where(lambda row: row.number > 0)
        .join()
        .select(TestQueryTable.other, TestRelationship.value)
        .first_or_fail()
    )

    assert partial.other
    assert not partial.number
    assert not partial.yet_another

    assert not partial.relations[0].name
    assert partial.relations[0].value


def test_paginate():
    _setup_data()

    result = TestQueryTable.paginate(limit=1, page=1).join(method='left').collect()

    assert len(result) == 1
    assert len(result.first().relations) == 4

    meta = result.metadata["pagination"]

    assert meta["page"] == 1
    assert meta["limit"] == 1
    assert meta["min_max"] == (0, 1)

    result = TestQueryTable.paginate(limit=1, page=2).join(method='left').collect()

    assert len(result) == 1
    assert len(result.first().relations) == 4

    meta = result.metadata["pagination"]

    assert meta["page"] == 2
    assert meta["limit"] == 1
    assert meta["min_max"] == (1, 2)

    result = TestQueryTable.paginate(limit=1, page=3).join(method='left').collect()

    assert len(result) == 1
    assert len(result.first().relations) == 0

    meta = result.metadata["pagination"]

    assert meta["page"] == 3
    assert meta["limit"] == 1
    assert meta["min_max"] == (2, 3)
