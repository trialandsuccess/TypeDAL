"""
Test (Typed)Table public API.
"""
import io
import json
from textwrap import dedent

import pytest
from pydal.objects import Expression

from src.typedal import TypeDAL, TypedTable, TypedField
from src.typedal.fields import IntegerField
import pydal

db = TypeDAL("sqlite:memory")


def test_both_styles_for_class():
    old_style = db.define_table("old_style",
                                pydal.Field("string_field"),
                                pydal.Field("int_field", "integer"),
                                )

    @db.define()
    class NewStyle(TypedTable):
        string_field: TypedField[str]
        int_field = IntegerField()

    assert old_style.as_dict()
    assert NewStyle.as_dict()

    assert NewStyle.as_dict().keys() == old_style.as_dict().keys()

    assert json.loads(NewStyle.as_json()).keys() == json.loads(old_style.as_json()).keys()

    # assert old_style.as_xml()
    # assert NewStyle.as_xml()

    assert old_style.as_yaml()
    assert NewStyle.as_yaml()

    bulk_data = [
        {'string_field': "String 1",
         'int_field': 1},
        {'string_field': "String 2",
         'int_field': 2},
    ]
    ids = old_style.bulk_insert(bulk_data)
    assert ids == [_.id for _ in NewStyle.bulk_insert(bulk_data)]

    assert old_style.create_index("first-index-oldstyle", "int_field")
    assert NewStyle.create_index("first-index-newstyle", "int_field")

    assert old_style.drop_index("first-index-oldstyle")
    assert NewStyle.drop_index("first-index-newstyle")

    assert old_style.drop_index("first-index-oldstyle", if_exists=True)
    assert NewStyle.drop_index("first-index-newstyle", if_exists=True)

    assert old_style.fields[1] == "string_field"
    assert NewStyle.fields[1] == "string_field"

    demo_csv = dedent("""\
    string_field,int_field
    field 3,3
    field 4,4
    field n,-1\
    """)

    old_style.import_from_csv_file(io.StringIO(demo_csv), validate=True)
    NewStyle.import_from_csv_file(io.StringIO(demo_csv), validate=True)

    assert db(old_style).count() == 5
    assert NewStyle.count() == 5

    assert old_style.insert(string_field="field 6", int_field=6) == 6
    instance = NewStyle.insert(string_field="field 6", int_field=6)
    assert instance.id == 6

    with pytest.raises(RuntimeError):
        old_style.update(old_style.int_field == 6, int_field=7)

    assert NewStyle.update(
        NewStyle.int_field == 6000,
        int_field=7
    ) is None

    same_as_before = NewStyle.update(
        NewStyle.int_field == 6,
        int_field=7
    ).update_record(
        int_field=6
    )

    assert same_as_before.int_field == 6

    assert isinstance(old_style.on(old_style.id == 1), Expression)
    assert isinstance(NewStyle.on(NewStyle.id == 1), Expression)

    assert old_style.query_name()[0] == '"old_style"' == old_style.sql_fullref
    assert NewStyle.query_name()[0] == '"new_style"' == NewStyle.sql_fullref

    assert not old_style.update_or_insert(  # update yields None
        old_style.id == 5,
        string_field="field 5",
        int_field=5

    )
    instance = NewStyle.update_or_insert(
        old_style.id == 5,
        string_field="field 5",
        int_field=5
    )

    assert instance.int_field == 5

    assert old_style.update_or_insert(  # insert yields id
        old_style.id == 7,
        string_field="field 7",
        int_field=7

    )
    instance = NewStyle.update_or_insert(
        old_style.id == 7,
        string_field="field 7",
        int_field=7
    )

    assert instance.id == 7
    assert instance.int_field == 7

    # query with dict:
    assert old_style.update_or_insert(dict(string_field="field 8", int_field=8), string_field="field 8", int_field=8)
    assert NewStyle.update_or_insert(dict(string_field="field 8", int_field=8), string_field="field 8", int_field=8)

    # without query:
    assert old_style.update_or_insert(string_field="field 9", int_field=9)
    assert NewStyle.update_or_insert(string_field="field 9", int_field=9)

    old_style.truncate()
    NewStyle.truncate()

    assert db(old_style).count() == 0
    assert NewStyle.count() == 0

    with pytest.raises(Exception):
        old_style.insert(string_field=123, int_field="abc")

    with pytest.raises(Exception):
        NewStyle.insert(string_field=123, int_field="abc")

    assert old_style.validate_and_insert(string_field=123, int_field="abc")["errors"]
    instance, errors = NewStyle.validate_and_insert(string_field=123, int_field="abc")
    assert not instance
    assert errors

    assert old_style.validate_and_insert(string_field="123", int_field=123)["id"]
    instance, errors = NewStyle.validate_and_insert(string_field="123", int_field=123)
    assert instance
    assert not errors

    assert old_style.validate_and_update(old_style.id == 1, string_field=123, int_field="abc")["errors"]
    assert old_style.validate_and_update(old_style.id == 1, string_field="123", int_field=123)["id"]

    instance, errors = NewStyle.validate_and_update(NewStyle.id == 1, string_field=123, int_field="abc")
    assert not instance
    assert errors

    instance, errors = NewStyle.validate_and_update(NewStyle.id == 1, string_field="123", int_field=123)
    assert instance
    assert not errors

    instance, errors = NewStyle.validate_and_update(NewStyle.id == 99, string_field="123", int_field=123)
    assert not instance
    assert errors

    assert old_style.validate_and_update_or_insert(old_style.id == 1, string_field=123, int_field="abc")["errors"]
    assert old_style.validate_and_update_or_insert(old_style.id == 101, string_field=123, int_field="abc")["errors"]

    assert old_style.validate_and_update_or_insert(old_style.id == 1, string_field="123", int_field=123)["id"]
    assert old_style.validate_and_update_or_insert(old_style.id == 101, string_field="123", int_field=123)["id"]

    instance, errors = NewStyle.validate_and_update_or_insert(old_style.id == 1, string_field=123, int_field="abc")

    assert not instance
    assert errors

    instance, errors = NewStyle.validate_and_update_or_insert(old_style.id == 99, string_field=123, int_field="abc")

    assert not instance
    assert errors

    instance, errors = NewStyle.validate_and_update_or_insert(old_style.id == 1, string_field="123", int_field=123)
    assert instance
    assert not errors

    instance, errors = NewStyle.validate_and_update_or_insert(old_style.id == 101, string_field="123", int_field=123)

    assert instance
    assert not errors

    assert len(db(old_style).select()) == 2
    assert len(NewStyle.all()) == 2

    assert isinstance(old_style.with_alias("aliased_old_style"), pydal.objects.Table)
    assert isinstance(NewStyle.with_alias("aliased_old_style"), pydal.objects.Table)

    old_style.drop()
    NewStyle.drop()

    with pytest.raises(Exception):
        old_style.drop()

    with pytest.raises(Exception):
        NewStyle.drop()