import typing
from datetime import datetime, timedelta

import pydal
import pytest
from pydal import DAL

from src.typedal.caching import get_expire
from src.typedal.helpers import (
    DummyQuery,
    all_annotations,
    as_lambda,
    extract_type_optional,
    get_db,
    get_field,
    get_table,
    instanciate,
    is_union,
    looks_like,
    match_strings,
    mktable,
    origin_is_subclass,
    to_snake,
    unwrap_type,
)
from typedal import TypeDAL, TypedTable
from typedal.types import Field


def test_is_union():
    assert is_union(int | str)
    assert is_union(typing.Union[int, str])
    assert is_union(int | None)
    assert is_union(typing.Union[int, None])
    assert is_union(typing.Optional[str])
    assert not is_union(int)
    assert not is_union(list[str])
    assert not is_union(list[str | int])


class Base:
    a: int
    b: str


class Child(Base):
    c: float
    d: bool


def test_all_annotations():
    assert all_annotations(Child) == {"a": int, "b": str, "c": float, "d": bool}


def test_instanciate():
    assert instanciate(list) == []
    assert instanciate([1, 2]) == [1, 2]
    assert isinstance(instanciate(Child), Child)
    assert instanciate(list[str]) == []
    assert instanciate(dict[str, float]) == {}


class MyList(list): ...


def test_origin_is_subclass():
    assert origin_is_subclass(MyList[str], list)
    # not a generic:
    assert not origin_is_subclass(MyList, list)
    # not subclass of dict:
    assert not origin_is_subclass(MyList[str], dict)
    assert not origin_is_subclass(MyList, dict)


def test_mktable():
    data = {
        "1": {"id": 1, "name": "Alice", "Age": 25, "Occupation": "Software Engineer"},
        "2": {"id": 2, "name": "Bob", "Age": 30, "Occupation": "Doctor"},
        "3": {"id": 3, "name": "Carol", "Age": 35, "Occupation": "Lawyer"},
    }

    assert mktable(data, skip_first=False)
    assert mktable(data, header=["id", "name", "age", "occupation"])


def test_unwrap():
    my_type = typing.Optional[list[list[str]]]

    assert unwrap_type(my_type) == str


def test_looks_like():
    assert looks_like([], list)
    assert looks_like(list, list)
    assert looks_like(list[str], list)

    assert not looks_like([], str)
    assert not looks_like(list, str)
    assert not looks_like(list[str], str)


def test_extract():
    assert extract_type_optional(typing.Optional[bool]) == (bool, True)
    assert extract_type_optional(bool | None) == (bool, True)
    assert extract_type_optional(None | bool) == (bool, True)
    assert extract_type_optional(bool) == (bool, False)
    assert extract_type_optional(None) == (None, False)


def test_to_snake():
    assert to_snake("MyClass") == "my_class"
    assert to_snake("myClass") == "my_class"
    assert to_snake("myclass") == "myclass"
    assert to_snake("my_class") == "my_class"
    assert to_snake("my_Class") == "my__class"


def test_dummy_query():
    dummy = DummyQuery()

    assert dummy & "nothing" == "nothing"
    assert dummy & 123 == 123
    assert dummy | "nothing" == "nothing"
    assert dummy | 123 == 123

    assert not dummy


def test_as_lambda():
    o = {}
    call = as_lambda(o)

    assert call() is o
    assert call(1, "two", {}) is o
    o["new"] = "value"

    assert call(one=1, two="two", three={}) is o

    assert call()["new"] == "value"


def test_get_expire():
    now = datetime(year=2023, hour=12, minute=1, second=1, month=1, day=1)

    assert get_expire() is None
    assert get_expire(ttl=2, now=now) == datetime(year=2023, hour=12, minute=1, second=3, month=1, day=1)
    assert get_expire(ttl=timedelta(seconds=2), now=now) == datetime(
        year=2023, hour=12, minute=1, second=3, month=1, day=1
    )

    assert get_expire(now) == now

    with pytest.raises(ValueError):
        get_expire(expires_at=now, ttl=3)


def test_match_strings():
    # Test single pattern
    patterns = "*.txt"
    string_list = ["file1.txt", "file2.jpg", "file3.txt", "file4.png"]
    expected_matches = ["file1.txt", "file3.txt"]
    assert sorted(match_strings(patterns, string_list)) == sorted(expected_matches)

    # Test multiple patterns
    patterns = ["*.txt", "*.jpg"]
    expected_matches = ["file1.txt", "file2.jpg", "file3.txt"]
    assert sorted(match_strings(patterns, string_list)) == sorted(expected_matches)

    # Test no matches
    patterns = "*.doc"
    expected_matches = []
    assert sorted(match_strings(patterns, string_list)) == sorted(expected_matches)

    # Test empty list
    patterns = "*.txt"
    string_list = []
    expected_matches = []
    assert sorted(match_strings(patterns, string_list)) == sorted(expected_matches)

    # Test empty patterns
    patterns = []
    string_list = ["file1.txt", "file2.jpg", "file3.txt", "file4.png"]
    expected_matches = []
    assert sorted(match_strings(patterns, string_list)) == sorted(expected_matches)


database = TypeDAL("sqlite:memory")
assert database._db_uid


@database.define()
class TestGetFunctions(TypedTable):
    string: str


def test_get_functions():
    db = get_db(TestGetFunctions)
    assert isinstance(db, DAL)
    assert db._db_uid == database._db_uid
    db = get_db(db.test_get_functions)
    assert db._db_uid == database._db_uid
    table = get_table(TestGetFunctions)
    assert hasattr(table, "string")
    assert issubclass(TestGetFunctions, TypedTable)
    assert isinstance(table, pydal.objects.Table)
    assert not isinstance(table, TypedTable)
    field = get_field(TestGetFunctions.string)
    print(type(field))
    assert isinstance(field, Field)
