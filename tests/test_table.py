"""
Test (Typed)Table public API.
"""

import datetime as dt
import io
import json
from textwrap import dedent

import dateutil.parser
import pydal
import pytest
from pydal.objects import Expression

from src.typedal import TypeDAL, TypedField, TypedTable
from src.typedal.fields import IntegerField
from src.typedal.serializers import as_json

db = TypeDAL("sqlite:memory")


def test_both_styles_for_class():
    old_style = db.define_table(
        "old_style",
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

    assert as_json.encode(NewStyle) == NewStyle.as_json()

    # assert old_style.as_xml()
    # assert NewStyle.as_xml()

    assert old_style.as_yaml()
    assert NewStyle.as_yaml()

    bulk_data = [
        {"string_field": "String 1", "int_field": 1},
        {"string_field": "String 2", "int_field": 2},
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

    demo_csv = dedent(
        """\
    string_field,int_field
    field 3,3
    field 4,4
    field n,-1\
    """
    )

    old_style.import_from_csv_file(io.StringIO(demo_csv), validate=True)
    NewStyle.import_from_csv_file(io.StringIO(demo_csv), validate=True)

    assert db(old_style).count() == 5
    assert NewStyle.count() == 5

    assert old_style.insert(string_field="field 6", int_field=6) == 6
    instance = NewStyle.insert(string_field="field 6", int_field=6)
    assert instance.id == 6

    with pytest.raises(RuntimeError):
        old_style.update(old_style.int_field == 6, int_field=7)

    assert NewStyle.update(NewStyle.int_field == 6000, int_field=7) is None

    same_as_before = NewStyle.update(NewStyle.int_field == 6, int_field=7).update_record(int_field=6)

    assert same_as_before.int_field == 6

    assert isinstance(old_style.on(old_style.id == 1), Expression)
    assert isinstance(NewStyle.on(NewStyle.id == 1), Expression)

    assert old_style.query_name()[0] == '"old_style"' == old_style.sql_fullref
    assert NewStyle.query_name()[0] == '"new_style"' == NewStyle.sql_fullref

    assert not old_style.update_or_insert(old_style.id == 5, string_field="field 5", int_field=5)  # update yields None
    instance = NewStyle.update_or_insert(old_style.id == 5, string_field="field 5", int_field=5)

    assert instance.int_field == 5

    assert old_style.update_or_insert(old_style.id == 7, string_field="field 7", int_field=7)  # insert yields id
    instance = NewStyle.update_or_insert(old_style.id == 7, string_field="field 7", int_field=7)

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
    assert not NewStyle.exists()

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

    # errors - int field is a string
    instance, errors = NewStyle.validate_and_update(NewStyle.id == 1, string_field=123, int_field="abc")
    assert not instance
    assert errors

    # errors - required field is None
    instance, errors = NewStyle.validate_and_update(NewStyle.id == 1, string_field=None, int_field=None)
    assert not instance
    assert errors

    # success - types match
    instance, errors = NewStyle.validate_and_update(NewStyle.id == 1, string_field="123", int_field=123)
    assert instance
    assert not errors

    # no instance because id 99 doesn't exist, but also no errors because types match:
    instance, errors = NewStyle.validate_and_update(NewStyle.id == 99, string_field="123", int_field=123)
    assert not instance
    assert not errors

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


def test_table_with_methods():
    simulated_today = dateutil.parser.parse("2020-01-01 00:00").date()

    @db.define()
    class TableWithMethods(TypedTable):
        birthday: dt.date

        @property
        def today(self):
            return dt.date.today()

        @staticmethod
        def get_age(dob: dt.date, today: dt.date) -> int:
            if today.month < dob.month or (today.month == dob.month and today.day < dob.day):
                return today.year - dob.year - 1
            else:
                return today.year - dob.year

        @classmethod
        def get_tablename(cls):
            return str(cls._table)

        def age(self, today: dt.date = None):
            # https://stackoverflow.com/questions/765797/convert-timedelta-to-years
            today = today or self.today
            dob = self.birthday

            return self.get_age(dob, today)

        def _as_dict(self, **kwargs):
            # custom as_dict behavior! Used by as_json()
            return super()._as_dict() | {"age": self.age(simulated_today)}

    row = TableWithMethods.insert(birthday="2000-01-01")

    assert row.age(simulated_today) == 20

    assert TableWithMethods.get_tablename() == "table_with_methods"

    # test custom JSON dumping behavior:
    assert row.as_dict()["age"] == 20

    loaded = json.loads(row.as_json())
    assert loaded["birthday"]
    assert loaded["age"] == 20

    # and from a level up:
    dumped = TableWithMethods.all().as_json()

    loaded = json.loads(dumped)
    assert loaded[0]["age"] == 20
