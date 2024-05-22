import typing

from typing_extensions import reveal_type

import pydal.objects
import pytest

from typedal import (  # todo: why does src.typedal not work anymore?
    TypeDAL,
    TypedField,
    TypedRows,
    TypedTable,
)
from typedal.types import CacheFn, CacheTuple, Rows

db = TypeDAL("sqlite:memory")


@db.define
class MyTable(TypedTable):
    normal: str
    fancy: TypedField[str]
    options = TypedField(str)


@db.define()
class OtherTable(TypedTable):
    ...


class LaterDefine(TypedTable):
    ...


old_style = db.define_table("old_table")


@pytest.mark.mypy_testing
def mypy_test_typedal_define() -> None:
    reveal_type(MyTable())  # R: tests.test_mypy.MyTable
    reveal_type(OtherTable())  # R: tests.test_mypy.OtherTable
    reveal_type(LaterDefine())  # R:  tests.test_mypy.LaterDefine

    db.define(LaterDefine)
    reveal_type(LaterDefine())  # R: tests.test_mypy.LaterDefine

    reveal_type(MyTable.normal)  # R: builtins.str
    reveal_type(MyTable().normal)  # R: builtins.str
    reveal_type(MyTable.fancy)  # R: typedal.core.TypedField[builtins.str]
    reveal_type(MyTable().fancy)  # R: builtins.str
    reveal_type(MyTable.options)  # R: typedal.core.TypedField[builtins.str]
    reveal_type(MyTable().options)  # R: builtins.str

    reveal_type(MyTable.fancy.lower())  # R: typedal.types.Expression
    reveal_type(MyTable().fancy.lower())  # R: builtins.str


@pytest.mark.mypy_testing
def test_update() -> None:
    query: pydal.objects.Query = MyTable.id == 3
    new = MyTable.update(query)
    reveal_type(new)  # R: Union[tests.test_mypy.MyTable, None]

    inst = MyTable(3)  # could also actually be None!
    reveal_type(inst)  # R: tests.test_mypy.MyTable

    if inst:
        inst2 = inst._update()  # normally you would just do .update
        reveal_type(inst2)  # R: tests.test_mypy.MyTable

        inst3 = inst.update_record()
        reveal_type(inst3)  # R: tests.test_mypy.MyTable


@pytest.mark.mypy_testing
def mypy_test_typedset() -> None:
    counted1 = db(MyTable).count()
    counted2 = db(db.old_style).count()
    counted3 = db(old_style).count()
    counted4 = MyTable.count()

    reveal_type(counted1)  # R: builtins.int
    reveal_type(counted2)  # R: builtins.int
    reveal_type(counted3)  # R: builtins.int
    reveal_type(counted4)  # R: builtins.int

    select1 = db(MyTable).select()  # E: [var-annotated]
    select2: TypedRows[MyTable] = db(MyTable).select()
    select3 = MyTable.select().collect()

    reveal_type(select1)  # R: typedal.core.TypedRows[Any]
    reveal_type(select2)  # R: typedal.core.TypedRows[tests.test_mypy.MyTable]
    reveal_type(select3)  # R: typedal.core.TypedRows[tests.test_mypy.MyTable]

    reveal_type(select1.first())  # R: Union[Any, None]
    reveal_type(select2.first())  # R: Union[tests.test_mypy.MyTable, None]
    reveal_type(select3.first())  # R: Union[tests.test_mypy.MyTable, None]

    for row in select2:
        reveal_type(row)  # R: tests.test_mypy.MyTable

    for row in MyTable.select():
        reveal_type(row)  # R: tests.test_mypy.MyTable


@pytest.mark.mypy_testing
def mypy_test_query() -> None:
    db(MyTable.id > 0)

    db(db.old_style.id > 3)

    db(MyTable)

    db(db.old_style)

    my_query = MyTable.id > 3

    reveal_type(my_query)  # R: typedal.types.Query

    MyTable.update_or_insert(MyTable)
    MyTable.update_or_insert(my_query)
    MyTable.update_or_insert(db.my_table.id > 3)


@pytest.mark.mypy_testing
def mypy_test_cachefn() -> None:
    def cache_model(key: str, fn: CacheFn, expire: int) -> Rows:
        return fn()

    cache_valid: CacheTuple = (cache_model, 3000)

    def invalid_cache_model(key: str, fn: typing.Callable[..., list[str]], _: int = None) -> list[str]:
        return fn()

    cache_invalid: CacheTuple = (invalid_cache_model, 3000)  # E: [assignment]
