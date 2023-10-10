"""
Test (Typed)Row(s) public API.

- fields
- find
- first
- group_by_value
- insert
- join
- json
- last
- records
- render
- response
- setvirtualfields
- sort
- xml

"""
import io
import json

import pydal
import pytest
from pydal.objects import Rows

from src.typedal import TypedTable, TypedField, TypeDAL
from src.typedal.fields import IntegerField

db = TypeDAL("sqlite:memory")

old_style_class = db.define_table("old_style",
                                  pydal.Field("string_field"),
                                  pydal.Field("int_field", "integer"),
                                  )


@db.define()
class NewStyleClass(TypedTable):
    string_field: TypedField[str]
    int_field = IntegerField()


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

    old_style_class.insert(string_field="one", int_field=1)
    old_style_class.insert(string_field="two", int_field=2)

    NewStyleClass.insert(string_field="one", int_field=1)
    NewStyleClass.insert(string_field="two", int_field=2)

    old_rows: Rows = db(old_style_class).select()
    new_rows = NewStyleClass.all()

    assert old_rows.as_csv() == new_rows.as_csv().replace("new_style_class", "old_style")
    assert old_rows.as_dict()[1]['string_field'] == new_rows.as_dict()[1].string_field == new_rows.as_dict()[1]['string_field']
    assert old_rows.as_json() == new_rows.as_json()
    assert old_rows.as_list()[0]['string_field'] == new_rows.as_list()[0].string_field
    assert old_rows.colnames == [_.replace("new_style_class", "old_style") for _ in new_rows.colnames]
    assert old_rows.colnames_fields == new_rows.colnames_fields
    assert old_rows.column("string_field") == new_rows.column("string_field")
    assert old_rows.db == new_rows.db

    old_filtered = old_rows.exclude(lambda row: row.int_field == 2)
    new_filtered= new_rows.exclude(lambda row: row.string_field == 2).as_dict()

    assert len(old_filtered) == len(new_filtered) == 1
    assert old_rows.as_dict()[1]['string_field'] == new_rows[1].string_field

    old_io = io.StringIO()
    new_io = io.StringIO()

    old_rows.export_to_csv_file(old_io)
    new_rows.export_to_csv_file(new_io)
    old_io.seek(0)
    new_io.seek(0)
    assert old_io.read() == new_io.read().replace("new_style_class", "old_style")
