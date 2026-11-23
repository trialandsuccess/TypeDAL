import uuid

import pytest

from src.typedal import TypeDAL, TypedTable
from src.typedal.fields import ArrayField, NativeArrayField, NativeArrayType


class ArrayThing(TypedTable):
    tags = ArrayField(str)
    nums = ArrayField(int)
    ids = ArrayField(uuid.UUID)
    maybe_tags = ArrayField(str, notnull=False, default=None)


def test_decode():
    assert NativeArrayType(str).decode(None) == []
    assert NativeArrayType(int).decode([1, None, 2]) == [1, 2]
    gid = uuid.uuid4()
    assert NativeArrayType(uuid.UUID).decode(f"{{{gid},{gid}}}") == [gid, gid]
    assert NativeArrayType(uuid.UUID).decode("{}") == []

    with pytest.raises(TypeError):
        NativeArrayType(str).encode("not a list")


def test_unsupported_element_type():
    with pytest.raises(TypeError):
        ArrayField(float)


def test_array_fields_psql(dal_psql: TypeDAL):
    db = dal_psql
    db.define(ArrayThing)

    sql = ArrayThing._sql()
    assert '"tags" text[] NOT NULL' in sql
    assert '"nums" bigint[] NOT NULL' in sql
    assert '"ids" uuid[] NOT NULL' in sql

    assert isinstance(db.array_thing.tags, NativeArrayField)
    assert ArrayThing.tags.type.startswith("list:")

    gid = uuid.uuid4()
    tricky = ["a,b", 'q"uote', "it's", "back\\slash", "{brace}", " sp ", "NULL"]
    row = ArrayThing.insert(tags=[*tricky, "", "  ", None], nums=[1, 2, 3], ids=[gid])
    empty = ArrayThing.insert()

    row = ArrayThing(row.id)
    assert row.tags == tricky
    assert row.nums == [1, 2, 3]
    assert row.ids == [gid]
    assert row.maybe_tags == []

    empty = ArrayThing(empty.id)
    assert empty.tags == [] and empty.nums == [] and empty.ids == []

    assert ArrayThing.where(ArrayThing.tags.contains("a,b")).count() == 1
    assert ArrayThing.where(ArrayThing.tags.contains("a")).count() == 0
    assert ArrayThing.where(ArrayThing.nums.contains(2)).count() == 1
    assert ArrayThing.where(ArrayThing.ids.contains(gid)).count() == 1
    assert ArrayThing.where(ArrayThing.nums.overlaps([3, 99])).count() == 1
    assert ArrayThing.where(ArrayThing.nums.contains([3, 99])).count() == 1
    assert ArrayThing.where(ArrayThing.nums.contains([3, 99], all=True)).count() == 0
    assert ArrayThing.where(ArrayThing.nums.contains_all([1, 3])).count() == 1
    assert ArrayThing.where(ArrayThing.ids.overlaps([gid])).count() == 1

    # contains is case-insensitive by default (like pydal), and never treats % or _ as wildcards
    assert ArrayThing.where(ArrayThing.tags.contains("A,B")).count() == 1
    assert ArrayThing.where(ArrayThing.tags.contains("A,B", case_sensitive=True)).count() == 0
    assert ArrayThing.where(ArrayThing.tags.contains("a%")).count() == 0
    assert ArrayThing.where(ArrayThing.tags.contains("IT'S")).count() == 1
    assert ArrayThing.where(ArrayThing.tags.contains("{BRACE}")).count() == 1
    # the prefilter joins elements with chr(1); a match spanning two elements must not count
    assert ArrayThing.where(ArrayThing.tags.contains('a,b\x01q"uote')).count() == 0

    assert ArrayThing.where(ArrayThing.tags.like("%brace%")).count() == 1
    assert ArrayThing.where(ArrayThing.tags.ilike("%BRACE%")).count() == 1
    assert ArrayThing.where(ArrayThing.nums.len() == 3).count() == 1
    assert ArrayThing.where(ArrayThing.nums.len() == 0).count() == 1

    ArrayThing.where(id=row.id).update(tags=["new"])
    assert ArrayThing(row.id).tags == ["new"]

    with pytest.raises(Exception):
        ArrayThing.insert(ids=["not-a-uuid"])
    db.rollback()
