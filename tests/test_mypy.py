import typing

import pydal.objects
import pytest

from typedal import (  # fixme: why does src.typedal not work anymore?
    TypeDAL,
    TypedField,
    TypedRows,
    TypedTable,
)

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
    typing.reveal_type(MyTable())  # R: tests.test_mypy.MyTable
    typing.reveal_type(OtherTable())  # R: tests.test_mypy.OtherTable
    typing.reveal_type(LaterDefine())  # R:  tests.test_mypy.LaterDefine

    db.define(LaterDefine)
    typing.reveal_type(LaterDefine())  # R: tests.test_mypy.LaterDefine

    typing.reveal_type(MyTable.normal)  # R: builtins.str
    typing.reveal_type(MyTable().normal)  # R: builtins.str
    typing.reveal_type(MyTable.fancy)  # R: typedal.core.TypedField[builtins.str]
    typing.reveal_type(MyTable().fancy)  # R: builtins.str
    typing.reveal_type(MyTable.options)  # R: typedal.core.TypedField[builtins.str]
    typing.reveal_type(MyTable().options)  # R: builtins.str


@pytest.mark.mypy_testing
def test_update() -> None:
    query: pydal.objects.Query = MyTable.id == 3
    new = MyTable.update(query)
    typing.reveal_type(new)  # R: Union[tests.test_mypy.MyTable, None]

    inst = MyTable(3)  # could also actually be None!
    typing.reveal_type(inst)  # R: tests.test_mypy.MyTable

    if inst:
        inst2 = inst._update()  # normally you would just do .update
        typing.reveal_type(inst2)  # R: tests.test_mypy.MyTable

        inst3 = inst.update_record()
        typing.reveal_type(inst3)  # R: tests.test_mypy.MyTable


@pytest.mark.mypy_testing
def mypy_test_typedset() -> None:
    counted1 = db(MyTable).count()
    counted2 = db(db.old_style).count()
    counted3 = db(old_style).count()
    counted4 = MyTable.count()

    typing.reveal_type(counted1)  # R: builtins.int
    typing.reveal_type(counted2)  # R: builtins.int
    typing.reveal_type(counted3)  # R: builtins.int
    typing.reveal_type(counted4)  # R: builtins.int

    select1 = db(MyTable).select()  # E: Need type annotation for "select1"
    select2: TypedRows[MyTable] = db(MyTable).select()
    select3 = MyTable.select().collect()

    typing.reveal_type(select1)  # R: typedal.core.TypedRows[Any]
    typing.reveal_type(select2)  # R: typedal.core.TypedRows[tests.test_mypy.MyTable]
    typing.reveal_type(select3)  # R: typedal.core.TypedRows[tests.test_mypy.MyTable]

    typing.reveal_type(select1.first())  # R: Union[Any, None]
    typing.reveal_type(select2.first())  # R: Union[tests.test_mypy.MyTable, None]
    typing.reveal_type(select3.first())  # R: Union[tests.test_mypy.MyTable, None]

    for row in select2:
        typing.reveal_type(row)  # R: tests.test_mypy.MyTable

    for row in MyTable.select():
        typing.reveal_type(row)  # R: tests.test_mypy.MyTable


@pytest.mark.mypy_testing
def mypy_test_query() -> None:
    db(MyTable.id > 0)

    db(db.old_style.id > 3)

    db(MyTable)

    db(db.old_style)

    my_query = MyTable.id > 3

    typing.reveal_type(my_query)  # R: typedal.types.Query

    MyTable.update_or_insert(MyTable)
    MyTable.update_or_insert(my_query)
    MyTable.update_or_insert(db.my_table.id > 3)
