import typing

from src.typedal.helpers import is_union, all_annotations, instanciate, origin_is_subclass


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
    assert all_annotations(Child) == {
        "a": int,
        "b": str,
        "c": float,
        "d": bool
    }


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
