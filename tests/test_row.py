"""
Test (Typed)Row public API.
"""
import json

import pydal
import pytest

from src.typedal import TypedTable, TypedField, TypeDAL
from src.typedal.fields import IntegerField

db = TypeDAL("sqlite:memory")


def test_both_styles_for_instance():
    old_style_class = db.define_table("old_style",
                                      pydal.Field("string_field"),
                                      pydal.Field("int_field", "integer"),
                                      )

    @db.define()
    class NewStyleClass(TypedTable):
        string_field: TypedField[str]
        int_field = IntegerField()

    old_style_class.insert(string_field="one", int_field=1)

    old_style = old_style_class(1)

    new_style = NewStyleClass.insert(string_field="one", int_field=1)

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
