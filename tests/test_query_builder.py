import inspect
import typing

import pytest
from pydal.objects import Query

from src.typedal import TypeDAL, TypedField, TypedTable, relationship
from typedal.types import CacheFn, CacheModel, CacheTuple, Rows

db = TypeDAL("sqlite:memory")


@db.define()
class TestQueryTable(TypedTable):
    number: TypedField[int]
    other = TypedField(str, default="Something")
    yet_another = TypedField(list[str], default=["something", "and", "other", "things"])

    relations = relationship(
        list["TestRelationship"], condition=lambda self, other: self.id == other.querytable, join="left"
    )


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


"""
SELECT "test_query_table"."id",
       "test_query_table"."number",
       "relations_8106139955393"."id",
       "relations_8106139955393"."name",
       "relations_8106139955393"."value",
       "relations_8106139955393"."querytable"
FROM "test_query_table"
         LEFT JOIN "test_relationship" AS "relations_8106139955393"
                   ON ("relations_8106139955393"."querytable" = "test_query_table"."id")
WHERE ("test_query_table"."id" IN (SELECT "test_query_table"."id"
                                   FROM "test_query_table"
                                   WHERE ("test_query_table"."id" > 0)
                                   ORDER BY "test_query_table"."id"
                                   LIMIT 3 OFFSET 0))
ORDER BY "test_query_table"."number" DESC;
"""


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

    db.commit()


def test_where_builder():
    _setup_data()

    assert TestQueryTable.first().id == TestQueryTable.select().first().id

    builder = TestQueryTable.where(lambda row: row.number < 3).where(TestQueryTable.number > 1)
    results = builder.collect()
    assert len(results) == 1

    sql = builder._first()
    assert "LIMIT 1" in sql

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
    builder = TestQueryTable.where(lambda row: row.number > 2)
    assert builder._delete()
    assert isinstance(builder._delete(), str)

    result = builder.delete()
    assert len(result) == 2  # -> 3 and 4

    assert TestQueryTable.where(lambda row: row.number > 99).delete() == []  # nothing deleted

    assert TestQueryTable.count() == 3 == len(TestQueryTable.collect())  # 0 - 2

    builder = TestQueryTable.where(lambda row: row.number == 0)
    sql = builder._update(number=5)
    assert sql
    assert isinstance(sql, str)
    assert "number" in sql
    assert "5" in sql

    result = builder.update(number=5)
    assert TestQueryTable.where(lambda row: row.number == -1).update(number=5) == []  # nothing updated
    assert result == [1]  # id 1 updated

    assert TestQueryTable(1).number == 5

    success = TestQueryTable.select().collect_or_fail()
    assert len(success) == 3

    # test OR

    assert TestQueryTable.where(lambda row: row.id == 1, lambda row: row.id == 2).count() == 2
    assert (
        TestQueryTable.where(
            {"id": 1},
            {  # OR
                "id": 2
            },
        ).count()
        == 2
    )

    assert TestQueryTable.where(lambda row: row.id == 1, lambda row: row.id == 99).count() == 1
    assert (
        TestQueryTable.where(
            {"id": 1},
            # OR
            {"id": 99},
        ).count()
        == 1
    )

    assert TestQueryTable.where(id=-1).first() is None
    with pytest.raises(ValueError):
        TestQueryTable.where(id=-1).collect_or_fail()

    with pytest.raises(TypeError):
        TestQueryTable.where(id=-1).collect_or_fail(TypeError("pytest"))

    with pytest.raises(ValueError):
        assert not TestQueryTable.where(id=-1).first_or_fail()

    with pytest.raises(TypeError):
        assert not TestQueryTable.where(id=-1).first_or_fail(TypeError("pytest"))

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

    other = partial.relations[0]

    assert isinstance(other, TestRelationship)

    assert not other.name
    assert other.value


def test_paginate():
    _setup_data()

    result = TestQueryTable.join(method="left").paginate(limit=1, page=1)

    result_two = result.next().previous()

    assert len(result) == 1 == len(result_two)
    assert len(result.first().relations) == 4 == len(result_two.first().relations)

    meta = result.metadata["pagination"]
    assert meta == result_two.metadata["pagination"]

    assert meta["current_page"] == 1
    assert meta["limit"] == 1
    assert meta["min_max"] == (0, 1)

    next_page = result.next()
    result = TestQueryTable.join(method="left").paginate(limit=1, page=2)

    assert len(result) == 1 == len(next_page)
    assert len(result.first().relations) == 4 == len(next_page.first().relations)

    meta = result.metadata["pagination"]
    assert meta == next_page.metadata["pagination"]

    assert meta["current_page"] == 2
    assert meta["limit"] == 1
    assert meta["min_max"] == (1, 2)

    next_page = result.next()

    sql = TestQueryTable.join(method="left")._paginate(limit=1, page=3)

    assert "LIMIT 1" in sql
    assert "OFFSET 2" in sql

    result = TestQueryTable.join(method="left").paginate(limit=1, page=3)

    assert len(result) == 1 == len(next_page) == len(result.data)
    assert len(result.first().relations) == 0 == len(next_page.first().relations)

    meta = result.metadata["pagination"]
    assert meta == next_page.metadata["pagination"]

    assert meta["current_page"] == 3
    assert meta["limit"] == 1
    assert meta["min_max"] == (2, 3)

    # final page (all items on 1 page is no prev or next):
    result = TestQueryTable.paginate(limit=10, page=1)

    with pytest.raises(StopIteration):
        result.previous()

    with pytest.raises(StopIteration):
        result.next()

    page_dict = result.as_dict()["pagination"]

    assert page_dict["has_next_page"] is False
    assert page_dict["has_prev_page"] is False
    assert page_dict["next_page"] is None
    assert page_dict["prev_page"] is None

    # what if no limit?
    all_rows = TestQueryTable.join(method="left").paginate(limit=0, page=1)
    assert len(all_rows) == 5
    assert all_rows.pagination["total_items"] == 5
    assert all_rows.pagination["total_pages"] == 1
    assert not all_rows.pagination["has_next_page"]


def test_chunking():
    _setup_data()

    total = 0
    size = 3
    for rows in TestQueryTable.chunk(size):
        assert rows
        assert len(rows) <= size
        total += len(rows)

    assert total == TestQueryTable.count()


def test_complex_join():
    _setup_data()

    # only rows that ahve a TestQueryTable with number == 1
    builder = TestRelationship.join(
        "test_query_table",
        method="inner",
        condition=(TestRelationship.querytable == TestQueryTable.id) & (TestQueryTable.number == 1),
    )

    assert builder.count() == 4 == len(builder.collect())

    assert builder.collect()

    # notation 2:

    builder = TestRelationship.join(
        TestQueryTable,
        method="inner",
        condition=lambda relation, query: (relation.querytable == query.id) & (query.number == 1),
    )

    assert builder.count() == 4 == len(builder.collect())

    count_sql = builder._count()
    assert "COUNT(" in count_sql

    sql = builder._collect()
    assert "JOIN" in sql
    assert "LEFT" not in sql
    assert "CROSS" not in sql

    rows = builder.collect()

    # value = 3 only ahppens for querytable.number == 0
    assert len(rows) == 4
    assert set(rows.column("value")) == {33}

    for row in rows:
        # note: other column name since it's a manual relationship:
        assert row.test_query_table.number == 1

    page = builder.paginate(limit=2)

    assert set(page.column("value")) == {33}
    assert len(page) == 2

    page = page.next()
    assert len(page) == 2
    assert set(page.column("value")) == {33}

    with pytest.raises(StopIteration):
        assert not page.next()

    # custom .on:

    builder = TestRelationship.join(
        TestQueryTable,
        on=lambda relation, query: query.on((relation.querytable == query.id) & (query.number == 1)),
    )

    sql = builder._collect()
    assert "JOIN" in sql
    assert "LEFT" in sql
    assert "INNER" not in sql
    assert "CROSS" not in sql

    assert builder.count() == 8 == len(builder.collect())  # all but with less extra's

    # notation 2:

    builder = TestRelationship.join(
        TestQueryTable,
        on=TestQueryTable.on((TestRelationship.querytable == TestQueryTable.id) & (TestQueryTable.number == 1)),
    )

    assert builder.count() == 8 == len(builder.collect())  # all but with less extra's

    for row in builder.collect():
        if row.test_query_table:
            assert row.test_query_table.number == 1

    # value errors:

    with pytest.raises(ValueError):
        TestRelationship.join(
            TestQueryTable,
            TestRelationship,
            condition=lambda relation, query: (relation.querytable == query.id) & (query.number == 1),
        )

    with pytest.raises(ValueError):
        TestRelationship.join(
            TestQueryTable,
            TestRelationship,
            method="inner",
            on=lambda relation, query: (relation.querytable == query.id) & (query.number == 1),
        )

    with pytest.raises(ValueError):
        TestRelationship.join(
            TestQueryTable,
            method="inner",
            condition=lambda relation, query: (relation.querytable == query.id) & (query.number == 1),
            on=lambda relation, query: (relation.querytable == query.id) & (query.number == 1),
        )


def test_reprs_and_bool():
    _setup_data()

    # NOTE: This logic changed: any 'empty' query table is False, and adding any conditions make it True.
    empty = TestQueryTable.where()
    notempty = TestQueryTable.where(id=1)
    assert (empty or notempty) is notempty
    assert (notempty or empty) is notempty
    assert not empty
    assert notempty
    assert TestQueryTable.where(id=10000000000)

    assert repr(TestQueryTable.where(id=1))
    assert str(TestQueryTable.where(id=1))


def test_orderby():
    _setup_data()

    base_qt = TestQueryTable.select(TestQueryTable.id, TestQueryTable.number)

    assert base_qt.count() == 5

    rows1 = base_qt.select(orderby=TestQueryTable.id).paginate(limit=3, page=1)
    rows2 = base_qt.select(orderby=TestQueryTable.id, limitby=(0, 3)).collect()
    assert [_.id for _ in rows1] == [_.id for _ in rows2] == [1, 2, 3]

    rows1 = base_qt.select(orderby=TestQueryTable.number).paginate(limit=3, page=1)
    rows2 = base_qt.select(orderby=TestQueryTable.number, limitby=(0, 3)).collect()
    assert [_.number for _ in rows1] == [_.number for _ in rows2] == [0, 1, 2]

    rows1 = base_qt.select(orderby=~TestQueryTable.number).paginate(limit=3, page=1)
    rows2 = base_qt.select(orderby=~TestQueryTable.number, limitby=(0, 3)).collect()
    assert [_.number for _ in rows1] == [_.number for _ in rows2] == [4, 3, 2]

    joined_qt = base_qt.join()

    assert joined_qt.count() == 5  # left join shouldn't change count

    rows1 = joined_qt.select(orderby=TestQueryTable.id).paginate(limit=3, page=1)
    rows2 = joined_qt.select(orderby=TestQueryTable.id, limitby=(0, 3)).collect()
    assert [_.id for _ in rows1] == [_.id for _ in rows2] == [1, 2, 3]

    rows1 = joined_qt.select(orderby=TestQueryTable.number).paginate(limit=3, page=1)
    rows2 = joined_qt.select(orderby=TestQueryTable.number, limitby=(0, 3)).collect()
    assert [_.number for _ in rows1] == [_.number for _ in rows2] == [0, 1, 2]

    rows1 = joined_qt.select(orderby=~TestQueryTable.number).paginate(limit=3, page=1)
    rows2 = joined_qt.select(orderby=~TestQueryTable.number, limitby=(0, 3)).collect()
    assert [_.number for _ in rows1] == [_.number for _ in rows2] == [4, 3, 2]

    rows1 = joined_qt.select(orderby=~TestQueryTable.number).paginate(limit=100, page=1)
    rows2 = joined_qt.select(orderby=~TestQueryTable.number, limitby=(0, 100)).collect()
    assert [_.number for _ in rows1] == [_.number for _ in rows2] == [4, 3, 2, 1, 0]


def test_execute():
    _setup_data()

    raw_execute = TestRelationship.select(
        TestRelationship.querytable.with_alias("query_table"),
        TestRelationship.querytable.count().with_alias("count"),
        groupby=TestRelationship.querytable,
    ).execute()  # ! no proper typing

    # 2 x 4:

    assert len(raw_execute) == 2

    for row in raw_execute:
        assert row["count"] == 4


def test_column():
    _setup_data()

    rows = TestRelationship.where(TestRelationship.value > 30).column(TestRelationship.value)

    assert len(rows) == 4
    assert set(rows) == {33}

    assert TestRelationship.column(TestRelationship.value, distinct=True, orderby=~TestRelationship.value) == [33, 3]


def test_collect_with_extra_fields():
    _setup_data()
    builder = TestRelationship.select(TestRelationship.id, TestRelationship.name, TestRelationship.querytable.count())

    assert builder.execute()

    row = builder.first_or_fail()

    assert row.id
    assert row.name
    assert row._extra
    assert row[TestRelationship.querytable.count()]
