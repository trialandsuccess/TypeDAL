"""
Test (Typed)Row(s) public APIs.
"""

import io
import json

import pydal
import pytest
from pydal.objects import Rows

from src.typedal import TypeDAL, TypedField, TypedTable
from src.typedal.fields import IntegerField, ReferenceField

db = TypeDAL("sqlite:memory")

db.define_table("to_reference", pydal.Field("hello_there"))

old_style_class = db.define_table(
    "old_style",
    pydal.Field("string_field"),
    pydal.Field("int_field", "integer"),
    pydal.Field("to_ref", "reference to_reference", notnull=False),
)


@db.define()
class NewStyleClass(TypedTable):
    string_field: TypedField[str]
    int_field = IntegerField()
    to_ref = ReferenceField(db.to_reference, notnull=False)


def test_both_styles_for_instance():
    old_style_class.insert(string_field="one", int_field=1)
    old_style_class.insert(string_field="extra", int_field=-99)

    old_style = old_style_class(1)

    new_style = NewStyleClass.insert(string_field="one", int_field=1)
    NewStyleClass.insert(string_field="extra", int_field=-99)

    assert new_style.id == old_style.id
    assert new_style.string_field == old_style.string_field
    assert new_style.int_field == old_style.int_field

    assert isinstance(new_style, NewStyleClass)
    assert not isinstance(new_style, (pydal.objects.Table, pydal.objects.Row))

    assert old_style.as_dict() == new_style.as_dict()

    assert json.loads(old_style.as_json()) == json.loads(new_style.as_json())

    old_style.update(string_field="two")  # only update in memory
    assert old_style.string_field == "two"

    old_style = old_style_class(1)
    assert old_style.string_field == "one"

    old_style.update_record(string_field="two")  # also update in db
    assert old_style.string_field == "two"

    old_style = old_style_class(1)
    assert old_style.string_field == "two"

    old_style.string_field = "three"
    new = old_style.update_record()

    old_style = old_style_class(1)
    assert new.string_field == old_style.string_field == "three"

    updated = new_style.update(string_field="two")  # only update in memory
    assert new_style.string_field == "two" == updated.string_field

    new_style = NewStyleClass(1)
    assert new_style.string_field == "one"

    updated = new_style.update_record(string_field="two")  # also update in db
    assert new_style.string_field == "two" == updated.string_field

    new_style = NewStyleClass(1)
    assert new_style.string_field == "two"

    new_style.string_field = "three"
    new = new_style.update_record()

    new_style = NewStyleClass(1)
    assert new.string_field == new_style.string_field == "three"

    assert old_style.delete_record()
    assert new_style.delete_record()
    assert not new_style

    # getting a non-shadowed property:
    old_style = db(old_style_class).select().first()
    new_style = NewStyleClass.select().first()

    assert old_style.keys()
    assert new_style.keys()

    old_style.clear()
    new_style.clear()

    with pytest.raises(AttributeError):
        old_style.fake()
    with pytest.raises(AttributeError):
        new_style.fake()


def test_rows():
    old_style_class.truncate()
    NewStyleClass.truncate()

    to_ref = db.to_reference.insert(hello_there="Hello There")

    old_style_class.insert(string_field="one", int_field=1, to_ref=to_ref)
    old_style_class.insert(string_field="two", int_field=2, to_ref=to_ref)
    old_style_class.insert(string_field="three", int_field=3, to_ref=to_ref)
    old_style_class.insert(string_field="3.5", int_field=3, to_ref=to_ref)

    NewStyleClass.insert(string_field="one", int_field=1, to_ref=to_ref)
    NewStyleClass.insert(string_field="two", int_field=2, to_ref=to_ref)
    NewStyleClass.insert(string_field="three", int_field=3, to_ref=to_ref)
    NewStyleClass.insert(string_field="3.5", int_field=3, to_ref=to_ref)

    old_rows: Rows = db(old_style_class).select()
    new_rows = NewStyleClass.all()

    assert str(new_rows) == "<TypedRows with 4 records>"

    assert 0 not in new_rows
    assert 4 in new_rows
    assert 5 not in new_rows

    assert old_rows.as_csv() == new_rows.as_csv().replace("new_style_class", "old_style")
    assert old_rows.as_dict()[1]["string_field"] == new_rows.as_dict()[1]["string_field"]

    assert new_rows.as_dict(storage_to_dict=True)

    assert old_rows.as_json() == new_rows.as_json() == new_rows.json() == old_rows.json()

    assert old_rows.as_list()[0]["string_field"] == new_rows.as_list()[0]["string_field"]

    assert new_rows.as_list(storage_to_dict=True)

    assert old_rows.colnames == [_.replace("new_style_class", "old_style") for _ in new_rows.colnames]
    assert old_rows.colnames_fields == new_rows.colnames_fields
    assert old_rows.column("string_field") == new_rows.column("string_field")
    assert old_rows.db == new_rows.db

    old_filtered = old_rows.exclude(lambda row: row.int_field == 2)
    new_filtered = new_rows.exclude(lambda row: row.int_field == 2).as_dict()

    assert str(new_rows) == "<TypedRows with 3 records>"

    assert len(old_filtered) == len(new_filtered) == 1

    assert old_filtered[0].string_field == new_filtered[2]["string_field"]
    assert old_rows.as_dict()[1]["string_field"] == new_rows[1].string_field

    old_io = io.StringIO()
    new_io = io.StringIO()

    old_rows.export_to_csv_file(old_io)
    new_rows.export_to_csv_file(new_io)
    old_io.seek(0)
    new_io.seek(0)
    assert old_io.read() == new_io.read().replace("new_style_class", "old_style")

    assert len(old_rows.fields) == len(new_rows.fields) > 0

    assert (
        old_rows.find(lambda row: row.int_field < 3).first().string_field
        == new_rows.find(lambda row: row.int_field < 3).first().string_field
    )

    assert len(new_rows.find(lambda row: row.int_field > 0)) == 3
    assert len(new_rows.find(lambda row: row.int_field > 0, limitby=(0, 1))) == 1

    assert (
        len(old_rows.group_by_value("int_field"))
        == len(new_rows.group_by_value("int_field"))
        == len(new_rows.group_by_value(NewStyleClass.int_field))
    )

    joined_old = old_rows.join(db.to_reference.id).first()

    joined_new = new_rows.join(db.to_reference.id).first()

    assert joined_old.to_ref.hello_there == joined_new.to_ref.hello_there == "Hello There"

    assert "---" in repr(new_rows)
    assert "string_field" in repr(new_rows)

    assert old_rows.last().string_field == new_rows.last().string_field == "3.5"

    assert old_rows.response == new_rows.response

    copied_old = old_rows.sort(lambda row: row.string_field)
    copied_new = new_rows.sort(lambda row: row.string_field)

    assert copied_old[0].id == 4
    assert copied_new[0].id == 4

    with pytest.raises(KeyError):
        assert not new_rows[-1]

    assert new_rows.get(-1) is None

    empty = NewStyleClass.where(lambda row: row.id == -1).collect()

    assert empty.first() is empty.last() is None

    assert len(empty) == 0
    assert len(empty.exclude(lambda x: x)) == 0
    assert len(empty.find(lambda x: x)) == 0

    empty_rows = NewStyleClass.where(NewStyleClass.id < 0).collect()
    assert str(empty_rows)
    assert repr(empty_rows)
