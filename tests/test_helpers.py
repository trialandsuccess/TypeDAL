import typing

from src.typedal.helpers import (
    all_annotations,
    instanciate,
    is_union,
    mktable,
    origin_is_subclass,
    unwrap_type, extract_type_optional,
)


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


class MyList(list):
    ...


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


def test_extract():
    assert extract_type_optional(typing.Optional[bool]) == (bool, True)
    assert extract_type_optional(bool | None) == (bool, True)
    assert extract_type_optional(None | bool) == (bool, True)
    assert extract_type_optional(bool) == (bool, False)
    assert extract_type_optional(None) == (None, False)
