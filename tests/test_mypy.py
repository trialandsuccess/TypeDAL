import pytest
import typing

from src.typedal import TypeDAL, TypedTable, TypedRows

db = TypeDAL("sqlite:memory")


@db.define
class MyTable(TypedTable):
    ...


old_style = db.define_table("old_table")


@pytest.mark.mypy_testing
def mypy_test_typedset() -> None:
    counted1 = db(MyTable).count()
    counted2 = db(db.old_style).count()
    counted3 = db(old_style).count()

    typing.reveal_type(counted1)  # R: builtins.int
    typing.reveal_type(counted2)  # R: builtins.int
    typing.reveal_type(counted3)  # R: builtins.int

    select1 = db(MyTable).select()  # E: Need type annotation for "select1"
    select2: TypedRows[MyTable] = db(MyTable).select()

    typing.reveal_type(select1)  # R: src.typedal.core.TypedRows[Any]
    typing.reveal_type(select2)  # R: src.typedal.core.TypedRows[tests.test_mypy.MyTable]

    for row in select2:
        typing.reveal_type(row)  # R: tests.test_mypy.MyTable
