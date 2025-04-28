from timeit import timeit

from pydal import DAL, Field

from src.typedal import TypeDAL, TypedField, TypedTable
from src.typedal.fields import TextField

pydal = DAL("sqlite:memory")
typedal = TypeDAL("sqlite:memory")


class TimeTable(TypedTable):
    string_field: TypedField[str]
    other: int
    text_field = TextField(default="Something")


def defines_pydal():
    if "time_table" in pydal.tables:
        pydal.time_table.drop()

    pydal.define_table(
        "time_table",
        Field("string_field"),
        Field("other", "integer"),
        Field("text_field", "text", default="Something"),
    )


def defines_typedal():
    if "time_table" in typedal.tables:
        TimeTable.drop()

    typedal.define(TimeTable)


def inserts_pydal():
    pydal.time_table.insert(
        string_field="This is a string field",
        other=33,
        text_field="This is a text field. This is a text field. This is a text field. This is a text field. ",
    )


def inserts_typedal():
    TimeTable.insert(
        string_field="This is a string field",
        other=33,
        text_field="This is a text field. This is a text field. This is a text field. This is a text field. ",
    )


def updates_pydal():
    pydal((pydal.time_table.id > 1) & (pydal.time_table.id < 3)).update(
        string_field="This is a string field!",
        other=34,
        text_field="This is a text field. This is a text field. "
        "This is a text field. This is a text field. This is a text field. ",
    )

    row = pydal.time_table(1)
    row.update_record(
        string_field="This is a string field?", other=35, text_field="This is a text field. This is a text field."
    )


def updates_typedal():
    TimeTable.where(TimeTable.id > 1).where(TimeTable.id < 3).update(
        string_field="This is a string field!",
        other=34,
        text_field="This is a text field. This is a text field. "
        "This is a text field. This is a text field. This is a text field. ",
    )

    TimeTable(1).update_record(
        string_field="This is a string field!",
        other=34,
        text_field="This is a text field. This is a text field. "
        "This is a text field. This is a text field. This is a text field. ",
    )


def selects_pydal():
    assert pydal(pydal.time_table.id == 1).count()
    assert pydal.time_table(1)
    assert pydal.time_table(id=1)
    assert pydal(pydal.time_table.id == 1).select().first()


def selects_typedal():
    assert TimeTable.count()
    assert TimeTable.exists()
    assert TimeTable(1)
    assert TimeTable(id=1)
    assert TimeTable.where(TimeTable.id == 1).first()


def deletes_pydal():
    first = pydal(pydal.time_table.id > 0).select("id", limitby=(0, 1)).first()
    pydal.time_table(first.id).delete_record()
    first = pydal(pydal.time_table.id > 0).select("id", limitby=(0, 1)).first()
    pydal(pydal.time_table.id == first.id).delete()


def deletes_typedal():
    TimeTable.first().delete_record()
    first = TimeTable.select(TimeTable.id).first()
    TimeTable.where(TimeTable.id == first.id).delete()


###


def compare(name1, name2, func1, func2, times=1000):
    result1 = timeit(func1, number=times)
    result2 = timeit(func2, number=times)

    fastest = name2 if result1 > result2 else name1
    slowest = name1 if result1 > result2 else name2

    diff = abs(result1 - result2)

    print(name1, result1, sep=": ")
    print(name2, result2, sep=": ")
    _diff = round(diff * 1000 * 10000) / 10000
    print(f"{fastest} beats {slowest} with {_diff}ms")
    print()


def time_defines():
    print("define")
    compare("pydal", "typedal", defines_pydal, defines_typedal)


def time_inserts():
    print("insert")
    compare("pydal", "typedal", inserts_pydal, inserts_typedal)


def time_updates():
    print("update")
    compare("pydal", "typedal", updates_pydal, updates_typedal)


def time_selects():
    print("select")
    compare("pydal", "typedal", selects_pydal, selects_typedal)


def time_deletes():
    print("delete")
    compare("pydal", "typedal", deletes_pydal, deletes_typedal, times=500)


if __name__ == "__main__":
    time_defines()
    time_inserts()
    time_updates()
    time_selects()
    time_deletes()
